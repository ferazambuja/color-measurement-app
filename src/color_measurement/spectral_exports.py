"""Bounded Argyll 3.5.0 SPECT projection with a mandatory canonical companion.

The named consumer is Argyll's ``read_nxspect`` / ``read_xspect`` from
``xicc/xspect.c``. This is spectral interchange, not a target .ti3 file or a
general CGATS importer. No stimulus channels, integration or resampling occur.
Custom CM_* keywords bind project provenance to JSON; Argyll ignores them.
``read_xspect`` reads only the first row. For multiple patches, use
``read_nxspect`` with capacity at least NUMBER_OF_SETS and verify its returned
count; a successful single-spectrum read does not establish complete import.
"""

import hashlib
import math
import re
from pathlib import Path

from .exports import SaveError, SaveResult, _publish_new_file, _read_regular, to_json
from .models import Observation
from .operation_exports import OperationSaveError
from .operation_records import OperationResult, result_to_json
from .ti3_exports import save_companions

# These are the recognized tags in the pinned reader, not upstream's defective
# write_nxspect condition selector. M0 has no explicit recognized tag: omitting
# MEAS_CONDITIONS leaves the reader's incoming condition unchanged, not known M0.
_CONDITIONS = {
    "reflective_spot_m1": (1, "D50", "M1"),
    "reflective_spot_m2": (3, "UVCUT", "M2"),
    "reflective_spot_m3": (4, "POLARIZED", "M3"),
}


def _record_parts(record):
    if isinstance(record, OperationResult):
        return ([patch.observation for patch in record.patches], record.mode,
                record.provenance, record.status, result_to_json(record).encode("utf-8"))
    if isinstance(record, Observation):
        return ([record], record.actual_mode, record.provenance, None,
                to_json(record).encode("utf-8"))
    raise ValueError("SPECT requires a current color observation or terminal operation")


def _save_error(target, record, payload, format, cause):
    return (OperationSaveError(target, record, payload, format, cause)
            if isinstance(record, OperationResult) else
            SaveError(target, record, payload, cause, format=format))


def _projection(result, canonical_name: str, canonical: bytes) -> bytes:
    observations, mode, provenance, status, _ = _record_parts(result)
    if any(not isinstance(observation, Observation) for observation in observations):
        raise ValueError("SPECT export cannot represent scalar or timing measurements")
    if mode not in _CONDITIONS:
        raise ValueError("SPECT export supports reflective M1/M2/M3; M0 has no explicit reader tag")
    if not observations:
        raise ValueError("SPECT export requires acquired spectra; retain zero-row outcomes as JSON")
    condition, tag, label = _CONDITIONS[mode]
    spectra = []
    for observation in observations:
        spectrum = observation.backend_reported.spectrum
        metadata = dict(observation.configuration)
        try:
            norm = float(metadata.get("spectrum_norm", "nan"))
        except ValueError:
            norm = math.nan
        if (
            spectrum is None or spectrum.quantity != "reflectance" or spectrum.unit != "%"
            or metadata.get("native_mtype") != "5"
            or metadata.get("native_mcond") != str(condition) or norm != 100
        ):
            raise ValueError(
                "SPECT requires native reflective percent data and matching condition/norm"
            )
        grid = spectrum.wavelengths_nm
        if not 2 <= len(grid) <= 601 or any(
            wavelength != int(wavelength) or wavelength > 2**31 - 1 for wavelength in grid
        ):
            raise ValueError(
                "SPECT requires 2..601 integer-nm samples within the reader's int range"
            )
        step = grid[1] - grid[0]
        if any(b - a != step for a, b in zip(grid, grid[1:])):
            raise ValueError("SPECT requires a uniform wavelength grid; no resampling is implicit")
        if spectra and grid != spectra[0].wavelengths_nm:
            raise ValueError("SPECT requires the same grid for every retained patch")
        if any(float(value) != value for value in spectrum.values):
            raise ValueError("SPECT values must be exactly representable by the reader's doubles")
        spectra.append(spectrum)
    grid = spectra[0].wavelengths_nm
    lines = [
        "SPECT",
        'DESCRIPTOR "Color Measurement reflective spectral projection"',
        'ORIGINATOR "Color Measurement"',
        'MEAS_TYPE "REFLECTIVE"',
        f'MEAS_CONDITIONS "{tag}"',
        f"SPECTRAL_BANDS {len(grid)}",
        f"SPECTRAL_START_NM {int(grid[0])}",
        f"SPECTRAL_END_NM {int(grid[-1])}",
        "SPECTRAL_NORM 100",
        '# CM_* metadata belongs to this project and is not interpreted by Argyll.',
    ]
    metadata = {
        "CM_CANONICAL_FILE": canonical_name,
        "CM_CANONICAL_SHA256": hashlib.sha256(canonical).hexdigest(),
        "CM_MODE": mode,
        "CM_CONDITION": label,
        "CM_PROVENANCE": provenance,
        "CM_SPECTRAL_QUANTITY": "reflectance",
        "CM_SPECTRAL_UNIT": "%",
        "CM_ROW_ORDER": "canonical patches array; missing patches omitted"
        if isinstance(result, OperationResult) else "single original observation",
    }
    if status is not None:
        metadata["CM_OPERATION_STATUS"] = status
    for key, value in metadata.items():
        lines.extend((f'KEYWORD "{key}"', f'{key} "{value}"'))
    lines.extend((
        f"NUMBER_OF_FIELDS {len(grid)}",
        "BEGIN_DATA_FORMAT",
        " ".join(f"SPEC_{int(wavelength):03d}" for wavelength in grid),
        "END_DATA_FORMAT",
        f"NUMBER_OF_SETS {len(spectra)}",
        "BEGIN_DATA",
    ))
    for spectrum in spectra:
        # The named reader requires real-valued CGATS fields. A column of bare
        # integer tokens (notably all-zero samples) is inferred as integer type.
        tokens = [format(float(value), ".17g") for value in spectrum.values]
        lines.append(" ".join(token if "." in token or "e" in token else token + ".0"
                              for token in tokens))
    lines.extend(("END_DATA", ""))
    return "\n".join(lines).encode("ascii")


def save_sp(result: OperationResult | Observation, path: str | Path, *, canonical_path: str | Path,
            previous_ti3: dict | None = None) -> SaveResult:
    """Save a bounded .sp projection after saving/verifying its JSON companion.

    Both destinations must share a directory. The companion's filename must be
    ASCII letters/digits/dots/underscores/hyphens, starting with a letter/digit;
    this avoids relying on a consumer-specific string escaping convention.
    Existing identical canonical bytes may be reused; conflicting bytes and any
    existing .sp destination are refused. A failed .sp publication can leave the
    complete canonical JSON for recovery: the pair is not an atomic transaction.

    Failed/cancelled operations may project only their retained acquired patches.
    Row order follows the canonical patches array, not expected-patch positions.
    Status, IDs, missing patches, XYZ, calibration and raw text remain in JSON.
    A consumer must verify the companion hash before interpreting project fields.
    M0, emission, irregular/mixed grids and zero-row outcomes are refused.
    Saving or retrying never acquires a measurement. SaveResult.path names the
    SPECT file; SaveResult.ti3 reports the canonical JSON's independent companion
    outcomes, including when identical canonical bytes are reused on retry.
    """
    from .exports import save as save_observation
    from .operation_exports import save as save_operation

    canonical = _record_parts(result)[4]
    target, companion = Path(path), Path(canonical_path)
    if (
        target.parent.resolve() != companion.parent.resolve()
        or target.resolve() == companion.resolve()
    ):
        raise ValueError(
            "SPECT and canonical JSON must have distinct filenames in the same directory"
        )
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", companion.name) is None:
        raise ValueError("Canonical companion filename must use simple ASCII letters/digits/._-")
    payload = _projection(result, companion.name, canonical)
    if target.exists() or target.is_symlink():
        cause = FileExistsError(f"Destination already exists: {target}")
        raise _save_error(target, result, payload, "sp", cause) from cause
    saved = None
    if not companion.exists():
        try:
            writer = save_operation if isinstance(result, OperationResult) else save_observation
            saved = writer(result, companion, previous_ti3=previous_ti3)
        except (OperationSaveError, SaveError) as error:
            # A concurrent equal-companion publication may be reused, but its
            # actual bytes must still pass the check below.
            if not isinstance(error.__cause__, FileExistsError):
                raise
    try:
        existing = _read_regular(companion)
    except OSError as error:
        raise _save_error(companion, result, canonical, "json", error) from error
    if existing != canonical:
        raise ValueError("Existing canonical companion bytes differ from this operation")
    published = _publish_new_file(
        target, payload, lambda cause: _save_error(target, result, payload, "sp", cause)
    )
    ti3 = saved.ti3 if saved else save_companions(result, companion, canonical, previous_ti3)
    return SaveResult(published.path, ti3)
