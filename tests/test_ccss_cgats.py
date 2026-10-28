"""Installed .ccss files are read within an xspect's bands and as their column types."""
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import argyll_native_check as native  # noqa: E402
from native_argyll import ccss_cgats as proof  # noqa: E402
from native_argyll import sanitizers  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402


@pytest.fixture(scope="module")
def original(argyll_archive):
    return before_source("ccss_cgats", proof.PATH, argyll_archive)


def test_actual_reader_validates_grid_norm_and_numeric_storage(
        argyll_archive, tmp_path, prepared_production):
    report = proof.prove(argyll_archive, tmp_path / "proof", source=prepared_production)
    assert report["status"] == "passed"
    baseline, patched = report["results"]["baseline"], report["results"]["patched"]
    # The pristine reader accepts every one of these files.
    assert set(baseline.values()) == {"accepted"}
    assert patched["integer_columns"] == patched["max_bands"] == "accepted"
    assert patched["over_max_bands"] == "Input file SPECTRAL_BANDS 602 is outside 2 to 601"
    assert patched["string_column"] == "Input file field SPEC_700 is not numeric"


def test_build_time_proof_reads_through_the_composed_units(argyll_archive, prepared_production,
                                                          tmp_path):
    """Given the composed tree, the repaired reader links the workers' CGATS parser,
    which refuses an integer the pristine parser converts to a different sample."""
    cgats = prepared_production / "cgats/cgats.c"
    report = proof.prove(argyll_archive, tmp_path / "proof", source=prepared_production)
    assert report["support"] == "composed"
    assert report["support_sha256"]["cgats/cgats.c"] == proof.digest(cgats.read_bytes())
    assert "out of range" in report["results"]["patched"]["integer_out_of_range"]
    assert "integer_out_of_range" not in report["results"]["baseline"]
    # The case distinguishes: the same reader over pristine units accepts it.
    case = proof.COMPOSED_CASES["integer_out_of_range"][0]
    with zipfile.ZipFile(argyll_archive) as z:
        exe = proof.compile_reader((prepared_production / proof.PATH).read_text(), z,
                                   tmp_path / "pristine_units",
                                   shutil.which("cc"), sanitize=False)
    row = proof.read_case(exe, tmp_path, "pristine_units", "integer_out_of_range", case)
    # The original atoi overflow is undefined: glibc wraps to INT_MIN while
    # the Windows CRT clamps to INT_MAX. Both store a native int different
    # from the file's 2147483648, which the composed parser must refuse.
    value = row["first"][0]
    assert row["rc"] == 0 and -(2**31) <= value <= 2**31 - 1
    assert int(value) == value and value != 2147483648.0


# The case each edit alone is needed for, and the observable wrong result
# without it. Mutated readers run unsanitized: two of these are undefined.
# Independent source mutants restore one unsafe reader fragment at a time.
READER_REVERTS = {'band_count': ('\tsp.spec_n = atoi(icg->t[0].kdata[ii]);\n',
                '\t{\n'
                '\t\tdouble bands;\n'
                '\t\tif (cm_ccss_number(icg->t[0].kdata[ii], &bands)) {\n'
                '\t\t\tsprintf(p->e.m,"Input file SPECTRAL_BANDS is not a number");\n'
                '\t\t\treturn 1;\n'
                '\t\t}\n'
                '\t\t/* CM: bound before integer conversion, and do not truncate fractions. '
                '*/\n'
                '\t\tif (!(bands >= 2.0 && bands <= XSPECT_MAX_BANDS && (int)bands == bands)) '
                '{\n'
                '\t\t\tsprintf(p->e.m,"Input file SPECTRAL_BANDS %.17g is outside 2 to '
                '%d",bands,XSPECT_MAX_BANDS);\n'
                '\t\t\treturn 1;\n'
                '\t\t}\n'
                '\t\tsp.spec_n = (int)bands;\n'
                '\t}\n'),
 'wavelengths': ('\tsp.spec_wl_long = atof(icg->t[0].kdata[ii]);\n\n',
                 '\tif (cm_ccss_number(icg->t[0].kdata[ii], &sp.spec_wl_long)) {\n'
                 '\t\tsprintf(p->e.m,"Input file spectral end wavelength is not a number");\n'
                 '\t\treturn 1;\n'
                 '\t}\n'
                 "\t/* CM: each band's nearest nm is converted to int below. */\n"
                 '\tif (!(sp.spec_wl_short >= -1e9 && sp.spec_wl_short <= 1e9\n'
                 '\t   && sp.spec_wl_long >= -1e9 && sp.spec_wl_long <= 1e9)) {\n'
                 '\t\tsprintf(p->e.m,"Input file spectral wavelength range is not usable");\n'
                 '\t\treturn 1;\n'
                 '\t}\n'
                 '\t/* CM: a multi-band grid must have positive spacing. */\n'
                 '\tif (!(sp.spec_wl_short > 0.0 && sp.spec_wl_long >= sp.spec_wl_short\n'
                 '\t   && sp.spec_wl_long > sp.spec_wl_short)) {\n'
                 '\t\tsprintf(p->e.m,"Input file spectral grid is not positive and ordered");\n'
                 '\t\treturn 1;\n'
                 '\t}\n'
                 '\n'),
 'norm': ('\t\tsp.norm = atof(icg->t[0].kdata[ii]);\n\t}\n',
          '\t\tif (cm_ccss_number(icg->t[0].kdata[ii], &sp.norm)) {\n'
          '\t\t\tsprintf(p->e.m,"Input file SPECTRAL_NORM is not a number");\n'
          '\t\t\treturn 1;\n'
          '\t\t}\n'
          '\t}\n'
          '\t/* CM: every consumer divides by the norm; x - x is 0 only when x is finite. */\n'
          '\tif (!(sp.norm > 0.0 && sp.norm - sp.norm == 0.0)) {\n'
          '\t\tsprintf(p->e.m,"Input file SPECTRAL_NORM is not a positive number");\n'
          '\t\treturn 1;\n'
          '\t}\n'),
 'field_type': ('\t\t\tsprintf(p->e.m,"Input file doesn\'t contain field %s",buf);\n'
                '\t\t\treturn 1;\n'
                '\t\t}\n',
                '\t\t\tsprintf(p->e.m,"Input file doesn\'t contain field %s",buf);\n'
                '\t\t\treturn 1;\n'
                '\t\t}\n'
                '\t\t/* CM: an all-integer column is stored as int (cgats.c:731-739). */\n'
                '\t\tif (icg->t[0].ftype[spi[j]] != r_t && icg->t[0].ftype[spi[j]] != i_t) {\n'
                '\t\t\tsprintf(p->e.m,"Input file field %s is not numeric",buf);\n'
                '\t\t\treturn 1;\n'
                '\t\t}\n'),
 'value_type': ('\t\t\tp->samples[i].spec[j] = *((double *)icg->t[0].fdata[i][spi[j]]);\n',
                '\t\t\tif (icg->t[0].ftype[spi[j]] == i_t)\n'
                '\t\t\t\tp->samples[i].spec[j] = (double)*((int '
                '*)icg->t[0].fdata[i][spi[j]]);\n'
                '\t\t\telse\n'
                '\t\t\t\tp->samples[i].spec[j] = *((double *)icg->t[0].fdata[i][spi[j]]);\n'
                '\t\t\t/* CM: do not admit a nonfinite correction into display calibration. '
                '*/\n'
                '\t\t\t/* Every consumer reads the band divided by the positive, finite norm, '
                '*/\n'
                '\t\t\t/* so a nonfinite band or an overflowing quotient is refused here. */\n'
                '\t\t\t{\n'
                '\t\t\t\tdouble normalized = p->samples[i].spec[j] / sp.norm;\n'
                '\t\t\t\tif (normalized - normalized != 0.0) {\n'
                '\t\t\t\t\tsprintf(p->e.m,"Input file normalized spectral sample %d band %d is '
                'not finite",i,j);\n'
                '\t\t\t\t\treturn 1;\n'
                '\t\t\t\t}\n'
                '\t\t\t}\n')}

MUTATIONS = {
    "band_count": ("zero_bands", "expected refusal"),
    "wavelengths": ("nan_start", "expected refusal 'spectral wavelength"),
    "norm": ("zero_norm", "expected refusal"),
    "field_type": ("string_column", "expected refusal"),
    "value_type": ("integer_columns", "first is"),
}


@pytest.mark.parametrize("edit", sorted(MUTATIONS))
def test_each_edit_is_needed_for_its_case(argyll_archive, tmp_path, mutate_production, edit):
    case, symptom = MUTATIONS[edit]
    replacement, anchor = READER_REVERTS[edit]

    def regress(text, path):
        assert text.count(anchor) == 1
        return text.replace(anchor, replacement, 1)

    mutated = mutate_production([proof.PATH], regress)
    with zipfile.ZipFile(argyll_archive) as z:
        exe = proof.compile_reader((mutated / proof.PATH).read_text(), z, tmp_path / "mutant",
                                   shutil.which("cc"), sanitize=False, composed=mutated)
    row = proof.read_case(exe, tmp_path, "patched", case, proof.CASES[case][0])
    with pytest.raises(AssertionError, match=f"patched/{case}: {symptom}"):
        proof._check("patched", case, row, proof.CASES[case][1], repaired=True)


@pytest.fixture(scope="module")
def sanitized_pristine_reader(argyll_archive, original, tmp_path_factory):
    """One sanitized build of the unchanged reader serves every fault case."""
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable")
    with zipfile.ZipFile(argyll_archive) as z:
        return proof.compile_reader(original, z, tmp_path_factory.mktemp("pristine"), cc,
                                    sanitize=True)


@pytest.mark.parametrize(("case", "fault"), [
    ("over_max_bands", r"index 601 out of bounds for type 'int\s*\[601\]'"),
    ("integer_columns", "heap-buffer-overflow"),
    ("string_column", "heap-buffer-overflow"),
    ("single_band", "is outside the range of representable values"),
    ("huge_end", "is outside the range of representable values"),
])
def test_pristine_reader_faults_under_the_sanitizers(sanitized_pristine_reader, tmp_path,
                                                    case, fault):
    """The cases the proof never runs on the pristine reader, and why."""
    with pytest.raises(AssertionError, match=f"Sanitizer diagnostic in pristine/{case}"):
        proof.read_case(sanitized_pristine_reader, tmp_path, "pristine", case,
                        proof.CASES[case][0])
    log = (tmp_path / f"{case}.log").read_text()
    assert re.search(fault, log) is not None and sanitizers.diagnostic(log) is not None


def test_preparation_records_the_reader_import_and_application_policy_once(
        argyll_archive, tmp_path):
    source, report = native.prepare_with_proof_inputs(argyll_archive, tmp_path / "build")
    rows = [r for r in report["contribution"]["entries"] if r["path"] == proof.PATH]
    policies = [row for row in report["patches"] if row["path"] == proof.PATH]
    assert len(rows) == len(policies) == 1
    assert rows[0]["post_sha256"] == policies[0]["before_sha256"]
    assert policies[0]["role"] == "application-policy"
    assert policies[0]["after_sha256"] == proof.digest((source / proof.PATH).read_bytes())


@pytest.mark.parametrize("guard,case", [
    ("suffix", "norm_keyword_suffix"), ("fraction", "fractional_band_count"),
    ("grid", "flat_grid"),
])
def test_complete_numeric_keywords_and_integer_counts_are_required(
        argyll_archive, tmp_path, prepared_production, guard, case):
    repaired = (prepared_production / proof.PATH).read_text()
    old = {
        "suffix": "*end != '\\0' || ",
        "fraction": " && (int)bands == bands",
        "grid": (
            '\tif (!(sp.spec_wl_short > 0.0 && sp.spec_wl_long >= sp.spec_wl_short\n'
            '\t   && sp.spec_wl_long > sp.spec_wl_short)) {\n'
            '\t\tsprintf(p->e.m,"Input file spectral grid is not positive and ordered");\n'
            '\t\treturn 1;\n'
            '\t}\n'),
    }[guard]
    assert repaired.count(old) == 1
    mutated = repaired.replace(old, "", 1)
    assert mutated != repaired
    with zipfile.ZipFile(argyll_archive) as z:
        exe = proof.compile_reader(mutated, z, tmp_path, shutil.which("cc"), sanitize=True,
                                   composed=prepared_production)
    row = proof.read_case(exe, tmp_path, "mutant", case, proof.CASES[case][0])
    with pytest.raises(AssertionError, match=f"mutant/{case}: expected refusal"):
        proof._check("mutant", case, row, proof.CASES[case][1], repaired=True)
