"""The i1Pro3 scan refuses nonfinite wavelength results; finite scans are unchanged."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))
import argyll_native_check as native  # noqa: E402
from native_argyll import i1pro3_wavelength_finite as proof  # noqa: E402
from native_argyll.proof_inputs import before_source  # noqa: E402


@pytest.fixture(scope='module')
def composed(argyll_archive, tmp_path_factory):
    source, _ = native.prepare(argyll_archive, tmp_path_factory.mktemp('wavelength') / 'build',
                               contribution=native.NATIVE / 'contributions/cr_pr')
    before = before_source('i1pro3_wavelength_finite', proof.SOURCE_PATH, argyll_archive)
    return source, before, (source / proof.SOURCE_PATH).read_text()


def test_production_checks_calibrated_bands_and_both_aggregate_helpers(composed):
    _, before, after = composed
    scan = proof._function(after, proof.SCAN)
    assert scan.count('if (!isfinite(specrd[i][j]))') == 1
    assert (scan.index('vect_mul(specrd[i], s->cal_factor[hr], m->nwav[hr]);')
            < scan.index('if (!isfinite(specrd[i][j]))'))
    for signature in proof.HELPERS:
        old = proof._function(before, signature)
        current = proof._function(after, signature)
        assert 'if (!isfinite(aavg))' not in old
        assert current.count('if (!isfinite(aavg))') == 1


# The sanitized run covers every scenario on both resolutions, with and without
# a position map and in both modes over two grid points; the plain run covers
# all eight. Under the sanitizers each of the 292 probe processes starts slowly,
# and the gate's suite has a time limit.
SANITIZED_GRID = ((0, 0, 0), (1, 1, 1))


def test_invalid_results_reach_the_existing_refusal_and_finite_scans_are_unchanged(
        argyll_archive, tmp_path, composed):
    if shutil.which('cc') is None:
        pytest.skip('C compiler unavailable')
    result = proof.prove(argyll_archive, tmp_path / 'proof', source=composed[0])
    assert result['status'] == 'passed'
    assert result['hardware_tested'] is result['instrument_runtime_linked'] is False
    assert result['before_sha256'] == proof.digest(composed[1].encode())
    assert result['patched_sha256'] == proof.digest(composed[2].encode())
    for signature in proof.FUNCTIONS:
        assert result['function_sha256'][signature.strip()] == {
            'before': proof.digest(proof._function(composed[1], signature).encode()),
            'after': proof.digest(proof._function(composed[2], signature).encode())}
    baseline, repaired = (result['results'][v] for v in ('baseline', 'patched'))
    assert len(baseline) == len(repaired) == len(proof.SCAN_CASES) * len(proof.GRID) == 136
    for hr, zebra, emissive in proof.GRID:
        key = f'/hr{hr}/zebra{zebra}/emissive{emissive}'
        # Finite bands whose sums cancel overflow the average; a finite factor
        # times a finite band overflows the multiplication. The helper sees
        # neither, so the original returns success with nonfinite bands.
        for case, bands in (('band_sum_overflow', 6), ('calibration_overflow', 3 * proof.NWAV[hr])):
            row = baseline[case + key]
            assert (row['code'], row['checks'], row['nonfinite_output']) == (0, 3, bands)
            assert repaired[case + key]['code'] == proof.READINCONS
        # Isolate both edges of the band loop. A NaN factor tests isfinite,
        # but is not a finite-input NaN control.
        for case in ('first_calibration_overflow', 'last_calibration_overflow', 'nan_factor'):
            assert baseline[case + key]['nonfinite_output'] == 3
            assert repaired[case + key]['code'] == proof.READINCONS
        # A NaN row passes the original aggregate comparison; the repaired helper refuses it.
        assert baseline['nan_row' + key]['code'] == 0
        assert (repaired['nan_row' + key]['code'], repaired['nan_row' + key]['averaged']) == (
            proof.READINCONS, 1)
        # A later patch failing after an earlier success still refuses the whole scan.
        assert repaired['later_band_overflow' + key]['averaged'] == 2
        for case in proof.SCAN_CONTROLS:
            assert baseline[case + key] == repaired[case + key]
        assert repaired['valid' + key]['values'] == baseline['valid' + key]['values']
    helpers = result['helper_results']
    assert helpers['wav1']['baseline']['nan_cell']['code'] == 0
    assert helpers['wav1']['patched']['nan_cell']['code'] == 1
    # Negative infinity already failed the first helper; the even/odd helper
    # accepted it, and now refuses it.
    assert helpers['wav2']['baseline']['negative_inf']['code'] == 0
    assert helpers['wav2']['patched']['negative_inf']['code'] == 1
    assert result == json.loads((tmp_path / 'proof/report.json').read_text())


def test_the_sanitized_run_reports_the_same_outcomes(argyll_archive, tmp_path, monkeypatch,
                                                   composed):
    if shutil.which('cc') is None:
        pytest.skip('C compiler unavailable')
    monkeypatch.setattr(proof, 'GRID', SANITIZED_GRID)
    result = proof.prove(argyll_archive, tmp_path / 'proof', sanitize=True, source=composed[0])
    assert result['status'] == 'passed' and result['sanitize'] is True
    assert len(result['results']['patched']) == len(proof.SCAN_CASES) * len(SANITIZED_GRID)
    for hr, zebra, emissive in SANITIZED_GRID:
        key = f'nan_row/hr{hr}/zebra{zebra}/emissive{emissive}'
        assert result['results']['baseline'][key]['nonfinite_output'] == proof.NWAV[hr]
        assert result['results']['patched'][key]['code'] == proof.READINCONS
    assert result == json.loads((tmp_path / 'proof/report.json').read_text())


# Each guard has a case the other two cannot refuse. One grid point suffices to
# show it; the outcome tables are the same at every point.
@pytest.mark.parametrize(('guard', 'message'), [
    ('band', 'patched result for band_sum_overflow/hr0/zebra0/emissive0: code 0'),
    ('band_skip_first',
     'patched result for first_calibration_overflow/hr0/zebra0/emissive0: code 0'),
    ('band_skip_last', 'patched result for last_calibration_overflow/hr0/zebra0/emissive0: code 0'),
    ('band_isinf', 'patched result for nan_factor/hr0/zebra0/emissive0: code 0'),
    ('wav1', 'patched wav1 result for nan_cell: code 0'),
    ('wav2', 'patched wav2 result for nan_cell: code 0'),
])
def test_each_guard_has_an_independent_regression(
        argyll_archive, tmp_path, monkeypatch, composed, guard, message, mutate_production):
    if shutil.which('cc') is None:
        pytest.skip('C compiler unavailable')
    def regressed(text):
        if guard.startswith('band'):
            body = proof._function(text, proof.SCAN)
            calibrated = body.index('vect_mul(specrd[i], s->cal_factor[hr], m->nwav[hr]);')
            start = body.index('\t\t\tfor (j = 0;', calibrated)
            end = body.index('\n\t\t\t}\n', start) + len('\n\t\t\t}\n')
            old = body[start:end]
            if guard == 'band':
                new = ''
            elif guard == 'band_skip_first':
                new = old.replace('for (j = 0;', 'for (j = 1;')
            elif guard == 'band_skip_last':
                new = old.replace('j < m->nwav[hr]', 'j < m->nwav[hr]-1')
            else:
                new = old.replace('!isfinite(specrd[i][j])', 'isinf(specrd[i][j])')
            assert new != old
            return text.replace(body, body.replace(old, new), 1)
        body = proof._function(text, proof.HELPERS[guard == 'wav2'])
        start = body.index('\tif (!isfinite(aavg)) {')
        end = body.index('\n\t}\n', start) + len('\n\t}\n')
        return text.replace(body, body[:start] + body[end:], 1)

    changed = regressed(composed[2])
    assert changed != composed[2]
    source = mutate_production([proof.SOURCE_PATH], lambda text, path: changed)
    monkeypatch.setattr(proof, 'GRID', ((0, 0, 0),))
    with pytest.raises(AssertionError, match=message):
        proof.prove(argyll_archive, tmp_path / 'regressed', source=source)
    assert json.loads((tmp_path / 'regressed/report.json').read_text())['status'] == 'incomplete'


def test_an_arbitrary_error_cannot_replace_the_proven_refusal(
        argyll_archive, tmp_path, monkeypatch, composed):
    if shutil.which('cc') is None:
        pytest.skip('C compiler unavailable')
    run = proof.subprocess.run

    def substitute(argv, **kwargs):
        result = run(argv, **kwargs)
        if Path(argv[0]).stem == 'patched' and argv[1:] == ['nan_row', '0', '0', '0']:
            row = json.loads(result.stdout)
            row.update(code=123)
            result.stdout = json.dumps(row)
        return result

    monkeypatch.setattr(proof.subprocess, 'run', substitute)
    monkeypatch.setattr(proof, 'GRID', ((0, 0, 0),))
    with pytest.raises(AssertionError,
                       match='patched result for nan_row/hr0/zebra0/emissive0: code 123'):
        proof.prove(argyll_archive, tmp_path / 'early-error', source=composed[0])


@pytest.mark.parametrize('projection', ['repairs', 'cr_pr'])
def test_both_current_projections_prepare_the_same_i1pro3_source(
        argyll_archive, tmp_path, composed, projection):
    source, report = native.prepare_with_proof_inputs(argyll_archive, tmp_path / 'build',
        contribution=native.NATIVE / 'contributions' / projection)
    rows = [row for row in report['contribution']['entries'] if row['path'] == proof.SOURCE_PATH]
    assert len(rows) == 1
    current = (source / proof.SOURCE_PATH).read_text()
    assert rows[0]['post_sha256'] == proof.digest(current.encode())
    assert not any(row['path'] == proof.SOURCE_PATH for row in report['patches'])
    assert current == composed[2]
