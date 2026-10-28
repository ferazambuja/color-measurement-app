"""Colorimeter corrections, against Argyll 3.5.0's ``xicc/ccmx.c`` and ``ccss.c``."""

import json
import sys
import zipfile

import pytest

from color_measurement.cgats import CgatsError
from color_measurement.colorimeter_correction import (
    CBID_ANY,
    CalibrationSpectralSet,
    CorrectionError,
    CorrectionMatrix,
    correction_material,
    correction_material_json,
    parse_ccmx,
    parse_ccss,
    read_correction,
)
from color_measurement.instrument import Configuration

# Deliberately asymmetric: a transposed read of this matrix is a different
# matrix, so orientation is testable rather than accidentally right.
MATRIX_ROWS = ("0.80 0.10 0.02", "-0.05 1.10 0.04", "0.01 -0.03 1.05")
MATRIX = ((0.80, 0.10, 0.02), (-0.05, 1.10, 0.04), (0.01, -0.03, 1.05))


def ccmx(keywords='INSTRUMENT "Xrite DTP94"\nDISPLAY "Dell 2408WFP"\nCOLOR_REP "XYZ"\n',
         rows=MATRIX_ROWS):
    return (
        "CCMX\n\n" + keywords + "\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\n"
        "XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n\n"
        f"NUMBER_OF_SETS {len(rows)}\nBEGIN_DATA\n" + "\n".join(rows) + "\nEND_DATA\n"
    )


def ccss(bands=4, start=400.0, end=700.0, samples=3, extra="", norm='SPECTRAL_NORM "100.0"\n'):
    names = " ".join(f"SPEC_{int(start + i / (bands - 1) * (end - start) + 0.5):03d}"
                     for i in range(bands))
    rows = ["  ".join(f"{(sample * bands + band) * 0.25!r}" for band in range(bands))
            for sample in range(samples)]
    return (
        'CCSS\n\nDISPLAY "Color LCD"\nTECHNOLOGY "LCD RG Phosphor IPS"\n'
        f'SPECTRAL_BANDS "{bands}"\nSPECTRAL_START_NM "{start}"\n'
        f'SPECTRAL_END_NM "{end}"\n{norm}{extra}\n'
        f"NUMBER_OF_FIELDS {bands}\nBEGIN_DATA_FORMAT\n{names}\nEND_DATA_FORMAT\n\n"
        f"NUMBER_OF_SETS {samples}\nBEGIN_DATA\n" + "\n".join(rows) + "\nEND_DATA\n"
    )


def test_correction_material_uses_native_identifiers_and_ignores_source_metadata():
    text = ccmx()
    expected = {"kind": "matrix", "matrix": [list(row) for row in MATRIX],
                "display_technology": 0, "calibration_base_id": 1}
    assert json.loads(correction_material_json(text)) == expected
    assert correction_material(parse_ccmx(text)) == expected
    for metadata in (
        '# Operator comment\nDESCRIPTOR "Renamed"\n',
        'INSTRUMENT "Another documentary name"\n',
        'DISPLAY_TYPE_BASE_ID "0"\n', 'DISPLAY_TYPE_BASE_ID "1"\n',
        'DISPLAY_TYPE_REFRESH "YES"\nOEM "YES"\nREFERENCE "Reference label"\n',
        'DISPLAY_TYPE_REFRESH "NO"\n',
    ):
        changed = text.replace('INSTRUMENT "Xrite DTP94"\n', "") if (
            metadata.startswith("INSTRUMENT")) else text
        changed = changed.replace("CCMX\n", "CCMX\n" + metadata, 1)
        assert correction_material_json(changed) == correction_material_json(text)
    assert correction_material_json(text.replace("0.80", "0.8000")) == (
        correction_material_json(text))


@pytest.mark.parametrize("cell", range(9))
def test_correction_material_detects_each_matrix_coefficient(cell):
    rows = [list(row) for row in MATRIX]
    rows[cell // 3][cell % 3] += 0.125
    text = ccmx(rows=[" ".join(map(repr, row)) for row in rows])
    assert correction_material_json(text) != correction_material_json(ccmx())


@pytest.mark.parametrize("keyword", [
    'TECHNOLOGY "LCD RG Phosphor IPS"\n', 'DISPLAY_TYPE_BASE_ID "2"\n',
])
def test_correction_material_detects_native_matrix_identifiers(keyword):
    text = ccmx().replace("CCMX\n", "CCMX\n" + keyword, 1)
    assert correction_material_json(text) != correction_material_json(ccmx())


def test_spectral_material_preserves_every_native_input_without_documentary_text():
    text = ccss()
    expected = {"kind": "spectral", "bands": 4, "start_nm": 400.0,
                "end_nm": 700.0, "norm": 100.0, "display_technology": 0x3510,
                "samples": [[(row * 4 + column) * 0.25 for column in range(4)]
                            for row in range(3)]}
    assert json.loads(correction_material_json(text)) == expected
    assert correction_material(parse_ccss(text)) == expected
    described = ccss(extra='# Comment\nDESCRIPTOR "Renamed"\nORIGINATOR "Edited"\n'
                          'CREATED "Today"\nREFERENCE "Another reference"\nOEM "YES"\n')
    assert correction_material_json(described) == correction_material_json(text)
    assert correction_material_json(text.replace("  ", " \t ")) == (
        correction_material_json(text))


@pytest.mark.parametrize("kind", ["matrix", "spectral"])
@pytest.mark.parametrize("metadata,field,expected", [
    ('DESCRIPTOR "Renamed"\n', "description", "Renamed"),
    ('DISPLAY_TYPE_REFRESH "YES"\n', "refresh_mode", True),
    ('DISPLAY_TYPE_REFRESH "NO"\n', "refresh_mode", False),
])
def test_material_ignores_actual_parsed_documentary_fields(kind, metadata, field, expected):
    text, parser = (ccmx(), parse_ccmx) if kind == "matrix" else (ccss(), parse_ccss)
    changed = text.replace("\n", "\n" + metadata, 1)
    assert getattr(parser(text), field) is None
    assert getattr(parser(changed), field) == expected
    assert correction_material_json(changed) == correction_material_json(text)


@pytest.mark.parametrize("cell", range(12))
def test_correction_material_detects_every_spectral_set_and_sample(cell):
    text = ccss()
    header, data = text.split("BEGIN_DATA\n")
    values = data.split("\nEND_DATA")[0].split()
    values[cell] = repr(float(values[cell]) + 0.125)
    changed = header + "BEGIN_DATA\n" + "\n".join(
        " ".join(values[row * 4:(row + 1) * 4]) for row in range(3)) + "\nEND_DATA\n"
    assert correction_material_json(changed) != correction_material_json(text)


@pytest.mark.parametrize("changes", [
    {"bands": 5}, {"start": 410.0}, {"end": 710.0}, {"samples": 4},
    {"norm": 'SPECTRAL_NORM "1.0"\n'},
])
def test_correction_material_detects_spectral_grid_normalization_and_set_count(changes):
    assert correction_material_json(ccss(**changes)) != correction_material_json(ccss())


def test_correction_material_detects_spectral_technology():
    changed = ccss().replace('TECHNOLOGY "LCD RG Phosphor IPS"', 'TECHNOLOGY "Unknown"')
    assert correction_material_json(changed) != correction_material_json(ccss())


@pytest.mark.parametrize("bad", [None, False, 1, {}, "CCSS\n", "CCMX\n"])
def test_correction_material_json_refuses_unusable_input(bad):
    with pytest.raises(CorrectionError):
        correction_material_json(bad)


@pytest.mark.parametrize("norm", [1e-320, 5e-324])
def test_subnormal_normalization_with_finite_ratios_is_accepted(norm):
    from color_measurement.colorimeter_correction import CalibrationSpectralSet
    samples = ((0.0, norm, -norm, norm),) * 3
    correction = CalibrationSpectralSet(samples, 4, 400.0, 700.0, norm)
    assert correction.samples == samples
    text = ccss(norm=f'SPECTRAL_NORM "{norm}"\n')
    text = text.split("BEGIN_DATA\n")[0] + "BEGIN_DATA\n" + (
        f"0.0 {norm} {-norm} {norm}\n" * 3) + "END_DATA\n"
    parsed = parse_ccss(text)
    assert parsed.samples == samples and parsed.norm == correction.norm


@pytest.mark.parametrize("sample", [1.0, -1.0, 10**400])
def test_normalized_spectral_overflow_is_refused_before_native_attachment(sample):
    from color_measurement.colorimeter_correction import CalibrationSpectralSet
    with pytest.raises(CorrectionError, match="normalized values.*finite"):
        CalibrationSpectralSet(((sample,) * 4,) * 3, 4, 400.0, 700.0, 1e-320)


@pytest.mark.parametrize("original, literal, message", [
    ('SPECTRAL_START_NM "400.0"', 'SPECTRAL_START_NM "4_00"', "SPECTRAL_START_NM is not finite"),
    ('SPECTRAL_START_NM "400.0"', 'SPECTRAL_START_NM "٤٠٠"',
     "SPECTRAL_START_NM is not finite"),
    ('SPECTRAL_NORM "100.0"', 'SPECTRAL_NORM "1_0"', "SPECTRAL_NORM is not finite"),
    ('SPECTRAL_BANDS "4"', 'SPECTRAL_BANDS "0_4"', "SPECTRAL_BANDS is not an integer"),
    ('SPECTRAL_BANDS "4"', 'SPECTRAL_BANDS "٤"', "SPECTRAL_BANDS is not an integer"),
    ("NUMBER_OF_SETS 3", "NUMBER_OF_SETS ٣", "NUMBER_OF_SETS is not an integer"),
])
def test_keyword_numbers_take_only_the_ascii_grammar_the_cells_take(original, literal, message):
    """Python's `float`/`int` read "4_00" as 400 and Arabic-Indic digits as digits;
    Argyll's atof/atoi read 4 and 0. A file that is a different correction in
    Argyll than here must be refused, not reinterpreted.
    The data cells were already immune through the cell scanner."""
    text = ccss()
    assert text.count(original) == 1
    with pytest.raises(CorrectionError, match=message):
        parse_ccss(text.replace(original, literal))
    parse_ccss(text)  # the same file with the ASCII literal is the control


def test_ccss_body_and_header_refuse_nonzero_underflow_consistently():
    with pytest.raises(CorrectionError, match="not finite"):
        parse_ccss(ccss(norm='SPECTRAL_NORM "1e-999"\n'))
    text = ccss().replace("BEGIN_DATA\n0.0", "BEGIN_DATA\n1e-999")
    with pytest.raises(CorrectionError, match="underflows"):
        parse_ccss(text)


def test_integer_spectral_overflow_remains_a_correction_refusal():
    text = (ccss().split("BEGIN_DATA\n", 1)[0] + "BEGIN_DATA\n" + "9" * 310
            + " 1 2 3\n4 5 6 7\n8 9 10 11\nEND_DATA\n")
    with pytest.raises(CorrectionError, match="exceeds binary64 range") as caught:
        parse_ccss(text)
    assert isinstance(caught.value.__cause__, CgatsError)
    with pytest.raises(ValueError, match="Not a usable colorimeter correction") as caller:
        Configuration.from_options({"correction": {"text": text}})
    assert isinstance(caller.value.__cause__, CorrectionError)


def test_integer_spectral_digit_limit_remains_a_correction_refusal():
    limit = sys.get_int_max_str_digits()
    if not limit:
        pytest.skip("Interpreter integer digit limit is disabled")
    text = (ccss().split("BEGIN_DATA\n", 1)[0] + "BEGIN_DATA\n" + "9" * (limit + 1)
            + " 1 2 3\n4 5 6 7\n8 9 10 11\nEND_DATA\n")
    with pytest.raises(CorrectionError, match="Invalid integer number") as caught:
        parse_ccss(text)
    assert isinstance(caught.value.__cause__, CgatsError)
    with pytest.raises(ValueError, match="Not a usable colorimeter correction") as caller:
        Configuration.from_options({"correction": {"text": text}})
    assert isinstance(caller.value.__cause__, CorrectionError)


@pytest.mark.parametrize("start,end", [(-1e308, 1e308), (0, 700), (700, 400)])
def test_invalid_grid_is_refused_before_spectral_field_names_are_computed(start, end):
    text = ccss().replace('SPECTRAL_START_NM "400.0"', f'SPECTRAL_START_NM "{start}"')
    text = text.replace('SPECTRAL_END_NM "700.0"', f'SPECTRAL_END_NM "{end}"')
    with pytest.raises(CorrectionError, match="Invalid calibration spectral grid"):
        parse_ccss(text)
    with pytest.raises(ValueError, match="Not a usable colorimeter correction") as caller:
        Configuration.from_options({"correction": {"text": text}})
    assert isinstance(caller.value.__cause__, CorrectionError)


# -- .ccmx ------------------------------------------------------------------

def test_each_data_row_is_a_matrix_row():
    """ccmx.c:303-307 assigns matrix[ix][0..2] from set ix, so a row is a row."""
    assert parse_ccmx(ccmx()).matrix == MATRIX


def test_apply_multiplies_the_matrix_by_the_xyz_column():
    """icmMulBy3x3 (icc_util.c:205-211): out[i] = sum_j matrix[i][j] * in[j]."""
    corrected = parse_ccmx(ccmx()).apply((10.0, 20.0, 30.0))
    assert corrected == pytest.approx((
        0.80 * 10 + 0.10 * 20 + 0.02 * 30,
        -0.05 * 10 + 1.10 * 20 + 0.04 * 30,
        0.01 * 10 - 0.03 * 20 + 1.05 * 30,
    ))


def test_an_identity_matrix_leaves_xyz_untouched():
    identity = parse_ccmx(ccmx(rows=("1 0 0", "0 1 0", "0 0 1")))
    assert identity.apply((4.5, 6.25, 8.0)) == (4.5, 6.25, 8.0)


def test_applying_to_anything_but_a_triple_is_refused():
    with pytest.raises(CorrectionError, match="XYZ triple"):
        parse_ccmx(ccmx()).apply((1.0, 2.0))


def test_the_instrument_keyword_is_required():
    with pytest.raises(CorrectionError, match="doesn't contain keyword INSTRUMENT"):
        parse_ccmx(ccmx(keywords='DISPLAY "d"\nCOLOR_REP "XYZ"\n'))


def test_a_colour_representation_other_than_xyz_is_refused():
    with pytest.raises(CorrectionError, match="COLOR_REP of XYZ"):
        parse_ccmx(ccmx(keywords='INSTRUMENT "i"\nDISPLAY "d"\nCOLOR_REP "LAB"\n'))


def test_a_missing_colour_representation_is_refused():
    with pytest.raises(CorrectionError, match="doesn't contain keyword COLOR_REP"):
        parse_ccmx(ccmx(keywords='INSTRUMENT "i"\nDISPLAY "d"\n'))


def test_either_a_display_or_a_technology_identifies_the_target():
    """ccmx.c:247-251 requires one of the two, not both."""
    only_tech = parse_ccmx(ccmx(
        keywords='INSTRUMENT "i"\nTECHNOLOGY "LCD RG Phosphor IPS"\nCOLOR_REP "XYZ"\n'))
    assert only_tech.display is None
    assert only_tech.display_technology == 0x3510
    with pytest.raises(CorrectionError, match="keyword DISPLAY or TECHNOLOGY"):
        parse_ccmx(ccmx(keywords='INSTRUMENT "i"\nCOLOR_REP "XYZ"\n'))


def test_an_unrecognised_technology_reads_as_unknown_rather_than_failing():
    """disptechs.c:140 returns the disptech_unknown entry when nothing matches."""
    parsed = parse_ccmx(ccmx(
        keywords='INSTRUMENT "i"\nTECHNOLOGY "Holographic Vapour"\nCOLOR_REP "XYZ"\n'))
    assert parsed.technology == "Holographic Vapour"
    assert parsed.display_technology == 0x0000


def test_a_file_without_a_base_id_asks_the_instrument_for_any():
    """The file's own "not known" is 0 (ccmx.c:262); inst.h:1105 wants 1."""
    parsed = parse_ccmx(ccmx())
    assert parsed.base_id == 0
    assert parsed.calibration_base_id == CBID_ANY == 1


def test_a_declared_base_id_is_passed_through():
    parsed = parse_ccmx(ccmx(
        keywords='INSTRUMENT "i"\nDISPLAY "d"\nCOLOR_REP "XYZ"\nDISPLAY_TYPE_BASE_ID "3"\n'))
    assert (parsed.base_id, parsed.calibration_base_id) == (3, 3)


@pytest.mark.parametrize("declared,expected", [("YES", True), ("NO", False),
                                               ("no", False), ("maybe", None)])
def test_refresh_mode_is_a_tristate_with_unknown_preserved(declared, expected):
    """ccmx.c:252-259 leaves refrmode at -1 for anything but YES/NO."""
    parsed = parse_ccmx(ccmx(
        keywords='INSTRUMENT "i"\nDISPLAY "d"\nCOLOR_REP "XYZ"\n'
                 f'DISPLAY_TYPE_REFRESH "{declared}"\n'))
    assert parsed.refresh_mode is expected


def test_a_matrix_must_have_exactly_three_rows():
    with pytest.raises(CorrectionError, match="exactly 3 sets"):
        parse_ccmx(ccmx(rows=MATRIX_ROWS[:2]))


def test_a_non_real_matrix_column_is_refused():
    """ccmx.c:293-296 checks the field type before reading it."""
    with pytest.raises(CorrectionError, match="unexpected type"):
        parse_ccmx(ccmx(rows=("a 0.1 0.2", "0.1 a 0.2", "0.1 0.2 a")))


# -- .ccss ------------------------------------------------------------------

def test_a_spectral_set_reads_its_grid_and_samples():
    parsed = parse_ccss(ccss())
    assert (parsed.bands, parsed.start_nm, parsed.end_nm, parsed.norm) == (4, 400.0, 700.0, 100.0)
    assert parsed.samples[0] == (0.0, 0.25, 0.5, 0.75)
    assert parsed.samples[2] == (2.0, 2.25, 2.5, 2.75)
    assert parsed.display_technology == 0x3510


def test_band_centres_are_evenly_spaced_between_the_endpoints():
    assert parse_ccss(ccss()).wavelengths_nm() == (400.0, 500.0, 600.0, 700.0)


def test_an_absent_normalisation_defaults_to_one():
    """"Older versions don't have SPECTRAL_NORM" -- ccss.c."""
    assert parse_ccss(ccss(norm="")).norm == 1.0


@pytest.mark.parametrize("keyword", ["SPECTRAL_BANDS", "SPECTRAL_START_NM", "SPECTRAL_END_NM"])
def test_each_grid_keyword_is_required(keyword):
    text = "\n".join(line for line in ccss().splitlines() if not line.startswith(keyword))
    with pytest.raises(CorrectionError, match=f"doesn't contain keyword {keyword}"):
        parse_ccss(text + "\n")


def test_fewer_than_three_samples_is_refused():
    """ccss.c refuses: three is the minimum a correction can be fitted from."""
    with pytest.raises(CorrectionError, match="at least three spectral samples"):
        parse_ccss(ccss(samples=2))


def test_more_bands_than_an_xspect_holds_is_refused():
    """xspect.spec is a fixed double[601] (xspect.h:81,:88); upstream's reader
    indexes spi[XSPECT_MAX_BANDS] with the file's own count and would overrun."""
    with pytest.raises(CorrectionError, match="above the 601"):
        parse_ccss(ccss().replace('SPECTRAL_BANDS "4"', 'SPECTRAL_BANDS "602"'))


def test_a_band_the_format_line_does_not_carry_is_refused():
    """Field names are rebuilt from the grid, so a grid that disagrees fails."""
    with pytest.raises(CorrectionError, match="does not contain field SPEC_"):
        parse_ccss(ccss().replace("SPEC_500", "SPEC_501"))


def test_a_non_finite_grid_value_is_refused():
    with pytest.raises(CorrectionError, match="not finite"):
        parse_ccss(ccss().replace('SPECTRAL_END_NM "700.0"', 'SPECTRAL_END_NM "inf"'))


def test_an_integer_spectral_column_still_reads():
    """SPEC_nnn is not a standard CGATS field, so an all-integer column types as
    int; upstream's reader does not type-check it, so neither may this one."""
    text = ccss()
    body = text.split("BEGIN_DATA\n")[1].split("END_DATA")[0]
    integers = "\n".join(" ".join(str(int(float(v))) for v in line.split())
                         for line in body.strip().splitlines())
    parsed = parse_ccss(text.replace(body.strip(), integers))
    assert parsed.samples[1] == (1.0, 1.0, 1.0, 1.0)


# -- dispatch ---------------------------------------------------------------

def test_read_correction_dispatches_on_the_identifier(tmp_path):
    matrix = tmp_path / "a.ccmx"
    matrix.write_text(ccmx())
    spectra = tmp_path / "b.ccss"
    spectra.write_text(ccss())
    assert isinstance(read_correction(matrix), CorrectionMatrix)
    assert isinstance(read_correction(spectra), CalibrationSpectralSet)


def test_read_correction_names_the_kind_it_was_given(tmp_path):
    other = tmp_path / "chart.ti3"
    other.write_text("CTI3\nNUMBER_OF_FIELDS 1\nBEGIN_DATA_FORMAT\nXYZ_X\n"
                     "END_DATA_FORMAT\nNUMBER_OF_SETS 1\nBEGIN_DATA\n1.0\nEND_DATA\n")
    with pytest.raises(CorrectionError, match="identifier is 'CTI3'"):
        read_correction(other)


def test_a_malformed_file_raises_the_documented_correction_error(tmp_path):
    """One error channel: a CGATS fault must not escape as a different class, or a
    caller catching the documented error misses half the ways a file is unusable.

    Both layers are covered deliberately. `read_correction` reads the file once to
    dispatch on its identifier, so a file too broken to have one is refused THERE,
    before `parse_ccmx` is reached -- the earlier version of this test used only
    such files, so it never exercised `_wrapped` at all and could not have told
    the two layers apart.
    """
    for name, body, expected in (
        # Refused by read_correction's own dispatch read.
        ("empty.ccmx", "", "Empty file"),
        ("quote.ccmx", ccmx(keywords='INSTRUMENT "unclosed\n'), "Unterminated"),
        # Well-formed CGATS, so dispatch succeeds and _wrapped is what converts.
        ("torn.ccmx", ccmx().replace("NUMBER_OF_SETS 3", "NUMBER_OF_SETS 9"),
         "NUMBER_OF_SETS is 9"),
        ("late.ccss", ccss().replace("NUMBER_OF_SETS 3", "NUMBER_OF_SETS 9"),
         "NUMBER_OF_SETS is 9"),
    ):
        path = tmp_path / name
        path.write_text(body)
        with pytest.raises(CorrectionError, match=expected):
            read_correction(path)

    # And directly through the per-kind readers, which is the path _wrapped guards.
    with pytest.raises(CorrectionError, match="NUMBER_OF_SETS is 9"):
        parse_ccmx(ccmx().replace("NUMBER_OF_SETS 3", "NUMBER_OF_SETS 9"))
    with pytest.raises(CorrectionError, match="NUMBER_OF_SETS is 9"):
        parse_ccss(ccss().replace("NUMBER_OF_SETS 3", "NUMBER_OF_SETS 9"))


# -- every correction ArgyllCMS ships ---------------------------------------

def test_every_shipped_correction_file_reads(argyll_archive):
    """Parse the CCMX and CCSS correction corpus shipped in pinned Argyll 3.5.0."""
    with zipfile.ZipFile(argyll_archive) as archive:
        names = [name for name in archive.namelist()
                 if name.endswith((".ccmx", ".ccss"))]
        assert len(names) >= 20, f"expected the shipped corrections, found {len(names)}"
        kinds = {"ccmx": 0, "ccss": 0}
        for name in names:
            text = archive.read(name).decode("utf-8", errors="replace")
            parsed = parse_ccmx(text) if name.endswith(".ccmx") else parse_ccss(text)
            kinds["ccmx" if name.endswith(".ccmx") else "ccss"] += 1
            if isinstance(parsed, CorrectionMatrix):
                assert len(parsed.matrix) == 3
                assert parsed.apply((1.0, 1.0, 1.0)) != (0.0, 0.0, 0.0)
            else:
                assert len(parsed.samples) >= 3
                assert all(len(sample) == parsed.bands for sample in parsed.samples)
    assert kinds["ccmx"] and kinds["ccss"], f"both kinds must be covered: {kinds}"


@pytest.mark.parametrize("bad", ["0.0", "0", "-1.0", "-100"])
def test_a_non_positive_normalisation_is_refused(bad):
    """`norm` is a DIVISOR, not a label.

    Every consumer divides by it -- `getval_xspec` (`xspect.c:4380-4382`) is
    `*rv /= sp->norm`, reached from `i1d3_comp_calmat` (`i1d3.c:2564,:2575`)
    through `xsp2cie_sconvert` (`xspect.c:5988`). A zero norm makes every band of
    every sample infinite and the resulting correction matrix NaN; a negative one
    flips every sign. Neither is checked anywhere downstream, so it is checked
    here and again in the helper's own loader.
    """
    with pytest.raises(CorrectionError, match="SPECTRAL_NORM must be positive"):
        parse_ccss(ccss(norm=f'SPECTRAL_NORM "{bad}"\n'))


def test_a_normalisation_other_than_one_or_a_hundred_survives_the_read():
    """Both shipped .ccss files use 1.0, and the obvious hardcode is 100.0."""
    assert parse_ccss(ccss(norm='SPECTRAL_NORM "2.5"\n')).norm == 2.5


def test_content_appended_after_end_data_cannot_override_the_correction():
    """The worst shape of the table-boundary bug: cbid and dtech reach hardware.

    `instrument.py` forwards `calibration_base_id` and `display_technology` to
    `col_cor_mat()`, so a file whose trailing lines were merged into its first
    table changed what was sent to the instrument. Upstream refuses such a file
    for having two tables (`ccmx.c:203`), and so must this.
    """
    appended = ccmx() + 'INSTRUMENT "ATTACKER"\nDISPLAY_TYPE_BASE_ID 7\n'
    with pytest.raises(CorrectionError, match="exactly one table"):
        parse_ccmx(appended)
    with pytest.raises(CorrectionError, match="exactly one table"):
        parse_ccss(ccss() + 'TECHNOLOGY "CRT"\n')


def test_a_malformed_number_is_refused_through_the_documented_channel():
    """"e5" reached float() and raised a bare ValueError, which `_wrapped` does
    not catch -- so it escaped `read_correction` as an undocumented class."""
    with pytest.raises(CorrectionError):
        parse_ccmx(ccmx(rows=("e5 0.1 0.2", "0.1 1.0 0.2", "0.1 0.2 1.0")))


def test_every_parsed_keyword_is_carried(tmp_path):
    """Six fields were parsed and asserted by nothing: replacing any of them with
    None survived the suite. All 25 shipped .ccmx carry DESCRIPTOR."""
    text = ccmx(keywords=(
        'DESCRIPTOR "Xrite DTP94 & Dell 2408WFP"\nINSTRUMENT "Xrite DTP94"\n'
        'DISPLAY "Dell 2408WFP"\nCOLOR_REP "XYZ"\nREFERENCE "GretagMacbeth i1 Pro"\n'
        'UI_SELECTORS "xy"\nOEM "YES"\n'
    ))
    parsed = parse_ccmx(text)
    assert parsed.description == "Xrite DTP94 & Dell 2408WFP"
    assert parsed.reference == "GretagMacbeth i1 Pro"
    assert parsed.selectors == "xy"
    assert parsed.oem is True
    spectra = parse_ccss(ccss(extra='ORIGINATOR "Argyll ccxxmake"\nCREATED "Wed Jan  4 2017"\n'))
    assert spectra.originator == "Argyll ccxxmake"
    assert spectra.created == "Wed Jan  4 2017"


def test_an_absent_oem_keyword_means_not_oem():
    """ccmx.c:280-287 defaults oem to 0; making an absent OEM True survived."""
    assert parse_ccmx(ccmx()).oem is False
    assert parse_ccss(ccss()).oem is False


def test_the_file_readers_are_reachable_and_typed(tmp_path):
    """read_ccmx and read_ccss are exported and were called by no test at all --
    replacing either body with `return None` survived the whole suite."""
    from color_measurement.colorimeter_correction import read_ccmx, read_ccss
    matrix = tmp_path / "a.ccmx"
    matrix.write_text(ccmx())
    spectra = tmp_path / "b.ccss"
    spectra.write_text(ccss())
    assert read_ccmx(matrix).matrix == MATRIX
    assert read_ccss(spectra).bands == 4
    # Each refuses the other kind, rather than returning something unusable.
    with pytest.raises(CorrectionError, match="isn't a CCMX format file"):
        read_ccmx(spectra)
    with pytest.raises(CorrectionError, match="isn't a CCSS format file"):
        read_ccss(matrix)


@pytest.mark.parametrize("keyword,value", [
    ("SPECTRAL_BANDS", "abc"), ("SPECTRAL_START_NM", "abc"),
    ("SPECTRAL_END_NM", "abc"), ("SPECTRAL_NORM", "abc"),
])
def test_a_non_numeric_grid_keyword_is_refused_not_read_as_zero(keyword, value):
    """_integer and _number returning 0 instead of raising both survived. Upstream
    uses atoi/atof, which yield 0 -- and a zero band count or a zero norm reaching
    col_cal_spec_set is a correction built from nothing."""
    text = ccss().replace(f'{keyword} "4"', f'{keyword} "{value}"')
    text = text.replace(f'{keyword} "400.0"', f'{keyword} "{value}"')
    text = text.replace(f'{keyword} "700.0"', f'{keyword} "{value}"')
    text = text.replace(f'{keyword} "100.0"', f'{keyword} "{value}"')
    with pytest.raises(CorrectionError, match=f"{keyword} is not"):
        parse_ccss(text)


@pytest.mark.parametrize("bands", ["0", "-1", "1"])
def test_a_band_count_without_interpolation_neighbours_is_refused(bands):
    """The Argyll interpolation path divides by n-1 and reads neighbours."""
    with pytest.raises(CorrectionError, match="at least 2"):
        parse_ccss(ccss().replace('SPECTRAL_BANDS "4"', f'SPECTRAL_BANDS "{bands}"'))


def test_a_non_finite_grid_value_of_either_sign_is_refused():
    """The NaN half of the finiteness check was droppable on its own."""
    for value in ("nan", "inf", "-inf"):
        with pytest.raises(CorrectionError, match="not finite|not a number"):
            parse_ccss(ccss().replace('SPECTRAL_START_NM "400.0"',
                                      f'SPECTRAL_START_NM "{value}"'))


def test_a_non_numeric_base_id_reads_as_not_known():
    """ccmx.c:261 uses atoi, so a non-numeric DISPLAY_TYPE_BASE_ID is 0 -- the same
    "not known" an absent keyword gives, which inst.h:1105 turns into 1."""
    parsed = parse_ccmx(ccmx(
        keywords='INSTRUMENT "i"\nDISPLAY "d"\nCOLOR_REP "XYZ"\nDISPLAY_TYPE_BASE_ID "xyz"\n'))
    assert parsed.base_id == 0
    assert parsed.calibration_base_id == 1


def test_a_single_band_set_is_not_admitted_as_display_calibration():
    one = ('CCSS\n\nDISPLAY "d"\nSPECTRAL_BANDS "1"\nSPECTRAL_START_NM "550.0"\n'
           'SPECTRAL_END_NM "550.0"\n\nNUMBER_OF_FIELDS 1\nBEGIN_DATA_FORMAT\n'
           "SPEC_550\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\nBEGIN_DATA\n"
           "0.5\n0.25\n0.125\nEND_DATA\n")
    with pytest.raises(CorrectionError, match="at least 2"):
        parse_ccss(one)


@pytest.mark.parametrize("bands", [2, 3])
def test_two_and_three_band_grids_are_accepted_at_any_spacing(bands):
    """The composed native cubic helper falls back to linear interpolation below
    four samples, so the parser imposes no spacing rule of its own."""
    coarse = parse_ccss(ccss(bands=bands))
    assert coarse.wavelengths_nm() == {2: (400.0, 700.0), 3: (400.0, 550.0, 700.0)}[bands]
    fine = parse_ccss(ccss(bands=bands, end=400 + 4 * (bands - 1)))
    assert fine.wavelengths_nm() == tuple(400 + 4 * index for index in range(bands))


def test_a_band_below_a_hundred_nanometres_keeps_its_zero_padding():
    """sprintf("SPEC_%03d", nm) pads to three digits; dropping the padding survived
    because no test used a band below 100 nm."""
    text = ('CCSS\n\nDISPLAY "d"\nSPECTRAL_BANDS "3"\nSPECTRAL_START_NM "80.0"\n'
            'SPECTRAL_END_NM "88.0"\n\nNUMBER_OF_FIELDS 3\nBEGIN_DATA_FORMAT\n'
            "SPEC_080 SPEC_084 SPEC_088\nEND_DATA_FORMAT\n\nNUMBER_OF_SETS 3\n"
            "BEGIN_DATA\n1 2 3\n4 5 6\n7 8 9\nEND_DATA\n")
    assert parse_ccss(text).samples[0] == (1.0, 2.0, 3.0)


def test_matrix_numeric_boundary_rejects_overflow_and_malformed_direct_values():
    with pytest.raises(CorrectionError, match="three rows"):
        CorrectionMatrix(((1., 0., 0.),), "test")
    with pytest.raises(CorrectionError, match="finite"):
        CorrectionMatrix(((float("nan"), 0., 0.), (0., 1., 0.), (0., 0., 1.)), "test")
    huge = CorrectionMatrix(((1e308, 0., 0.), (0., 1., 0.), (0., 0., 1.)), "test")
    with pytest.raises(CorrectionError, match="overflowed"):
        huge.apply((1e308, 1., 1.))


def test_duplicate_correction_fields_are_not_silently_aliased():
    with pytest.raises(CorrectionError, match="unique"):
        parse_ccmx(ccmx().replace("XYZ_X XYZ_Y XYZ_Z", "XYZ_X XYZ_X XYZ_Z"))


@pytest.mark.parametrize("raw", ["-1", "2147483648"])
def test_a_calibration_base_id_outside_int_is_refused(raw):
    """``col_cor_mat()`` takes the base ID as an ``int`` (inst.h:1105); both ends."""
    with pytest.raises(CorrectionError, match="Invalid calibration base ID"):
        parse_ccmx(ccmx(keywords='INSTRUMENT "i"\nDISPLAY "d"\nCOLOR_REP "XYZ"\n'
                                 f'DISPLAY_TYPE_BASE_ID "{raw}"\n'))


@pytest.mark.parametrize("samples, accepted", [(1000, True), (1001, False)])
def test_the_calibration_sample_count_bound_is_pinned_at_both_ends(samples, accepted):
    if accepted:
        assert len(parse_ccss(ccss(samples=samples)).samples) == samples
    else:
        with pytest.raises(CorrectionError, match="3 to 1000"):
            parse_ccss(ccss(samples=samples))
