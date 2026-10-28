"""Colorimeter corrections: ``.ccmx`` matrices and ``.ccss`` spectral sets.

Why this exists
---------------
A colorimeter measures through three fixed filters. Those filters approximate the
CIE colour matching functions, and the approximation error depends on the
*spectrum* of what is being measured -- so the same instrument reading the same
visual colour on a CCFL LCD and on an RGB-LED LCD returns two different XYZ
values. ArgyllCMS carries two corrections for this, and an instrument advertises
which it accepts through ``inst2_ccmx`` / ``inst2_ccss`` (``inst.h:362-363``):

``.ccmx`` -- Colorimeter Correction Matrix
    A 3x3 matrix applied to the instrument's XYZ, derived by measuring one
    display with both the colorimeter and a reference spectrometer.
    Applied by ``col_cor_mat()`` (``inst.h:1102``).

``.ccss`` -- Colorimeter Calibration Spectral Set
    Sample spectra of the display, from which the instrument recomputes its own
    calibration. Richer than a matrix and only some instruments accept it.
    Applied by ``col_cal_spec_set()`` (``inst.h:1115``).

Without a supplied correction, the driver uses its factory/display-type
calibration. A display-specific correction may reduce spectral mismatch;
its absence alone does not make reported XYZ invalid.

Both formats are CGATS; ``cgats.py`` is the format layer and this module is the
semantics. Field and keyword requirements are transcribed from
``xicc/ccmx.c:191-310`` (``read_ccmx_cgats``) and ``xicc/ccss.c``
(``read_ccss_cgats``).

One deliberate divergence from upstream
---------------------------------------
``spotread.c:1680-1745`` takes a single ``-X file`` and discovers its kind by
*attempting* ``read_ccmx`` and falling back to ``read_ccss`` when that fails.
That is a consequence of its API having no cheap probe, and it reports a CCSS
parse error for a malformed CCMX. ``read_correction()`` below dispatches on the
file's CGATS identifier instead, which accepts exactly the same files and names
the actual fault. Both per-kind readers remain available and strict.

Units
-----
XYZ is in the instrument's own emissive units (cd/m^2 for Y, absolute) or
relative units for reflective work. Display corrections usually preserve this
scale; an ambient conversion matrix requires its own quantity context.
Spectral samples are in nm against a
``norm`` scale factor, exactly as written in the file.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .argyll_capability import display_technology
from .cgats import _INTEGER, CgatsError, CgatsTable, _real_number, read_cgats

__all__ = [
    "CalibrationSpectralSet",
    "CorrectionMatrix",
    "CorrectionError",
    "correction_material",
    "correction_material_json",
    "parse_ccmx",
    "parse_ccss",
    "parse_correction",
    "read_ccmx",
    "read_ccss",
    "read_correction",
]

# xspect.spec is a fixed double[601] (`xicc/xspect.h:81,:88`). Upstream's
# read_ccss_cgats indexes a spi[XSPECT_MAX_BANDS] with SPECTRAL_BANDS taken
# straight from the file, so a larger count overruns that array upstream. We
# refuse it: the instrument API cannot carry more than 601 bands either.
XSPECT_MAX_BANDS = 601

# `col_cor_mat()` documents "Calibration display type base ID, needed 1 if
# unknown" (`inst.h:1105`), while ccmx.c:259-263 stores 0 when the file carries
# no DISPLAY_TYPE_BASE_ID. The translation from "file said nothing" to "tell the
# instrument 1" belongs here, not in the ladder.
CBID_UNKNOWN = 0
CBID_ANY = 1


class CorrectionError(ValueError):
    """A correction that is malformed or unsafe for this application's numeric API."""


def _required(table: CgatsTable, keyword: str, kind: str) -> str:
    value = table.keywords.get(keyword)
    if value is None:
        raise CorrectionError(f"read_{kind}: Input file doesn't contain keyword {keyword}")
    return value


def _yes_no(table: CgatsTable, keyword: str) -> bool | None:
    """A YES/NO keyword as a tristate. ``None`` is upstream's -1 "unknown"."""
    value = table.keywords.get(keyword)
    if value is None:
        return None
    # stricmp upstream (ccmx.c:252-258); anything else leaves it unknown.
    if value.upper() == "YES":
        return True
    if value.upper() == "NO":
        return False
    return None


def _single_table(text: str, identifier: str) -> CgatsTable:
    tables = read_cgats(text, expected_identifier=identifier)
    if any(len(set(table.fields)) != len(table.fields) for table in tables):
        raise CorrectionError("Correction data fields must be unique")
    if len(tables) != 1:
        raise CorrectionError("Input file doesn't contain exactly one table")
    return tables[0]


@dataclass(frozen=True)
class CorrectionMatrix:
    """A ``.ccmx``: a 3x3 correction for one colorimeter on one display type."""

    matrix: tuple[tuple[float, float, float],
                  tuple[float, float, float],
                  tuple[float, float, float]]
    instrument: str
    description: str | None = None
    display: str | None = None
    technology: str | None = None
    base_id: int = CBID_UNKNOWN
    refresh_mode: bool | None = None
    selectors: str | None = None
    reference: str | None = None
    oem: bool = False

    def __post_init__(self):
        if len(self.matrix) != 3 or any(len(row) != 3 for row in self.matrix):
            raise CorrectionError("A correction matrix has exactly three rows and columns")
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for row in self.matrix for value in row):
            raise CorrectionError("Correction matrix values must be finite numbers")
        if type(self.base_id) is not int or not 0 <= self.base_id <= 0x7fffffff:
            raise CorrectionError("Invalid calibration base ID")

    @property
    def display_technology(self) -> int:
        """The ``disptech`` enumeration ``col_cor_mat()`` is given."""
        return display_technology(self.technology)

    @property
    def calibration_base_id(self) -> int:
        """``cbid`` as the instrument wants it: 1 when the file did not say.

        ``inst.h:1105`` -- "Calibration display type base ID, needed 1 if
        unknown". The file's own absent value is 0 (``ccmx.c:262``).
        """
        return self.base_id if self.base_id != CBID_UNKNOWN else CBID_ANY

    def apply(self, xyz: Sequence[float]) -> tuple[float, float, float]:
        """Correct one XYZ triple.

        Equation, from ``icmMulBy3x3`` (``icc/icc_util.c:205-211``), which is
        what ``ccmx.c:391-397`` calls::

            out[i] = sum over j of matrix[i][j] * in[j]

        This numeric operation does not infer units. Display corrections usually
        preserve them; an ambient conversion requires its own quantity context.

        Limitation: a matrix is valid only for the instrument named by
        ``instrument`` on the display it was made for. Applying another
        instrument's matrix produces a plausible and wrong number; the caller
        must check, because nothing in the file prevents it.
        """
        if len(xyz) != 3:
            raise CorrectionError(f"A correction applies to an XYZ triple, got {len(xyz)} values")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in xyz):
            raise CorrectionError("XYZ values must be finite numbers")
        result = tuple(
            sum(self.matrix[row][column] * xyz[column] for column in range(3))
            for row in range(3)
        )
        if not all(math.isfinite(value) for value in result):
            raise CorrectionError("Corrected XYZ overflowed")
        return result


def _validate_spectral_grid(bands: int, start_nm: float, end_nm: float, norm: float) -> None:
    """Validate before reconstructing column names or using Argyll interpolation."""
    if type(bands) is not int or not 2 <= bands <= XSPECT_MAX_BANDS:
        raise CorrectionError("Invalid calibration spectral band count")
    try:
        valid_grid = (all(type(value) in (int, float) and math.isfinite(value)
                          for value in (start_nm, end_nm, norm))
                      and start_nm > 0 and end_nm > start_nm and norm > 0)
    except OverflowError:
        valid_grid = False
    if not valid_grid:
        raise CorrectionError("Invalid calibration spectral grid or normalization")


@dataclass(frozen=True)
class CalibrationSpectralSet:
    """A ``.ccss``: display sample spectra an instrument recalibrates against."""

    samples: tuple[tuple[float, ...], ...]
    bands: int
    start_nm: float
    end_nm: float
    norm: float = 1.0
    description: str | None = None
    originator: str | None = None
    created: str | None = None
    display: str | None = None
    technology: str | None = None
    refresh_mode: bool | None = None
    selectors: str | None = None
    reference: str | None = None
    oem: bool = False

    def __post_init__(self):
        _validate_spectral_grid(self.bands, self.start_nm, self.end_nm, self.norm)
        if (not 3 <= len(self.samples) <= 1000
                or any(len(row) != self.bands for row in self.samples)):
            raise CorrectionError("Calibration spectra require 3 to 1000 complete samples")
        try:
            valid_samples = all(type(value) in (int, float) and math.isfinite(value)
                                and math.isfinite(value / self.norm)
                                for row in self.samples for value in row)
        except OverflowError:
            valid_samples = False
        if not valid_samples:
            raise CorrectionError("Calibration spectral samples and normalized values "
                                  "must be finite numbers")

    @property
    def display_technology(self) -> int:
        """The ``disptech`` enumeration ``col_cal_spec_set()`` is given."""
        return display_technology(self.technology)

    def wavelengths_nm(self) -> tuple[float, ...]:
        """Band centres, evenly spaced from ``start_nm`` to ``end_nm``.

        This is ``XSPECT_SET_INFO``'s implied grid: upstream stores only the two
        endpoints and the count (``xspect.h:83-88``) and every consumer derives
        the rest, as ``read_ccss_cgats`` itself does when it rebuilds the field
        names.
        """
        span = self.end_nm - self.start_nm
        return tuple(
            self.start_nm + (index / (self.bands - 1.0)) * span for index in range(self.bands)
        )


def correction_material(correction: CorrectionMatrix | CalibrationSpectralSet) -> dict:
    """The parsed numerical payload and identifiers sent to the native API.

    This is also the projection used by ``instrument._correction_arguments``.
    It excludes source spelling, descriptions and other documentary metadata;
    clients retain those separately from material configuration comparisons.
    Spectra keep their grid, normalization and every sample exactly as supplied.
    Comparing this payload does not establish physical calibration equivalence
    or suitability for an instrument/display.
    """
    if isinstance(correction, CorrectionMatrix):
        return {
            "kind": "matrix",
            "matrix": [[float(value) for value in row] for row in correction.matrix],
            "display_technology": correction.display_technology,
            "calibration_base_id": correction.calibration_base_id,
        }
    if isinstance(correction, CalibrationSpectralSet):
        return {
            "kind": "spectral",
            "bands": correction.bands,
            "start_nm": float(correction.start_nm),
            "end_nm": float(correction.end_nm),
            "norm": float(correction.norm),
            "samples": [[float(value) for value in row] for row in correction.samples],
            "display_technology": correction.display_technology,
        }
    raise CorrectionError("Material correction requires a parsed matrix or spectral set")


def correction_material_json(text: str) -> str:
    """Validate correction text and return its native payload as inert JSON.

    This uses the existing CCMX/CCSS parser; it reads no file, opens no instrument
    and creates no worker. Raw request text and any source digest remain the
    caller's provenance, separate from this parsed material projection.
    """
    if not isinstance(text, str):
        raise CorrectionError("Correction text must be a string")
    return json.dumps(correction_material(parse_correction(text)),
                      allow_nan=False, sort_keys=True, separators=(",", ":"))


def _shared_display_keywords(table: CgatsTable, kind: str) -> dict[str, object]:
    """The keywords ccmx and ccss read identically."""
    display = table.keywords.get("DISPLAY")
    technology = table.keywords.get("TECHNOLOGY")
    if display is None and technology is None:
        raise CorrectionError(
            f"read_{kind}: Input file doesn't contain keyword DISPLAY or TECHNOLOGY"
        )
    return {
        "description": table.keywords.get("DESCRIPTOR"),
        "display": display,
        "technology": technology,
        "refresh_mode": _yes_no(table, "DISPLAY_TYPE_REFRESH"),
        "selectors": table.keywords.get("UI_SELECTORS"),
        "reference": table.keywords.get("REFERENCE"),
        "oem": _yes_no(table, "OEM") is True,
    }


def _parse_ccmx(text: str) -> CorrectionMatrix:
    table = _single_table(text, "CCMX")

    color_rep = _required(table, "COLOR_REP", "ccmx")
    if color_rep != "XYZ":
        raise CorrectionError("read_ccmx: Input file doesn't have COLOR_REP of XYZ")

    instrument = _required(table, "INSTRUMENT", "ccmx")
    shared = _shared_display_keywords(table, "ccmx")

    base_id = CBID_UNKNOWN
    if (raw := table.keywords.get("DISPLAY_TYPE_BASE_ID")) is not None:
        # atoi() upstream (ccmx.c:261): a non-numeric value reads as 0, which is
        # the same "not known" the absent keyword produces.
        try:
            base_id = int(raw)
        except ValueError:
            base_id = CBID_UNKNOWN

    if len(table.rows) != 3:
        raise CorrectionError("read_ccmx: Input file doesn't have exactly 3 sets")
    # .reals() enforces the r_t field type ccmx.c:293-296 requires.
    columns = [table.reals(name) for name in ("XYZ_X", "XYZ_Y", "XYZ_Z")]
    matrix = tuple(tuple(column[row] for column in columns) for row in range(3))

    return CorrectionMatrix(
        matrix=matrix,  # type: ignore[arg-type]
        instrument=instrument,
        base_id=base_id,
        **shared,  # type: ignore[arg-type]
    )


def _parse_ccss(text: str) -> CalibrationSpectralSet:
    table = _single_table(text, "CCSS")
    shared = _shared_display_keywords(table, "ccss")

    bands = _integer(_required(table, "SPECTRAL_BANDS", "ccss"), "SPECTRAL_BANDS")
    # getval_raw_xspec divides by n-1, and interpolation needs neighbouring
    # samples (xspect.c:4347-4357). A single-band set is not usable calibration.
    if bands < 2:
        raise CorrectionError(f"SPECTRAL_BANDS must be at least 2, got {bands}")
    if bands > XSPECT_MAX_BANDS:
        raise CorrectionError(
            f"SPECTRAL_BANDS is {bands}, above the {XSPECT_MAX_BANDS} an xspect holds"
        )
    start_nm = _number(_required(table, "SPECTRAL_START_NM", "ccss"), "SPECTRAL_START_NM")
    end_nm = _number(_required(table, "SPECTRAL_END_NM", "ccss"), "SPECTRAL_END_NM")
    # "Older versions don't have SPECTRAL_NORM" (ccss.c) -- absent means 1.0.
    norm = 1.0
    if (raw := table.keywords.get("SPECTRAL_NORM")) is not None:
        norm = _number(raw, "SPECTRAL_NORM")
        # A normalising scale is a DIVISOR downstream, not a label: every consumer
        # divides by it (`getval_xspec`, `xspect.c:4380-4382`, `*rv /= sp->norm`),
        # reached from `i1d3_comp_calmat` (`i1d3.c:2564,:2575`) by way of
        # `xsp2cie_sconvert` (`xspect.c:5988`). A zero or negative norm therefore
        # does not produce a scaled correction, it produces one made of infinities
        # or sign-flipped nonsense, and nothing downstream checks.
        if norm <= 0.0:
            raise CorrectionError(f"SPECTRAL_NORM must be positive, got {norm}")

    _validate_spectral_grid(bands, start_nm, end_nm, norm)

    # Field names are rebuilt, never read from the format line: upstream computes
    # the nearest integer nm per band and looks each one up by name.
    names = []
    for index in range(bands):
        nanometres = int(
            start_nm + (index / (bands - 1.0)) * (end_nm - start_nm) + 0.5
        )
        names.append(f"SPEC_{nanometres:03d}")

    # numbers(), not reals(): SPEC_nnn is not a standard CGATS field, so a column
    # written without decimal points types as int, and upstream's reader does not
    # type-check it at all.
    columns = [table.numbers(name) for name in names]

    if len(table.rows) < 3:
        raise CorrectionError("Input file doesn't contain at least three spectral samples")
    samples = tuple(
        tuple(column[row] for column in columns) for row in range(len(table.rows))
    )

    return CalibrationSpectralSet(
        samples=samples,
        bands=bands,
        start_nm=start_nm,
        end_nm=end_nm,
        norm=norm,
        originator=table.keywords.get("ORIGINATOR"),
        created=table.keywords.get("CREATED"),
        **shared,  # type: ignore[arg-type]
    )


def parse_ccmx(text: str) -> CorrectionMatrix:
    """Parse ``.ccmx`` text. Mirrors ``read_ccmx_cgats`` (``ccmx.c:191-310``).

    A malformed CGATS file and a well-formed file that is not a usable correction
    both raise ``CorrectionError``. Upstream has one error channel for the pair --
    ``read_ccmx`` returns nonzero with a message whether ``cgats`` or ``ccmx``
    produced it (``ccmx.c:333-341``) -- and splitting it here would let a caller
    that catches the documented error miss half the ways a file can be unusable.
    The original is kept as ``__cause__``.
    """
    return _wrapped(_parse_ccmx, text)


def parse_ccss(text: str) -> CalibrationSpectralSet:
    """Parse ``.ccss`` text. Mirrors ``read_ccss_cgats`` (``ccss.c``).

    Raises ``CorrectionError`` for a malformed file as well; see ``parse_ccmx``.
    """
    return _wrapped(_parse_ccss, text)


def _wrapped(parse, text: str):
    try:
        return parse(text)
    except CgatsError as error:
        raise CorrectionError(str(error)) from error


def _integer(value: str, keyword: str) -> int:
    # ASCII digits only, as the cell scanner requires: `int` also takes "0_4" and
    # non-ASCII digits, which upstream's atoi reads as 0.
    if _INTEGER.match(value) is None:
        raise CorrectionError(f"{keyword} is not an integer: {value!r}")
    try:
        return int(value)
    except ValueError:
        raise CorrectionError(f"{keyword} is not an integer: {value!r}") from None


def _number(value: str, keyword: str) -> float:
    try:
        return _real_number(value)
    except CgatsError as error:
        raise CorrectionError(f"{keyword} is not finite or not a number: {value!r}") from error


def read_ccmx(path: str | Path) -> CorrectionMatrix:
    """Read a ``.ccmx`` file. Raises ``CorrectionError`` for any other kind."""
    return parse_ccmx(Path(path).read_text(encoding="utf-8", errors="replace"))


def read_ccss(path: str | Path) -> CalibrationSpectralSet:
    """Read a ``.ccss`` file. Raises ``CorrectionError`` for any other kind."""
    return parse_ccss(Path(path).read_text(encoding="utf-8", errors="replace"))


def read_correction(path: str | Path) -> CorrectionMatrix | CalibrationSpectralSet:
    """Read a correction file of either kind, dispatching on its identifier.

    This is the entry point matching ``spotread -X file``; see the module
    docstring for why the dispatch differs from upstream's try-and-fall-back.
    """
    return parse_correction(Path(path).read_text(encoding="utf-8", errors="replace"))


def parse_correction(text: str) -> CorrectionMatrix | CalibrationSpectralSet:
    """Parse a correction of either kind from text, dispatching on its identifier.

    Same dispatch as ``read_correction``, for a correction that arrives as content
    rather than as a path -- a client sending one over the frontend's request
    channel has the bytes, not a file this process can open.
    """
    try:
        tables = read_cgats(text)
    except CgatsError as error:
        raise CorrectionError(str(error)) from error
    identifier = tables[0].identifier
    if identifier == "CCMX":
        return parse_ccmx(text)
    if identifier == "CCSS":
        return parse_ccss(text)
    raise CorrectionError(
        f"Not a colorimeter correction file: CGATS identifier is {identifier!r}, "
        "expected 'CCMX' or 'CCSS'"
    )
