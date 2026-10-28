"""Quantity-aware CTI3 measurement projections of already retained records.

This is a file-only boundary: no acquisition, integration, resampling or chart
state. Reference-only INPUT/EMISINPUT tables are consumer-specific measurement
files, not device-channel pairs for profiling. Argyll 3.5.0 txt2ti3.c:591-670
supplies the INPUT precedent; spec2cie.c:470-484 recognizes EMISINPUT. Its
spectral norm of 100 (:683-686) and output multiplier of 100 (:1051-1053)
cancel for this dialect. Emission spectra use mW/(m^2 sr nm), as documented in
ti3_format.html:438-454 and xsp2cie_sconvert (xspect.c:5957-6008). Established
W densities are multiplied by 1000, never brightness-normalized.

Imported chart OUTPUT pairs reuse validated retained device channels and the
selected actual reflective readings. chartread.c:3054-3119 defines COLOR_REP;
its resume join uses original SAMPLE_LOC (3304-3336). Each saved source has a
stable identity separate from its changing byte digest and its record owner.

Authoritative JSON/MAT remains the complete record. Unsupported components are
reported explicitly. Each owned companion is atomic; a set of files is not.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import stat
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path

from .cgats import REAL, read_cgats
from .chart_targets import validate_target_json
from .exports import _folder_lock, _publish_owned_file, _read_regular
from .json_values import unique_object
from .models import XYZ, Observation, Spectrum
from .operation_records import OperationResult, result_from_json, result_to_json


def _json(value) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )
    except RecursionError as error:
        raise ValueError("TI3 JSON nesting exceeds the supported depth") from error


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _source_identity(source_id: str) -> str:
    """Keep the local ownership path out of the exchange-file source header."""
    return _digest(source_id.encode("utf-8"))


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} fields differ from the TI3 contract")
    return value


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{label} must be {'possibly empty ' if empty else 'nonempty '}text")
    value.encode("utf-8")
    if not empty and "\x00" in value:
        raise ValueError(f"{label} contains NUL")
    return value


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a lowercase SHA-256 digest")
    return value


def _load(text):
    if not isinstance(text, str):
        raise ValueError("TI3 input must be JSON text")

    def invalid(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid)
    except RecursionError as error:
        raise ValueError("TI3 JSON nesting exceeds the supported depth") from error


def _plain(value):
    # JSON has already been decoded, so the context is acyclic. Walk without
    # imposing a second, call-stack-dependent limit on otherwise valid data.
    pending = [value]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key in value:
                _text(key, "context key")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            _text(value, "context text", empty=True)
        elif type(value) is float and not math.isfinite(value):
            raise ValueError("Context numbers must be finite")
        elif value is not None and type(value) not in (bool, int, float):
            raise ValueError("Context must be ordinary JSON data")


@dataclass(frozen=True)
class _Row:
    source: dict
    xyz: XYZ | None
    spectrum: Spectrum | None


def _row(value):
    _object(
        value, ("id", "name", "notes", "provenance", "kind", "xyz", "spectrum", "context"), "Record"
    )
    _text(value["id"], "record id")
    for name in ("name", "notes"):
        _text(value[name], name, empty=True)
    if value["provenance"] not in ("physical", "simulated", "replay"):
        raise ValueError("Unknown retained provenance")
    if value["kind"] not in ("color", "scalar", "timing", "configuration"):
        raise ValueError("Unknown retained record kind")
    context = _object(value["context"], ("route", "material", "provenance"), "Context")
    _text(context["route"], "context route")
    if not all(isinstance(context[key], dict) for key in ("material", "provenance")):
        raise ValueError("Context material and provenance must be objects")
    _plain(value["context"])
    if value["kind"] == "timing" and context["provenance"].get("presentationInvalidated") is True:
        _text(context["provenance"].get("presentationInvalidationReason"),
              "presentation invalidation reason")
    xyz = spectrum = None
    if value["xyz"] is not None:
        data = _object(
            value["xyz"],
            ("values", "unit", "scale", "observer", "illuminant", "origin", "method"),
            "XYZ",
        )
        if data["origin"] not in ("native", "derived"):
            raise ValueError("XYZ origin must distinguish native and derived values")
        _text(data["method"], "XYZ method")
        if not isinstance(data["values"], list):
            raise ValueError("XYZ values must be an array")
        xyz = XYZ(
            tuple(data["values"]), data["unit"], data["scale"], data["observer"], data["illuminant"]
        )
        for key in ("unit", "scale", "observer", "illuminant"):
            _text(data[key], key)
    if value["spectrum"] is not None:
        data = _object(
            value["spectrum"], ("wavelengths_nm", "values", "quantity", "unit"), "Spectrum"
        )
        if not all(isinstance(data[key], list) for key in ("wavelengths_nm", "values")):
            raise ValueError("Spectrum grid and values must be arrays")
        spectrum = Spectrum(
            tuple(data["wavelengths_nm"]), tuple(data["values"]), data["quantity"], data["unit"]
        )
        _text(data["quantity"], "spectrum quantity")
        _text(data["unit"], "spectrum unit")
    if value["kind"] != "color" and (xyz is not None or spectrum is not None):
        raise ValueError("Non-color records cannot carry borrowed color quantities")
    return _Row(value, xyz, spectrum)


def _snapshot(value):
    _object(
        value,
        ("schema_version", "owner_id", "source_id", "source_sha256", "records",
         "ordinary_record_ids", "charts"),
        "Snapshot",
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 3:
        raise ValueError("Unsupported TI3 export schema")
    _text(value["owner_id"], "owner id")
    _text(value["source_id"], "authoritative source id")
    _hash(value["source_sha256"])
    if not isinstance(value["records"], list) or not isinstance(value["charts"], list):
        raise ValueError("Records and charts must be arrays")
    rows = [_row(item) for item in value["records"]]
    ids = [row.source["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Retained record identities must be unique")
    ordinary_ids = value["ordinary_record_ids"]
    if not isinstance(ordinary_ids, list):
        raise ValueError("Ordinary record identities must be an array")
    for identity in ordinary_ids:
        _text(identity, "ordinary record id")
    if len(set(ordinary_ids)) != len(ordinary_ids):
        raise ValueError("Ordinary record identities must be unique")
    if not set(ordinary_ids) <= set(ids):
        raise ValueError("Ordinary selection refers to an absent retained record")
    chart_ids = set()
    for chart in value["charts"]:
        _object(chart, ("id", "provenance", "total_cells", "rows", "target"), "Chart view")
        target = (
            _load(validate_target_json(chart["target"])) if chart["target"] is not None else None
        )
        target_pairs = (
            {(r["sample_id"], r["sample_loc"]) for r in target["rows"]} if target else None
        )
        _text(chart["id"], "chart pass id")
        if chart["provenance"] not in ("physical", "simulated", "replay"):
            raise ValueError("Unknown chart provenance")
        if chart["id"] in chart_ids:
            raise ValueError("Chart pass identities must be unique")
        chart_ids.add(chart["id"])
        if type(chart["total_cells"]) is not int or chart["total_cells"] < 1:
            raise ValueError("Chart total_cells must be positive")
        if target and chart["total_cells"] != len(target["rows"]):
            raise ValueError("Chart coverage differs from its frozen TI2 target")
        if not isinstance(chart["rows"], list) or len(chart["rows"]) > chart["total_cells"]:
            raise ValueError("Chart selections exceed the declared chart")
        selected = {key: set() for key in ("record_id", "sample_id", "sample_loc")}
        normalized = set()
        for item in chart["rows"]:
            _object(item, selected, "Chart selection")
            for key in selected:
                _text(item[key], key)
                if item[key] in selected[key]:
                    raise ValueError(f"Duplicate chart {key}")
                selected[key].add(item[key])
            if item["record_id"] not in ids:
                raise ValueError("Chart selection refers to an absent retained record")
            if (
                target_pairs is not None
                and (item["sample_id"], item["sample_loc"]) not in target_pairs
            ):
                raise ValueError("Chart selection source ID/location pair differs from its target")
            location = _location(item["sample_loc"])
            if location in normalized:
                raise ValueError("Chart locations collide after scanin normalization")
            normalized.add(location)
    return rows


def _location(value):
    # scanin.c:1464-1481 fix_it8: two special names and a leading zero for
    # a single final digit. Restrict character classification to ASCII.
    if not value.isascii():
        raise ValueError("Chart locations must use ASCII consumer labels")
    if value in ("Dmin", "Dmax"):
        return {"Dmin": "GS00", "Dmax": "GS23"}[value]
    return re.sub(r"^([^0-9]*)([0-9])$", r"\g<1>0\2", value)


def _xyz_kind(row):
    xyz = row.xyz
    if xyz is None:
        return None, "No retained XYZ"
    if xyz.observer != "CIE1931_2":
        return None, "XYZ observer is unresolved or unsupported"
    if any(float(value) != value for value in xyz.values):
        return None, "XYZ values are not exactly representable by the named reader's doubles"
    if (xyz.unit, xyz.scale, xyz.illuminant) == ("1", "relative; perfect-diffuser Y=100", "D50"):
        return "reflective", ""
    if (xyz.unit, xyz.scale, xyz.illuminant) == (
        "cd/m^2",
        "absolute; Y is luminance",
        "none (emissive)",
    ):
        return "emissive", ""
    return None, "XYZ quantity, units, reference or scale has no qualified CTI3 dialect"


def _spectrum_kind(row):
    spectrum = row.spectrum
    if spectrum is None:
        return None, (), "No retained spectrum"
    if (spectrum.quantity, spectrum.unit) == ("reflectance", "%"):
        kind, multiplier = "reflective", 1.0
    elif spectrum.quantity in ("spectral_radiance", "radiance") and spectrum.unit in (
        "mW/(m^2 sr nm)",
        "W/(m^2 sr nm)",
        "W/(sr m2 nm)",
    ):
        kind = "emissive"
        multiplier = 1.0 if spectrum.unit.startswith("mW") else 1000.0
    else:
        return None, (), "Spectrum quantity/units are unresolved or have no qualified CTI3 dialect"
    if (
        spectrum.unit == "W/(sr m2 nm)"
        and row.source["context"]["material"].get("spectralUnitsConfirmed") is not True
    ):
        return None, (), "CS native spectral units have not been explicitly confirmed"
    configuration = row.source["context"]["material"].get("configuration", {})
    if isinstance(configuration, dict) and "spectrum_norm" in configuration:
        try:
            norm = float(configuration["spectrum_norm"])
        except (ValueError, TypeError, OverflowError):
            norm = None
        if norm != (100.0 if kind == "reflective" else 1.0):
            return None, (), "Retained spectral normalization differs from the qualified units"
    grid = spectrum.wavelengths_nm
    # spec2cie.c:674-703 reconstructs a uniform grid and rounds its FIELD NAMES,
    # not its retained start/end wavelengths. No implicit resampling occurs.
    if not 2 <= len(grid) <= 601:
        return None, (), "CTI3 spectral reader requires 2..601 bands"
    if any(float(value) != value for value in grid):
        return None, (), "Spectral grid is not representable by the named reader's doubles"
    expected = _grid(grid)
    if any(abs(a - b) > 4 * max(math.ulp(a), math.ulp(b)) for a, b in zip(grid, expected)):
        return None, (), "CTI3 cannot represent an irregular grid without resampling"
    if grid[-1] > 2**31 - 1 - 0.5:
        return None, (), "Spectral field wavelength exceeds the named reader's integer range"
    names = [int(w + 0.5) for w in expected]
    if len(set(names)) != len(names):
        return None, (), "Rounded spectral field names collide"
    converted = tuple(float(v) * multiplier for v in spectrum.values)
    if any(
        not math.isfinite(v) or (v == 0 and original != 0)
        for original, v in zip(spectrum.values, converted)
    ):
        return None, (), "Spectral unit conversion is not representable"
    if any(float(v) != v for v in spectrum.values):
        return None, (), "Spectral values are not representable by the named reader's doubles"
    return kind, converted, ""


def _grid(grid):
    return tuple(
        float(grid[0]) + i / (len(grid) - 1) * (float(grid[-1]) - float(grid[0]))
        for i in range(len(grid))
    )


def _quote(value):
    # CGATS unquote_cs (cgats.c:1896-1911) escapes a quote by doubling it.
    return '"' + value.replace('"', '""') + '"'


def _real(value):
    token = format(float(value), ".17g")
    return token if "." in token or "e" in token else token + ".0"


def _key(row, kind, xyz, spectrum):
    return _json(
        {
            "kind": kind,
            "material": row.source["context"]["material"],
            "provenance": row.source["provenance"],
            "xyz": ({k: v for k, v in row.source["xyz"].items() if k != "values"} if xyz else None),
            "spectrum": (
                {k: v for k, v in row.source["spectrum"].items() if k != "values"}
                if spectrum
                else None
            ),
        }
    )


def _payload(snapshot, snapshot_id, group_id, scope, kind, entries, *, total=None, target=None):
    row, xyz, spectrum, _, _ = entries[0]
    fields = [
        "SAMPLE_ID",
        "CM_RECORD_ID",
        "CM_NAME_JSON",
        "CM_NOTES_JSON",
        "CM_PROVENANCE",
        "CM_CONTEXT_JSON",
    ]
    if scope != "ordinary":
        fields.append("SAMPLE_LOC")
    if target:
        fields.extend(target["channel_fields"])
    if xyz:
        fields.extend(("XYZ_X", "XYZ_Y", "XYZ_Z"))
    if spectrum:
        fields.extend(f"SPEC_{int(w + 0.5):03d}" for w in _grid(row.spectrum.wavelengths_nm))
    keywords = {
        "DESCRIPTOR": "Color Measurement "
        + ("reflective" if kind == "reflective" else "absolute emissive")
        + " measurement-only reference",
        "ORIGINATOR": "Color Measurement",
        "DEVICE_CLASS": "INPUT" if kind == "reflective" else "EMISINPUT",
        "CM_EXPORT_KIND": scope,
        "CM_OWNER_ID": snapshot["owner_id"],
        "CM_GROUP_ID": group_id,
        "CM_SOURCE_SHA256": snapshot["source_sha256"],
        "CM_SOURCE_ID": _source_identity(snapshot["source_id"]),
        "CM_SNAPSHOT_ID": snapshot_id,
        "CM_PROVENANCE": row.source["provenance"],
        "CM_MATERIAL_JSON": _json(row.source["context"]["material"]),
        "CM_XYZ_JSON": _json({k: v for k, v in row.source["xyz"].items() if k != "values"})
        if xyz
        else "null",
        "CM_SPECTRUM_JSON": _json(
            {
                k: v
                for k, v in row.source["spectrum"].items()
                if k not in ("values", "wavelengths_nm")
            }
        )
        if spectrum
        else "null",
    }
    if target:
        keywords.update(
            DESCRIPTOR="Color Measurement target-associated reflective measurements",
            DEVICE_CLASS="OUTPUT",
            COLOR_REP=target["color_rep"] + "_XYZ",
            CM_TARGET_SHA256=target["source_sha256"],
        )
        for name in (
            "CHART_ID",
            "RANDOM_START",
            "SINGLE_DIM_STEPS",
            "COMP_GREY_STEPS",
            "MULTI_DIM_STEPS",
            "FULL_SPREAD_PATCHES",
            "TOTAL_INK_LIMIT",
            "FINAL_TOTAL_INK_LIMIT",
            "TEXT_SPACER_TOTAL_INK_LIMIT",
            "FINAL_TEXT_SPACER_TOTAL_INK_LIMIT",
        ):
            if name in target["keywords"]:
                keywords[name] = target["keywords"][name]
    if kind == "emissive":
        keywords["NORMALIZED_TO_Y_100"] = "NO"
    if total is not None:
        keywords["CM_TOTAL_CELLS"] = str(total)
        keywords["CM_COVERAGE"] = "complete" if len(entries) == total else "partial"
    if spectrum:
        grid = row.spectrum.wavelengths_nm
        keywords.update(
            SPECTRAL_BANDS=str(len(grid)),
            SPECTRAL_START_NM=_real(grid[0]),
            SPECTRAL_END_NM=_real(grid[-1]),
            SPECTRAL_NORM="100" if kind == "reflective" else "1",
            CM_SPECTRAL_UNIT="%" if kind == "reflective" else "mW/(m^2 sr nm)",
        )
    lines = ["CTI3"]
    for name, value in keywords.items():
        if name.startswith("CM_"):
            lines.append(f"KEYWORD {_quote(name)}")
        lines.append(f"{name} {_quote(value)}")
    lines.extend(
        (
            f"NUMBER_OF_FIELDS {len(fields)}",
            "BEGIN_DATA_FORMAT",
            " ".join(fields),
            "END_DATA_FORMAT",
            f"NUMBER_OF_SETS {len(entries)}",
            "BEGIN_DATA",
        )
    )
    expected_rows = []
    for row, xyz, spectrum, sample_id, location in entries:
        expected = [
            sample_id,
            _json(row.source["id"]),
            _json(row.source["name"]),
            _json(row.source["notes"]),
            row.source["provenance"],
            _json(row.source["context"]),
        ]
        values = [
            _quote(sample_id),
            _quote(_json(row.source["id"])),
            _quote(_json(row.source["name"])),
            _quote(_json(row.source["notes"])),
            _quote(row.source["provenance"]),
            _quote(_json(row.source["context"])),
        ]
        if scope != "ordinary":
            values.append(_quote(location))
            expected.append(location)
        if target:
            source = next(r for r in target["rows"] if r["sample_loc"] == location)
            values.extend(_real(v) for v in source["device_values"])
            expected.extend(source["device_values"])
        if xyz:
            values.extend(_real(v) for v in row.xyz.values)
            expected.extend(row.xyz.values)
        if spectrum:
            values.extend(_real(v) for v in _spectrum_kind(row)[1])
            expected.extend(_spectrum_kind(row)[1])
        lines.append(" ".join(values))
        expected_rows.append(tuple(expected))
    lines.extend(("END_DATA", ""))
    text = "\n".join(lines)
    (table,) = read_cgats(text)
    numerical = [field for field in fields if field.startswith(("XYZ_", "SPEC_", "RGB_", "CMYK_"))]
    if any(table.field_types[field] != REAL for field in numerical):
        raise ValueError("TI3 numerical columns must read back as real fields")
    if table.rows != tuple(expected_rows) or table.fields != tuple(fields):
        raise ValueError("TI3 field/set/value/text readback differs")
    if dict(table.keywords) != keywords:
        raise ValueError("TI3 metadata readback differs")
    return text.encode("utf-8")


def _file(
    group_id, scope, provenance, path, ids, *, chart_id=None, total=None, reason="", target=None
):
    return dict(
        group_id=group_id,
        scope=scope,
        path=str(path),
        row_ids=ids,
        row_count=len(ids),
        total_rows=total,
        state="unavailable",
        payload_sha256=None,
        snapshot_id=None,
        reason=reason,
        provenance=provenance,
        target_path=str(path.with_suffix(".ti2")) if target else None,
        target_sha256=target["source_sha256"] if target else None,
        chart_id=chart_id,
    )


def _previous(text, owner, source_id, folder):
    value = _load(text)
    if value is None:
        return {}
    _object(
        value,
        (
            "schema_version",
            "owner_id",
            "source_id",
            "source_sha256",
            "snapshot_id",
            "status",
            "retryable",
            "relocatable",
            "files",
            "unavailable",
        ),
        "Previous result",
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("Unsupported previous TI3 schema")
    if value["owner_id"] != owner or value["status"] not in ("current", "unavailable", "stale"):
        raise ValueError("Previous TI3 result belongs to another owner or has an invalid status")
    if value["source_id"] != source_id:
        raise ValueError("Previous TI3 result belongs to another authoritative source")
    _hash(value["source_sha256"])
    if type(value["retryable"]) is not bool:
        raise ValueError("Previous TI3 retryability must be boolean")
    if type(value["relocatable"]) is not bool:
        raise ValueError("Previous TI3 relocation decision must be boolean")
    if value["snapshot_id"] is None:
        if (
            value["retryable"]
            or value["relocatable"]
            or value["status"] not in ("unavailable", "stale")
            or not isinstance(value["files"], list)
            or any(
                not isinstance(item, dict) or item.get("state") == "current"
                for item in value["files"]
            )
        ):
            raise ValueError(
                "Absent snapshot identity cannot claim a current, retryable "
                "or relocatable projection"
            )
    else:
        _hash(value["snapshot_id"])
    if not isinstance(value["files"], list) or not isinstance(value["unavailable"], list):
        raise ValueError("Previous results must contain file and disposition arrays")
    previous = {}
    for item in value["files"]:
        _object(
            item,
            (
                "group_id",
                "scope",
                "path",
                "row_ids",
                "row_count",
                "total_rows",
                "state",
                "payload_sha256",
                "snapshot_id",
                "reason",
                "provenance",
                "chart_id",
                "target_path",
                "target_sha256",
            ),
            "Previous file",
        )
        group_id = _hash(item["group_id"])
        _text(item["path"], "previous path")
        _text(item["reason"], "previous reason", empty=True)
        if item["provenance"] not in ("physical", "simulated", "replay"):
            raise ValueError("Unknown previous provenance")
        path = Path(item["path"])
        if (
            path != folder / _name(group_id, item["scope"], item["provenance"])
            or item["scope"] not in ("ordinary", "chart_reference", "chart_target")
            or group_id in previous
        ):
            raise ValueError(
                "Previous TI3 file path/scope/group differs from its owned destination"
            )
        if item["payload_sha256"] is not None:
            _hash(item["payload_sha256"])
            _hash(item["snapshot_id"])
        elif item["snapshot_id"] is not None or item["state"] == "current":
            raise ValueError("Previous current file must bind its payload and snapshot")
        if item["state"] not in ("current", "unavailable", "stale", "retired"):
            raise ValueError("Unknown previous file state")
        if (
            not isinstance(item["row_ids"], list)
            or type(item["row_count"]) is not int
            or item["row_count"] != len(item["row_ids"])
        ):
            raise ValueError("Previous row count/identities differ")
        if item["state"] == "retired" and not item["row_ids"]:
            raise ValueError("Previous retired file must retain nonempty historical row identities")
        for row_id in item["row_ids"]:
            _text(row_id, "previous row id")
        if item["scope"] == "chart_target":
            if item["target_path"] != str(path.with_suffix(".ti2")):
                raise ValueError("Previous target source must share its owned TI3 basename")
            _hash(item["target_sha256"])
        elif item["target_path"] is not None or item["target_sha256"] is not None:
            raise ValueError("Reference/ordinary files cannot claim a paired TI2 target")
        if len(set(item["row_ids"])) != len(item["row_ids"]):
            raise ValueError("Previous row identities must be unique")
        if item["scope"] == "ordinary":
            if item["total_rows"] is not None or item["chart_id"] is not None:
                raise ValueError("Ordinary files cannot claim chart coverage")
        else:
            _text(item["chart_id"], "previous chart id")
            if type(item["total_rows"]) is not int or item["total_rows"] < item["row_count"]:
                raise ValueError("Previous chart coverage differs")
        previous[group_id] = item
    for item in value["unavailable"]:
        _object(item, ("record_id", "component", "reason"), "Previous disposition")
        _text(item["record_id"], "previous disposition id")
        _text(item["reason"], "previous disposition reason")
        if item["component"] not in ("record", "xyz", "spectrum"):
            raise ValueError("Unknown previous disposition component")
    return previous


def validate_result_json(
    result_json: str, *, owner_id: str, source_id: str, folder: str | Path
) -> dict:
    """Validate a complete writer receipt without modifying or trusting its files.

    Schema, owner, source and path claims use the same validator as update_json.
    Physical file authentication remains the writer's explicit update operation.
    """
    _previous(result_json, owner_id, source_id, Path(folder))
    result = _load(result_json)
    if result is None:
        raise ValueError("A complete writer receipt cannot be null")
    return result


def _name(group_id, scope, provenance):
    if scope == "chart_target":
        return f"chart-target-{provenance}-{group_id}/target-{provenance}.ti3"
    return (
        ("measurements-" if scope == "ordinary" else "chart-reference-")
        + provenance
        + "-"
        + group_id
        + ".ti3"
    )


def _last_good(file, owner, source_id):
    """Bind claimed prior bytes to this owner, file identity and actual rows."""
    payload = _read_regular(Path(file["path"]))
    if _digest(payload) != file["payload_sha256"]:
        raise FileExistsError("Previous companion bytes changed")
    if file["scope"] == "chart_target":
        if _digest(_read_regular(Path(file["target_path"]))) != file["target_sha256"]:
            raise FileExistsError("Previous paired TI2 source bytes changed")
    try:
        (table,) = read_cgats(payload.decode("utf-8"))
        expected = {
            "CM_OWNER_ID": owner,
            "CM_SOURCE_ID": _source_identity(source_id),
            "CM_GROUP_ID": file["group_id"],
            "CM_SNAPSHOT_ID": file["snapshot_id"],
            "CM_EXPORT_KIND": file["scope"],
            "CM_PROVENANCE": file["provenance"],
        }
        if file["scope"] == "chart_target":
            expected["CM_TARGET_SHA256"] = file["target_sha256"]
        source_header = table.keywords.get("CM_SOURCE_ID")
        if source_header != expected["CM_SOURCE_ID"] and source_header == source_id:
            raise ValueError(
                "Previous companion uses the old cleartext CM_SOURCE_ID format; "
                "restoring the authentic previous result cannot authorize replacement. "
                "Use a new destination"
            )
        if table.identifier != "CTI3" or any(
            table.keywords.get(k) != v for k, v in expected.items()
        ):
            raise ValueError("Previous companion ownership metadata differs")
        index = table.index("CM_RECORD_ID")
        if index < 0 or [_load(row[index]) for row in table.rows] != file["row_ids"]:
            raise ValueError("Previous companion retained row identities differ")
    except (ValueError, UnicodeError, TypeError) as error:
        raise FileExistsError(str(error)) from error


def _failed_file(file, reason, previous, owner, source_id):
    """Retain only authenticated last-good bytes when a projection fails."""
    retired = file["state"] == "retired"
    old = previous.get(file["group_id"])
    if old and old["payload_sha256"]:
        try:
            _last_good(old, owner, source_id)
        except OSError as error:
            file.update(payload_sha256=None, snapshot_id=None, state="stale")
            reason += "; previous companion is missing, changed or unconfirmed: " + str(error)
        else:
            file.update(old, state="stale")
    else:
        file["state"] = (
            "stale" if file["state"] in ("current", "stale") or not file["reason"]
            else "unavailable"
        )
    file["reason"] = reason
    if retired:
        file["state"] = "retired"


def _status(files):
    states = {file["state"] for file in files}
    return "stale" if "stale" in states else "current" if "current" in states else "unavailable"


def _historical(file):
    """A prior plan alone is not evidence that a companion was published."""
    return file is not None and (file["payload_sha256"] is not None or file["state"] == "retired")


def update_json(snapshot_json: str, folder: str, previous_json: str = "null") -> str:
    """Refresh owned companions after a durable source save, never acquiring.

    Validation fails before publication. The caller supplies a frozen retained
    snapshot and the complete last result for updates. Cooperating updates in a
    folder are excluded with a persistent kernel lock. Changed/foreign files
    are refused; an exact already-published payload can be reused after a lost
    response. Unsupported data and I/O failures leave source/last-good files
    intact and return precise per-component/per-file outcomes. A planned export
    creates its missing companion folder; the parent must already exist.

    ``ordinary_record_ids`` selects current acquisitions in their supplied
    order. Other retained records remain available to chart selections under
    their original identities, without becoming new ordinary acquisitions.
    The complete record set is validated before either projection is written.
    """
    snapshot = _load(snapshot_json)
    rows = _snapshot(snapshot)
    folder = Path(folder).resolve()
    previous = _previous(previous_json, snapshot["owner_id"], snapshot["source_id"], folder)
    snapshot_id = _digest(_json(snapshot).encode("ascii"))
    result = dict(
        schema_version=2,
        owner_id=snapshot["owner_id"],
        source_id=snapshot["source_id"],
        source_sha256=snapshot["source_sha256"],
        snapshot_id=snapshot_id,
        status="unavailable",
        retryable=False,
        relocatable=False,
        files=[],
        unavailable=[],
    )

    def failed(file, reason):
        _failed_file(file, reason, previous, snapshot["owner_id"], snapshot["source_id"])

    def inactive(old):
        file = dict(old)
        if _historical(old):
            file["state"] = "retired"
            reason = "Companion group is retired from the current snapshot"
        else:
            file.update(state="unavailable")
            reason = "Companion group is absent; no authenticated historical output"
        failed(file, reason)
        return file

    try:
        snapshot = _load(normalize_snapshot_json(_json(snapshot)))
        rows = _snapshot(snapshot)
    except (ValueError, TypeError, UnicodeError) as error:
        reason = f"TI3 projection unavailable: {error}"
        result.update(
            snapshot_id=None,
            unavailable=[
                dict(record_id=row.source["id"], component="record", reason=reason) for row in rows
            ],
        )
        for old in previous.values():
            file = dict(old)
            failed(file, reason)
            result["files"].append(file)
        result["status"] = _status(result["files"])
        return _json(result)
    snapshot_id = _digest(_json(snapshot).encode("ascii"))
    result["snapshot_id"] = snapshot_id
    groups = {}

    def unavailable(row, component, reason):
        result["unavailable"].append(
            dict(record_id=row.source["id"], component=component, reason=reason)
        )

    by_id = {row.source["id"]: row for row in rows}
    for identity in snapshot["ordinary_record_ids"]:
        row = by_id[identity]
        if row.source["kind"] != "color":
            provenance = row.source["context"]["provenance"]
            reason = (
                provenance["presentationInvalidationReason"]
                if row.source["kind"] == "timing"
                and provenance.get("presentationInvalidated") is True
                else f"CTI3 cannot represent {row.source['kind']} outcomes"
            )
            unavailable(row, "record", reason)
            continue
        xkind, xreason = _xyz_kind(row)
        skind, _, sreason = _spectrum_kind(row)
        if not xkind:
            unavailable(row, "xyz", xreason)
        if not skind:
            unavailable(row, "spectrum", sreason)
        if not xkind and not skind:
            unavailable(row, "record", "No color component has a qualified CTI3 representation")
        for kind in dict.fromkeys(k for k in (xkind, skind) if k):
            xyz, spectrum = xkind == kind, skind == kind
            key = _key(row, kind, xyz, spectrum)
            groups.setdefault(key, []).append((row, xyz, spectrum, row.source["id"], ""))
    planned = []
    for key, entries in groups.items():
        group = _digest(
            _json([snapshot["owner_id"], snapshot["source_id"], "ordinary", key]).encode("ascii")
        )
        provenance = entries[0][0].source["provenance"]
        planned.append(
            (
                _file(
                    group,
                    "ordinary",
                    provenance,
                    folder / _name(group, "ordinary", provenance),
                    [r.source["id"] for r, *_ in entries],
                ),
                _payload(
                    snapshot, snapshot_id, group, "ordinary", json.loads(key)["kind"], entries
                ),
            )
        )
    targets = {}
    for chart in snapshot["charts"]:
        provenance = chart["provenance"]
        target = (
            _load(validate_target_json(chart["target"])) if chart["target"] is not None else None
        )
        scope = "chart_target" if target else "chart_reference"
        identity = [snapshot["owner_id"], snapshot["source_id"], scope, chart["id"], provenance]
        if target:
            identity.append(target["source_sha256"])
        group = _digest(_json(identity).encode())
        selected_rows = chart["rows"]
        if target:
            order = {r["sample_loc"]: r["source_row"] for r in target["rows"]}
            selected_rows = sorted(selected_rows, key=lambda item: order[item["sample_loc"]])
            targets[group] = base64.b64decode(target["source_base64"], validate=True)
        entries = [
            (by_id[item["record_id"]], True, False, item["sample_id"], item["sample_loc"])
            for item in selected_rows
        ]
        reason = ""
        if not entries:
            reason = "No selected measured chart readings"
        elif any(row.source["provenance"] != provenance for row, *_ in entries):
            reason = "Selected chart provenance differs from the retained pass"
        elif any(_xyz_kind(row)[0] != "reflective" for row, *_ in entries):
            reason = (
                "Every chart selection requires qualified reflective XYZ; ordinary TI3 is separate"
            )
        elif len({_key(row, "reflective", True, False) for row, *_ in entries}) != 1:
            reason = "Selected chart readings have incompatible measurement contexts"
        spectra = bool(entries) and all(
            _spectrum_kind(row)[0] == "reflective" for row, *_ in entries
        )
        if spectra and len({row.spectrum.wavelengths_nm for row, *_ in entries}) != 1:
            spectra = False
        if not spectra and not reason:
            for row, *_ in entries:
                unavailable(
                    row,
                    "spectrum",
                    ("Chart target" if target else "Chart reference")
                    + " is XYZ-only: selected spectra "
                    "require one compatible representable reflective grid",
                )
        entries = [(row, xyz, spectra, sid, loc) for row, xyz, _, sid, loc in entries]
        file = _file(
            group,
            scope,
            provenance,
            folder / _name(group, scope, provenance),
            [r.source["id"] for r, *_ in entries],
            chart_id=chart["id"],
            total=chart["total_cells"],
            reason=reason,
            target=target,
        )
        payload = (
            None
            if reason
            else _payload(
                snapshot,
                snapshot_id,
                group,
                scope,
                "reflective",
                entries,
                total=chart["total_cells"],
                target=target,
            )
        )
        planned.append((file, payload))

    try:
        if planned or previous:
            folder.mkdir(exist_ok=True)
        with _folder_lock(folder, ".ti3-export.lock") if planned or previous else nullcontext():
            for file, payload in planned:
                if payload is None:
                    reason = file["reason"]
                    empty_chart = (
                        file["scope"] in ("chart_reference", "chart_target") and not file["row_ids"]
                    )
                    if empty_chart and _historical(previous.get(file["group_id"])):
                        file.update(previous[file["group_id"]])
                        file["state"] = "retired"
                    failed(file, reason)
                else:
                    old = previous.get(file["group_id"])
                    try:
                        if file["scope"] == "chart_target":
                            destination = Path(file["path"]).parent
                            destination.mkdir(exist_ok=True)
                            if not stat.S_ISDIR(destination.lstat().st_mode):
                                raise OSError(
                                    "Target companion folder must be an ordinary directory"
                                )
                            _publish_owned_file(
                                Path(file["target_path"]), targets[file["group_id"]], None
                            )
                        if old and old["payload_sha256"]:
                            try:
                                existing = _read_regular(Path(file["path"]))
                            except FileNotFoundError:
                                existing = None
                            if existing is not None and existing != payload:
                                _last_good(old, snapshot["owner_id"], snapshot["source_id"])
                        _publish_owned_file(
                            Path(file["path"]), payload, old["payload_sha256"] if old else None
                        )
                        file.update(
                            state="current",
                            payload_sha256=_digest(payload),
                            snapshot_id=snapshot_id,
                        )
                    except OSError as error:
                        result["retryable"] |= not isinstance(error, FileExistsError)
                        result["relocatable"] = True
                        failed(file, str(error))
                result["files"].append(file)
            present = {file["group_id"] for file in result["files"]}
            for group, old in previous.items():
                if group not in present:
                    result["files"].append(inactive(old))
    except OSError as error:
        for file, payload in planned:
            if file["group_id"] not in {f["group_id"] for f in result["files"]}:
                # A non-publishing plan keeps its semantic refusal even when
                # the folder lock fails. Active publications keep the I/O error.
                reason = str(error) if payload is not None else file["reason"]
                if payload is None and not file["row_ids"] and _historical(
                    previous.get(file["group_id"])
                ):
                    file.update(previous[file["group_id"]], state="retired")
                if payload is not None:
                    result["retryable"] |= not isinstance(error, FileExistsError)
                    result["relocatable"] = True
                failed(file, reason)
                result["files"].append(file)
        for group, old in previous.items():
            if group not in {f["group_id"] for f in result["files"]}:
                result["files"].append(inactive(old))
    result["status"] = _status(result["files"])
    return _json(result)


def retained_snapshot(observations, owner_id, source_sha256, *, source_id):
    """Project current immutable observations, preserving native/derived origins.

    All supplied observations are current acquisitions. A linked-chart caller
    adds validated inherited records separately from ``ordinary_record_ids``.
    """
    rows = []
    for observation in observations:
        native = observation.backend_reported
        color = isinstance(observation, Observation)
        xyz = asdict(native.xyz) if color and native.xyz else None
        if xyz:
            xyz.update(origin="native", method="backend-reported XYZ")
        original = dict(observation.configuration)
        config = dict(original)
        # Incidental timing/order/transport facts remain authoritative JSON, not
        # grouping fields. Unknown material configuration is kept conservatively.
        for key in (
            "acquired_at_source",
            "native_duration_seconds",
            "native_patch_index",
            "strip_direction",
            "strip_direction_source",
            "averages",
            "port",
            "path",
            "native_facts_json",
            "confirmed_routes",
        ):
            config.pop(key, None)
        facts = _load(original["native_facts_json"]) if "native_facts_json" in original else {}
        if not isinstance(facts, dict) or not isinstance(facts.get("settings", []), list):
            raise ValueError("Retained native facts must be an object with a setting array")
        if not isinstance(facts.get("requested", {}), dict):
            raise ValueError("Retained native facts requested member must be an object")
        for item in facts.get("settings", []):
            if not isinstance(item, dict) or not {"key", "value", "presence"} <= set(item):
                raise ValueError("Retained native setting fields are missing")
            _text(item["key"], "native setting key")
        material = dict(
            device={
                k: v
                for k, v in asdict(observation.device).items()
                if k not in ("device_id", "backend", "backend_version")
            },
            mode=observation.actual_mode,
            configuration=config,
            # Readiness is a typed fact. Its explanatory sentence describes how
            # the driver reached that assessment, not a different optical setup.
            calibration={"status": observation.calibration.status},
        )
        if facts:
            material["native_identity"] = facts.get("identity", {})
            material["native_settings"] = sorted(
                [
                    {k: item[k] for k in ("key", "value", "presence")}
                    for item in facts.get("settings", [])
                    if item["key"].lower() != "averages"
                ],
                key=lambda item: _json(item),
            )
            material["native_requested"] = {
                k: v for k, v in facts.get("requested", {}).items() if k != "averages"
            }
            for key in ("factory_calibration", "model_policy", "xyz_origin"):
                if key in facts:
                    material[key] = facts[key]
        rows.append(
            dict(
                id=observation.observation_id,
                name=observation.sample_id,
                notes="",
                provenance=observation.provenance,
                kind="color" if color else observation.record_kind,
                xyz=xyz,
                spectrum=asdict(native.spectrum) if color and native.spectrum else None,
                context=dict(
                    route=observation.device.backend,
                    material=material,
                    provenance=dict(
                        device=asdict(observation.device),
                        acquired_at=observation.acquired_at,
                        calibration=asdict(observation.calibration),
                        configuration={
                            k: v for k, v in original.items() if k != "native_facts_json"
                        },
                        native_facts=facts,
                    ),
                ),
            )
        )
    return dict(
        schema_version=3,
        owner_id=owner_id,
        source_id=source_id,
        source_sha256=source_sha256,
        records=rows,
        ordinary_record_ids=[row["id"] for row in rows],
        charts=[],
    )


def component_eligibility(record):
    """Pure current native-component reasons from the same retained TI3 policy owner."""
    if isinstance(record, OperationResult):
        payload = result_to_json(record).encode("utf-8")
        observations = [patch.observation for patch in record.patches]
        owner = record.operation_id
    elif isinstance(record, Observation):
        from .exports import to_json

        payload = to_json(record).encode("utf-8")
        observations, owner = [record], record.observation_id
    else:
        raise ValueError("Choose a current color observation or terminal operation")
    try:
        snapshot = retained_snapshot(observations, owner, _digest(payload),
                                     source_id="retained-record:" + owner)
        rows = [_row(row) for row in json.loads(_json(snapshot))["records"]]
    except (TypeError, ValueError, UnicodeError) as error:
        return [dict(record_id=observation.observation_id, component="record", available=False,
                     reason=str(error)) for observation in observations]
    result = []
    for row in rows:
        xyz, xyz_reason = _xyz_kind(row)
        spectrum, _, spectrum_reason = _spectrum_kind(row)
        result.extend([
            dict(record_id=row.source["id"], component="xyz", available=xyz is not None,
                 reason=xyz_reason or None),
            dict(record_id=row.source["id"], component="spectrum", available=spectrum is not None,
                 reason=spectrum_reason or None),
        ])
    if not observations:
        result.append(dict(record_id=owner, component="record", available=False,
                           reason="No retained measurement components"))
    return result


def normalize_snapshot_json(snapshot_json: str) -> str:
    """Reuse canonical Argyll context projection for the MATLAB package bridge.

    Caller annotations and chart associations stay caller-owned. A native row
    points to its complete validated operation and original observation ID;
    neither quantity labels nor values may differ from that retained record.
    The projection retains row-local provenance and the canonical JSON digest,
    rather than echoing a whole multi-patch operation into every TI3 row.
    An explicitly invalidated timing presentation accounts for its operation
    without projecting a retained native reading as a usable measurement.
    """
    snapshot = _load(snapshot_json)
    rows = _snapshot(snapshot)
    operations = {}
    for row in rows:
        provenance = row.source["context"]["provenance"]
        if "canonicalOperationJson" not in provenance:
            continue
        canonical = provenance["canonicalOperationJson"]
        if not isinstance(canonical, str):
            raise ValueError("Canonical operation must be JSON text")
        if canonical not in operations:
            operation = result_from_json(canonical)
            observations = {p.observation.observation_id: p.observation for p in operation.patches}
            operations[canonical] = operation, observations, _digest(canonical.encode("utf-8"))
        operation, observations, canonical_sha256 = operations[canonical]
        if row.source["provenance"] != operation.provenance:
            raise ValueError("Canonical operation provenance differs from retained row")
        if provenance.get("presentationInvalidated") is True:
            if (
                operation.workflow != "display_timing"
                or row.source["kind"] != "timing"
                or row.source["id"] != operation.operation_id
                or provenance.get("canonicalOperationId") != operation.operation_id
            ):
                raise ValueError(
                    "Invalidated timing row must identify its canonical timing operation"
                )
            reason = provenance["presentationInvalidationReason"]
            row.source["context"]["material"] = {}
            row.source["context"]["provenance"] = dict(
                canonicalOperationId=operation.operation_id,
                canonicalOperationSha256=canonical_sha256,
                status=operation.status,
                workflow=operation.workflow,
                presentationInvalidated=True,
                presentationInvalidationReason=reason,
            )
            continue
        if not operation.patches:
            if (
                row.source["kind"] != "configuration"
                or provenance.get("canonicalOperationId") != operation.operation_id
                or row.source["id"] != operation.operation_id
            ):
                raise ValueError("Zero-reading row must identify its canonical operation")
            row.source["context"]["material"] = {}
            row.source["context"]["provenance"] = dict(
                canonicalOperationId=operation.operation_id,
                canonicalOperationSha256=canonical_sha256,
                status=operation.status,
                workflow=operation.workflow,
            )
            continue
        observation_id = provenance.get("canonicalObservationId")
        observation = observations.get(observation_id) if isinstance(observation_id, str) else None
        if observation is None:
            raise ValueError("Retained row must identify exactly one canonical observation")
        if row.source["id"] != observation.observation_id:
            raise ValueError("Retained row identity differs from its canonical observation")
        expected = retained_snapshot(
            [observation],
            snapshot["owner_id"],
            snapshot["source_sha256"],
            source_id=snapshot["source_id"],
        )["records"][0]
        expected = _load(_json(expected))
        if any(row.source[key] != expected[key] for key in ("kind", "xyz", "spectrum")):
            raise ValueError("Native row quantities differ from the canonical observation")
        row.source["context"]["material"] = expected["context"]["material"]
        row.source["context"]["provenance"] = dict(
            expected["context"]["provenance"],
            canonicalOperationId=operation.operation_id,
            canonicalObservationId=observation.observation_id,
            canonicalOperationSha256=canonical_sha256,
        )
    return _json(snapshot)


def save_companions(record, path, payload, previous=None):
    """Used by both canonical save APIs after successful source publication."""
    if isinstance(record, OperationResult):
        # Reuse the canonical operation's existing validation owner.
        result_to_json(record)
        observations = [patch.observation for patch in record.patches]
        owner = record.operation_id
    else:
        observations = [record]
        owner = record.observation_id
    source = _digest(payload)
    source_id = str(path.resolve())
    snapshot_id = None
    try:
        snapshot = retained_snapshot(observations, owner, source, source_id=source_id)
        if not observations:
            snapshot["ordinary_record_ids"].append(owner)
            snapshot["records"].append(
                dict(
                    id=owner,
                    name="",
                    notes="",
                    provenance=record.provenance,
                    kind="configuration",
                    xyz=None,
                    spectrum=None,
                    context=dict(
                        route=record.device.backend,
                        material={},
                        provenance=dict(status=record.status, workflow=record.workflow),
                    ),
                )
            )
        snapshot_id = _digest(_json(snapshot).encode("ascii"))
        return json.loads(update_json(_json(snapshot), str(path.parent), _json(previous)))
    except (ValueError, TypeError, UnicodeError) as error:
        # A durable source remains successful when its optional projection is
        # unavailable. Complete values and metadata are still in that source.
        reason = f"TI3 projection unavailable: {error}"
        files = []
        try:
            confirmed = _previous(_json(previous), owner, source_id, path.parent.resolve())
        except (ValueError, TypeError, UnicodeError):
            confirmed = {}
        for old in confirmed.values():
            file = dict(old)
            _failed_file(file, reason, confirmed, owner, source_id)
            files.append(file)
        return dict(
            schema_version=2,
            owner_id=owner,
            source_id=source_id,
            source_sha256=source,
            snapshot_id=snapshot_id,
            status=_status(files),
            retryable=False,
            relocatable=False,
            files=files,
            unavailable=[
                dict(
                    record_id=o.observation_id,
                    component="record",
                    reason=reason,
                )
                for o in observations
            ]
            or [
                dict(
                    record_id=owner,
                    component="record",
                    reason=reason,
                )
            ],
        )
