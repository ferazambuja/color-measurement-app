"""Bounded CTI2 target semantics over the shared CGATS reader.

Argyll 3.5.0 chartread.c:2918-2995 specifies layout and indexing defaults;
printtarg.c:3756-3788 writes dimensions, locations and 0..100 device channels.
The supported alphabetic and decimal ranges follow alphix.c:31-103,163-177.
This first importer admits only one complete rectangular reflective sheet.
It never selects an instrument, interprets expected values as measurements,
or infers a physical placement. MATLAB owns the user's axis confirmation.

Exact source bytes are retained once. Revalidation derives every semantic
field from those bytes; neither a moved source file nor edited projections
can change an accepted target. Unknown reference conditions stay unavailable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path

from .cgats import _unquote, read_cgats
from .json_values import unique_object
from .science import ColorDomainError, lab_to_xyz

MAX_SOURCE_BYTES = 1024 * 1024
MAX_PATCHES = 140
_STRIP_PATTERN = "A-Z, A-Z"
_PATCH_DEFAULT = "0-9,@-9,@-9;1-999"
_PATCH_PADDED = "@-9,0-9;01-99"
_CHANNELS = {
    "RGB": ["RGB_R", "RGB_G", "RGB_B"],
    "iRGB": ["RGB_R", "RGB_G", "RGB_B"],
    "CMYK": ["CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"],
}


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _label(value, name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or any(not 32 <= ord(char) <= 126 for char in value)
    ):
        raise ValueError(f"{name} requires 1..128 printable ASCII characters")
    return value


def _positive(value, name):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError(f"{name} requires one positive integer")
    if len(value) > 9 or not 1 <= int(value) <= MAX_PATCHES:
        raise ValueError(f"{name} exceeds the supported 1..{MAX_PATCHES} range")
    return int(value)


def _location(text, order, pattern):
    width = 3 if pattern == _PATCH_DEFAULT else 2
    expressions = {
        "STRIP_THEN_PATCH": rf"([A-Z]{{1,2}})([0-9]{{1,{width}}})",
        "PATCH_THEN_STRIP": rf"([0-9]{{1,{width}}})([A-Z]{{1,2}})",
    }
    match = re.fullmatch(expressions[order], text)
    if not match:
        raise ValueError(f"SAMPLE_LOC {text!r} is outside the declared location pattern")
    alpha, number = match.groups() if order == "STRIP_THEN_PATCH" else match.groups()[::-1]
    if pattern == _PATCH_PADDED and len(number) != 2:
        raise ValueError(f"SAMPLE_LOC {text!r} requires a two-digit patch index")
    strip = 0
    for char in alpha:
        strip = 26 * strip + ord(char) - ord("A") + 1
    step = int(number)
    if not 1 <= step <= (999 if width == 3 else 99):
        raise ValueError(f"SAMPLE_LOC {text!r} is outside the declared numeric range")
    return strip, step


def _parse(payload, filename):
    if len(payload) > MAX_SOURCE_BYTES:
        raise ValueError("TI2 source exceeds the 1 MiB limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("TI2 source must be UTF-8 text") from error
    tables = read_cgats(text, expected_identifier="CTI2")
    if len(tables) != 1:
        raise ValueError("TI2 import requires exactly one CTI2 table; appended CAL is unsupported")
    table = tables[0]
    if table.declared_fields is None or table.declared_sets is None:
        raise ValueError("TI2 requires NUMBER_OF_FIELDS and NUMBER_OF_SETS")
    names = [name for name, _ in table.keyword_entries]
    if len(set(names)) != len(names):
        raise ValueError("TI2 contains repeated or conflicting keyword/count declarations")
    if len(set(table.fields)) != len(table.fields):
        raise ValueError("TI2 data fields must be unique")
    count = len(table.rows)
    if not 1 <= count <= MAX_PATCHES:
        raise ValueError(f"TI2 has {count} patches; supported count is 1..{MAX_PATCHES}")
    keywords = dict(table.keywords)
    if "PASSES_IN_STRIPS" in keywords or "HEXAGON_PATCHES" in keywords:
        raise ValueError("Old, staggered or hexagonal target layouts are unsupported")
    if keywords.get("DEVICE_CLASS", "OUTPUT") != "OUTPUT":
        raise ValueError("TI2 import supports printed reflective OUTPUT targets only")
    strips = _positive(keywords.get("PASSES_IN_STRIPS2"), "PASSES_IN_STRIPS2")
    steps = _positive(keywords.get("STEPS_IN_PASS"), "STEPS_IN_PASS")
    if strips * steps != count:
        raise ValueError("TI2 requires one fully populated rectangular sheet matching its count")
    if keywords.get("STRIP_INDEX_PATTERN", _STRIP_PATTERN) != _STRIP_PATTERN:
        raise ValueError("Unsupported STRIP_INDEX_PATTERN")
    pattern = keywords.get("PATCH_INDEX_PATTERN", _PATCH_DEFAULT)
    if pattern not in (_PATCH_DEFAULT, _PATCH_PADDED):
        raise ValueError("Unsupported PATCH_INDEX_PATTERN")
    order = keywords.get("INDEX_ORDER", "STRIP_THEN_PATCH")
    if order not in ("STRIP_THEN_PATCH", "PATCH_THEN_STRIP"):
        raise ValueError("Unsupported INDEX_ORDER")
    representation = keywords.get("COLOR_REP")
    if representation not in _CHANNELS:
        raise ValueError("TI2 COLOR_REP must explicitly declare RGB, iRGB or CMYK")
    channels = _CHANNELS[representation]
    required = ["SAMPLE_ID", "SAMPLE_LOC", *channels]
    if any(table.index(field) < 0 for field in required):
        raise ValueError("TI2 lacks required SAMPLE_ID, SAMPLE_LOC or complete device channels")
    if table.field_types["SAMPLE_LOC"] != "quoted":
        raise ValueError("TI2 SAMPLE_LOC must be quoted for the pinned chartread resume reader")
    if any(table.field_types[field] not in ("int", "real") for field in channels):
        raise ValueError("TI2 device channels must be numerical")
    rows, identities, positions = [], set(), set()
    for index, (values, raw) in enumerate(zip(table.rows, table.raw_rows), 1):
        identity = _label(_unquote(raw[table.index("SAMPLE_ID")]), "SAMPLE_ID")
        location = _label(_unquote(raw[table.index("SAMPLE_LOC")]), "SAMPLE_LOC")
        if identity == "0":
            raise ValueError("TI2 printtarg padding ID 0 is unsupported")
        if identity in identities:
            raise ValueError("TI2 SAMPLE_ID is duplicate")
        position = _location(location, order, pattern)
        if position in positions:
            raise ValueError("TI2 SAMPLE_LOC values collide after location normalization")
        if not (1 <= position[0] <= strips and 1 <= position[1] <= steps):
            raise ValueError("TI2 SAMPLE_LOC falls outside its declared rectangle")
        device = [values[table.index(field)] for field in channels]
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in device):
            raise ValueError("TI2 device channels require finite 0..100 values without clipping")
        identities.add(identity)
        positions.add(position)
        rows.append(
            dict(
                source_row=index,
                sample_id=identity,
                sample_loc=location,
                strip=position[0],
                step=position[1],
                device_values=device,
                values=list(values),
                raw_values=list(raw),
            )
        )
    # Unique in-range positions with the exact rectangular count cover every cell.
    return dict(
        schema_version=1,
        importer_revision=1,
        filename=filename,
        source_base64=base64.b64encode(payload).decode("ascii"),
        source_sha256=hashlib.sha256(payload).hexdigest(),
        keywords=keywords,
        fields=list(table.fields),
        field_types=dict(table.field_types),
        strips=strips,
        steps=steps,
        color_rep=representation,
        channel_fields=channels,
        rows=rows,
        reference=dict(
            available=False,
            reason="Source expected fields are retained; reference rendering requires "
            "qualified white, observer and scale and is unavailable in this importer",
        ),
    )


def import_ti2_json(path: str) -> str:
    """Read at most 1 MiB plus a limit sentinel; return frozen plain target JSON."""
    source = Path(path)
    filename = _filename(source.name)
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("TI2 source must be an ordinary file before opening")
    descriptor = os.open(
        source, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        opened = os.fstat(descriptor)
        current = source.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("TI2 source changed identity while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_SOURCE_BYTES + 1)
    finally:
        os.close(descriptor)
    return validate_target_json(_json(_parse(payload, filename)))


def _filename(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 1024
        or "/" in value
        or "\\" in value
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    ):
        raise ValueError(
            "Retained target filename must be descriptive basename text without controls"
        )
    value.encode("utf-8")
    return value


def _same(actual, expected):
    """Compare projections without treating JSON booleans as numerical ones."""
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and all(_same(actual[key], item) for key, item in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_same(a, b) for a, b in zip(actual, expected))
        )
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def validate_target_json(text: str) -> str:
    """Authenticate a retained target and all projections, without reading its path."""

    def invalid(value):
        raise ValueError(f"Nonfinite target JSON constant: {value}")

    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid)
    except RecursionError as error:
        raise ValueError("Retained TI2 target JSON nesting exceeds the supported depth") from error
    if not isinstance(value, dict):
        raise ValueError("Retained TI2 target must be an object")
    filename = _filename(value.get("filename"))
    encoded = value.get("source_base64")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_SOURCE_BYTES + 2) // 3):
        raise ValueError("Retained TI2 byte snapshot is missing or exceeds 1 MiB")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Retained TI2 byte snapshot is invalid base64") from error
    expected = _parse(payload, filename)
    if not _same(value, expected):
        raise ValueError("Retained TI2 digest, source identity or semantic projection changed")
    return _json(expected)


def _expected_context(text):
    if not isinstance(text, str) or len(text) > 16384:
        raise ValueError("Expected reference context must be bounded JSON text")

    def invalid(value):
        raise ValueError(f"Nonfinite reference JSON constant: {value}")

    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid)
    except RecursionError as error:
        raise ValueError(
            "Expected reference context nesting exceeds the supported depth"
        ) from error
    if value is None:
        return {}, "Expected values need an explicit white, observer, units, scale and meaning"
    fields = {
        "observerId",
        "xyzUnits",
        "xyzScale",
        "whiteXYZ",
        "illuminant",
        "interpretation",
        "meaning",
        "sourceFields",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Expected reference context requires exactly the declared fields")
    for name in fields - {"whiteXYZ", "sourceFields"}:
        # MATLAB's numel(char(field)) counts UTF-16 units, not Unicode code
        # points. A little-endian encoding adds no byte-order mark; surrogatepass
        # counts existing code units without introducing a new Unicode policy.
        if (
            not isinstance(value[name], str)
            or not value[name].strip()
            or len(value[name].encode("utf-16-le", "surrogatepass")) // 2 > 2048
        ):
            raise ValueError(
                f"Expected reference {name} must be nonblank text within 2048 UTF-16 code units"
            )
    if value["sourceFields"] not in (["XYZ_X", "XYZ_Y", "XYZ_Z"], ["LAB_L", "LAB_A", "LAB_B"]):
        raise ValueError("Expected reference sourceFields must explicitly select XYZ or Lab")
    # Reuse the inverse's existing finite positive-white validation; no default
    # white, independent inverse, normalization or adaptation belongs here.
    try:
        white = lab_to_xyz((100, 0, 0), value["whiteXYZ"])
    except ColorDomainError as error:
        raise ValueError(f"Expected reference white is invalid: {error}") from error
    if (
        value["observerId"] != "CIE1931_2deg"
        or value["xyzUnits"] != "1"
        or value["xyzScale"] != "relative; perfect-diffuser Y=100"
        or white[1] != 100
    ):
        return value, "Reference illustrations require declared relative Y=100 XYZ and CIE1931_2deg"
    return value, ""


def expected_reference_json(target_json: str, context_json: str = "null") -> str:
    """Project frozen expected XYZ/Lab once for reference illustrations.

    The current TI2 packet is revalidated from its retained bytes. The caller
    explicitly selects XYZ_X/Y/Z or LAB_L/A/B and declares a common positive
    white XYZ, CIE1931_2deg observer, dimensionless relative Y=100 scale,
    illuminant, interpretation and meaning. These declarations are retained;
    they do not prove specimen conditions or qualify a position warning.
    Each of the six context text fields is limited to 2048 UTF-16 code units,
    matching the MATLAB consumer; a supplementary character occupies two.

    XYZ is retained exactly. Lab uses the established science.lab_to_xyz
    inverse of CIE S 017:2020 e-ILV 17-23-076, in the declared white's scale:
    https://cie.co.at/eilvterm/17-23-076
    For example, (L*, a*, b*)=(100, 0, 0) returns the supplied white XYZ.
    Neither printer channels, ACCURATE_EXPECTED_VALUES nor numerical ranges
    supply missing context. No default white, clipping, adaptation, rendering
    or acquisition occurs. Missing selected columns or conversion overflow
    leave that source row explicitly unavailable. The shared strict CGATS
    reader refuses nonnumerical standard XYZ/Lab columns before interpretation.

    The result keeps every original source-row identity/location in source
    order, selected original fields/values, context and source digest. The raw
    packet is unchanged. Subsequent rendering uses this retained projection
    without another Python call or access to the original TI2 file.
    """
    target = json.loads(validate_target_json(target_json))
    context, context_reason = _expected_context(context_json)
    selected = context.get("sourceFields", [])
    positions = [target["fields"].index(field) for field in selected if field in target["fields"]]
    complete = len(positions) == 3
    cells = []
    for row in target["rows"]:
        originals = [row["values"][position] for position in positions]
        reason = context_reason
        xyz = []
        if not reason and not complete:
            reason = "The selected expected XYZ/Lab columns are missing or incomplete"
        if not reason:
            try:
                xyz = (
                    list(lab_to_xyz(originals, context["whiteXYZ"]))
                    if selected[0] == "LAB_L"
                    else list(originals)
                )
            except ColorDomainError as error:
                reason = f"Expected Lab conversion unavailable: {error}"
        cells.append(
            dict(
                source_row=row["source_row"],
                sample_id=row["sample_id"],
                sample_loc=row["sample_loc"],
                available=not reason,
                XYZ=xyz,
                originalFields=[target["fields"][position] for position in positions],
                originalValues=originals,
                reason=reason,
            )
        )
    return _json(
        dict(schema_version=1, source_sha256=target["source_sha256"], context=context, cells=cells)
    )
