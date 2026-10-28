"""Named display calibration: native identity, stale refusal and retained readback."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from conftest import native_display_unsupported, supplied_stub

from color_measurement.argyll_capability import CAPABILITY2, mode_word
from color_measurement.display_types import DisplayTypeSelection, catalogue, validate_catalogue
from color_measurement.instrument import Configuration, Instrument, InstrumentError
from color_measurement.instrument_frontend import InstrumentFrontend
from color_measurement.native_receipt import native_worker_name

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'tools/native_argyll'
MODEL = 'X-Rite i1 DisplayPro, ColorMunki Display'


def observed():
    rows = [{'index': i, 'id': str(i + 1) * 64, 'label': 'Duplicate label',
             'technology_code': 0, 'refresh_mode': 0, 'base_calibration_id': i + 1,
             'kind': 'builtin', 'source_path': None, 'custom': False,
             'identity_scope': 'catalogue_metadata', 'available': True,
             'unavailable_reason': None, 'native_label_bytes': None,
             'native_source_path_bytes': None, 'native_default': i == 0} for i in range(2)]
    summary = {'status': 'available', 'native_code': 0, 'count': 2,
               'catalogue_id': 'a' * 64, 'applied_choice_id': None,
               'readback': {'native_code': 0, 'technology_code': 0, 'refresh_mode': None,
                            'base_calibration_id': 0}}
    return catalogue(summary, rows)


def test_duplicate_labels_are_distinct_choices_and_zero_is_native_readback():
    value = observed()
    selected = DisplayTypeSelection.from_catalogue(value, value['entries'][1]['id'])
    configuration = Configuration.from_options({'display_type': selected.choice_id},
                                              display_types=value)
    assert configuration.display_type == selected
    assert configuration.flags('emissive_spot')['display_type'] == 1
    assert value['readback']['base_calibration_id'] == 0
    assert value['readback']['refresh_mode'] is None
    assert 'display_type' not in Configuration().flags('emissive_spot')
    front = InstrumentFrontend(Instrument('/never-started', MODEL),
        mode_capabilities=mode_word('emissive_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['disptype'], filter_capabilities=0,
        provenance='simulated', display_types=value)
    descriptor = json.loads(front.describe_json())
    option = next(row for row in descriptor['options'] if row['id'] == 'display_type')
    assert option['type'] == 'choice' and option['default'] is None and option['available']
    assert option['choices'] == [row['id'] for row in value['entries']]
    assert len({row['presentation_label'] for row in option['choice_details']}) == 2
    value['entries'][0]['label'] = 'mutated outside frontend'
    assert front._display_types['entries'][0]['label'] == 'Duplicate label'


@pytest.mark.parametrize('edit', [
    lambda value: value.update(count=1),
    lambda value: value.update(count=True),
    lambda value: value.update(catalogue_id='x'),
    lambda value: value['entries'][1].update(index=0),
    lambda value: value['entries'][1].update(id='1' * 64),
    lambda value: value['entries'][0].update(custom=1),
    lambda value: value['entries'][0].pop('native_default'),
    lambda value: value['entries'][0].update(native_default=1),
    lambda value: value['entries'][0].update(native_default=None),
    lambda value: value.update(applied_choice_id='c' * 64),
    lambda value: value['readback'].update(native_code=0x50000),
])
def test_malformed_catalogue_never_authorizes_a_choice(edit):
    value = observed()
    edit(value)
    with pytest.raises(ValueError):
        validate_catalogue(value)


def test_a_native_name_that_matches_a_qualified_label_remains_distinct():
    value = observed()
    value['entries'].append(value['entries'][0] | {
        'index': 2, 'id': '3' * 64, 'label': 'Duplicate label [builtin, #1]'})
    value['count'] = 3
    front = InstrumentFrontend(Instrument('/never-started', MODEL),
        mode_capabilities=mode_word('emissive_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['disptype'], filter_capabilities=0,
        provenance='simulated', display_types=value)
    option = next(row for row in json.loads(front.describe_json())['options']
                  if row['id'] == 'display_type')
    assert len({row['presentation_label'] for row in option['choice_details']}) == 3
    assert [row['label'] for row in option['choice_details']] == [
        row['label'] for row in value['entries']]


def test_unknown_or_numeric_choice_does_not_become_the_first_choice():
    for choice in (0, 1, True, '', 'not-a-choice', 'c' * 64):
        with pytest.raises(ValueError):
            Configuration.from_options({'display_type': choice}, display_types=observed())
    with pytest.raises(ValueError, match='unavailable'):
        DisplayTypeSelection.from_catalogue(
            catalogue(native_display_unsupported(), []), 'a' * 64)


@pytest.mark.parametrize('defaults', [(), (0,), (1,), (0, 1)])
def test_native_default_markers_do_not_infer_an_active_selection(defaults):
    value = observed()
    for row in value['entries']:
        row['native_default'] = row['index'] in defaults
    front = InstrumentFrontend(Instrument('/never-started', MODEL),
        mode_capabilities=mode_word('emissive_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['disptype'], filter_capabilities=0,
        provenance='simulated', display_types=value)
    snapshot = json.loads(front.describe_json())
    option = next(row for row in snapshot['options'] if row['id'] == 'display_type')
    marked = [row['index'] for row in option['choice_details'] if row['native_default']]
    assert marked == list(defaults)
    assert option['default'] is None
    assert Configuration.from_options({}, display_types=value).display_type is None
    assert snapshot['display_types']['applied_choice_id'] is None
    assert 'selected_index' not in snapshot['display_types']['readback']


def isolated_catalogue(*, all_unavailable=False):
    value = observed()
    value['entries'].append(value['entries'][0] | {'index': 2, 'id': '3' * 64})
    value['count'] = 3
    for index in (range(3) if all_unavailable else (1,)):
        value['entries'][index].update(
            label=None, native_label_bytes='ff', available=False,
            unavailable_reason='Native label is not valid UTF-8')
    return validate_catalogue(value)


@pytest.mark.parametrize('all_unavailable', [False, True])
def test_unavailable_rows_remain_observed_but_cannot_authorize_selection(all_unavailable):
    value = isolated_catalogue(all_unavailable=all_unavailable)
    front = InstrumentFrontend(Instrument('/never-started', MODEL),
        mode_capabilities=mode_word('emissive_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['disptype'], filter_capabilities=0,
        provenance='simulated', display_types=value)
    snapshot = json.loads(front.describe_json())
    option = next(row for row in snapshot['options'] if row['id'] == 'display_type')
    assert len(option['choice_details']) == len(snapshot['display_types']['entries']) == 3
    assert option['choices'] == ([] if all_unavailable else ['1' * 64, '3' * 64])
    assert option['available'] is not all_unavailable
    assert option['choice_details'][1]['presentation_label'].startswith(
        'Unavailable display calibration')
    with pytest.raises(ValueError, match='unavailable'):
        DisplayTypeSelection.from_catalogue(value, '2' * 64)
    assert Configuration.from_options({}, display_types=value).display_type is None
    if not all_unavailable:
        assert DisplayTypeSelection.from_catalogue(value, '3' * 64).index == 2


# Entry 1 is unavailable, so a lossy-label case also contradicts its availability;
# each case names the refusal it must reach first.
@pytest.mark.parametrize('edit,refusal', [
    (lambda row: row.update(available=True), 'Contradictory native display-type availability'),
    (lambda row: row.update(native_label_bytes='61'), 'Valid native UTF-8 must be published'),
    (lambda row: row.update(native_label_bytes='ff00'), 'bytes contain a terminator'),
    (lambda row: row.update(native_label_bytes='FF'), 'Invalid native display-type bytes'),
    (lambda row: row.update(label='guessed code page'), 'Invalid native display-type bytes'),
    (lambda row: row.update(unavailable_reason=None),
     'Contradictory native display-type availability'),
])
def test_inconsistent_or_lossy_unavailable_metadata_is_refused(edit, refusal):
    value = isolated_catalogue()
    edit(value['entries'][1])
    with pytest.raises(ValueError, match=refusal):
        validate_catalogue(value)


def test_an_unavailable_choice_cannot_be_reported_as_applied():
    value = isolated_catalogue()
    value['applied_choice_id'] = value['entries'][1]['id']
    with pytest.raises(ValueError, match='unavailable'):
        validate_catalogue(value)


def test_unavailable_presentation_label_cannot_collide_with_a_native_name():
    value = isolated_catalogue()
    value['entries'][2]['label'] = 'Unavailable display calibration [builtin, #2]'
    front = InstrumentFrontend(Instrument('/never-started', MODEL),
        mode_capabilities=mode_word('emissive_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['disptype'], filter_capabilities=0,
        provenance='simulated', display_types=value)
    option = next(row for row in json.loads(front.describe_json())['options']
                  if row['id'] == 'display_type')
    assert len({row['presentation_label'] for row in option['choice_details']}) == 3
    assert option['choice_details'][2]['label'] == value['entries'][2]['label']


@pytest.mark.parametrize('old_budget', [False, True])
def test_actual_reader_holds_a_full_catalogue_before_the_client_consumes_it(
    monkeypatch, old_budget,
):
    from color_measurement import native_protocol

    if old_budget:
        monkeypatch.setattr(native_protocol, 'MAX_OPERATION_EVENTS', 64)
    script = 'import json\nfor i in range(1000): print(json.dumps({"index": i}))'
    with native_protocol.HelperSession([sys.executable, '-c', script]) as session:
        session.threads[0].join(timeout=5)
        assert not session.threads[0].is_alive()
        if old_budget:
            assert session.reader_error == 'Helper output queue overflow'
        else:
            assert session.reader_error is None
            assert session.messages.qsize() == 1001  # All rows and EOF.
            assert [json.loads(session.messages.get())['index']
                    for _ in range(1000)] == list(range(1000))
            assert session.messages.get() is None


@pytest.fixture(scope='module')
def native_source(argyll_archive, tmp_path_factory):
    sys.path.insert(0, str(ROOT / 'tools'))
    import argyll_native_check as native

    compiler = shutil.which(native.compiler_name())
    if compiler is None:
        pytest.skip('C compiler unavailable for inert display-catalogue proof')
    directory = tmp_path_factory.mktemp('display-catalogue')
    with zipfile.ZipFile(argyll_archive) as archive:
        archive.extractall(directory)
    source = directory / 'Argyll_V3.5.0'
    sys.path.insert(0, str(ROOT / 'tools'))
    from native_argyll.display_catalogue import prepare_display_digest
    from native_argyll.sanitizers import FLAGS

    raw = (source / 'ccast/axTLS/sha256.c').read_bytes()
    report = {'upstream_source_sha256': {
        'ccast/axTLS/sha256.c': hashlib.sha256(raw).hexdigest()}}
    prepare_display_digest(source, report)
    shutil.copy(source / 'h/aconfig.h', directory / 'sa_config.h')
    flags = [f'-I{directory}', '-std=gnu99', '-DSALONEINSTLIB',
             '-DNT' if sys.platform == 'win32' else '-DUNIX',
             *(f'-I{source / path}' for path in ('spectro', 'numlib', 'xicc', 'cgats', 'h'))]
    # Link support is probed separately; diagnostics from a linked proof must fail.
    support = subprocess.run([compiler, '-x', 'c', '-', *FLAGS, '-o',
                              str(directory / native_worker_name('support'))],
        input='int main(void) { return 0; }', capture_output=True, text=True, timeout=30)
    if support.returncode == 0:
        flags += list(FLAGS)
    return directory, compiler, flags


@pytest.mark.parametrize('mutation', [None, 'matrix', 'spectrum', 'norm', 'index', 'limit',
                                      'grid', 'every_set', 'technology', 'matrix_base',
                                      'instrument_type', 'load_flag', 'bank_matrix',
                                      'later_grid', 'matrix_cell', 'first_sample',
                                      'length_prefix', 'last_entry', 'utf8_global_refusal',
                                      'default_flag'])
def test_compiled_production_identity_tracks_loaded_data_and_bounds(native_source, mutation):
    directory, compiler, flags = native_source
    out = directory / (mutation or 'baseline')
    out.mkdir()
    text = (NATIVE / 'display_types.h').read_text()
    edits = {
        'matrix': ('if (!cm_display_hash_double(&hash, row->mat[i][j])) return 0;',
                   'if (!isfinite(row->mat[i][j])) return 0;'),
        'spectrum': ('if (!cm_display_hash_double(&hash,s->spec[j])) return 0;',
                     'if (!isfinite(s->spec[j])) return 0;'),
        'norm': ('|| !cm_display_hash_double(&hash,s->norm)', '|| !isfinite(s->norm)'),
        'index': ('cm_display_hash_int(&hash, index);', '(void)index;'),
        'limit': ('catalogue->count>CM_DISPLAY_LIMIT', 'catalogue->count>CM_DISPLAY_LIMIT+1'),
        'grid': ('if (!cm_display_hash_double(&hash,s->spec_wl_short)',
                 'if (!isfinite(s->spec_wl_short)'),
        'every_set': ('for (int i=0; i<row->no_sets; i++) {', 'for (int i=0; i<1; i++) {'),
        'technology': ('cm_display_hash_int(&hash, row->dtech);', '(void)row->dtech;'),
        'matrix_base': ('cm_display_hash_int(&hash, row->cc_cbid);', '(void)row->cc_cbid;'),
        'instrument_type': ('cm_display_hash_int(&hash,meter->dtype);', '(void)meter->dtype;'),
        # What selection itself writes (spydX.c:1264-1266) must stay outside identity.
        'load_flag': ('row->flags & ~inst_dtflags_ld', 'row->flags'),
        'default_flag': ('row->flags & ~inst_dtflags_ld',
                         'row->flags & ~(inst_dtflags_ld | inst_dtflags_default)'),
        'bank_matrix': ('if (row->flags & inst_dtflags_ccmx) {',
                        'if (row->flags & (inst_dtflags_ccmx | inst_dtflags_mtx)) {'),
        # Each omission passed the earlier probe despite its every-field claim.
        'later_grid': ('!cm_display_hash_double(&hash,s->spec_wl_short)',
                       '(i == 0 ? !cm_display_hash_double(&hash,s->spec_wl_short) '
                       ': !isfinite(s->spec_wl_short))'),
        'matrix_cell': ('if (!cm_display_hash_double(&hash, row->mat[i][j])) return 0;',
                        'if ((i != 0 || j != 0) && '
                        '!cm_display_hash_double(&hash, row->mat[i][j])) return 0;'),
        # Without the length prefix, field boundaries vanish: index 1 with name
        # "2x" hashes exactly as index 12 with name "x".
        'length_prefix': ('SHA256_Update(hash, (const uint8_t *)length, (int)strlen(length));',
                          '(void)length;'),
        # Installed CCSS/CCMX rows come last (inst.c:950-1035): a digest that
        # skips the final entry misses the commonest change.
        'last_entry': ('for (int i=0; i<catalogue->count; i++) {\n        char identity[65];',
                       'for (int i=0; i<catalogue->count-1; i++) {\n        char identity[65];'),
        'first_sample': ('if (!cm_display_hash_double(&hash,s->spec[j])) return 0;',
                         'if (j != 0 && !cm_display_hash_double(&hash,s->spec[j])) return 0;'),
        'utf8_global_refusal': ('if (!cm_display_bounded_text(row->desc,sizeof(row->desc)-1)',
                               'if (!cm_utf8_text(row->desc,sizeof(row->desc)-1) '
                               '|| !cm_display_bounded_text(row->desc,sizeof(row->desc)-1)'),
    }
    if mutation:
        old, new = edits[mutation]
        assert text.count(old) == 1
        changed = text.replace(old, new)
        assert changed != text
        text = changed
    (out / 'display_types.h').write_text(text)
    shutil.copy(NATIVE / 'display_types_probe.c', out / 'probe.c')
    executable = out / native_worker_name('probe')
    build = subprocess.run([compiler, *flags, f'-I{out}', f'-I{NATIVE}', str(out / 'probe.c'),
                            '-o', str(executable)], capture_output=True, text=True, timeout=30)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    from native_argyll import sanitizers

    # A crash or sanitizer diagnostic cannot stand in for an identity assertion.
    sanitizers.refuse(run.stderr, f'display-types/{mutation or "baseline"}')
    frames = [json.loads(line) for line in run.stdout.splitlines()]
    if mutation:
        assert run.returncode == 1 and frames[-1]['failures'] > 0, mutation
    else:
        assert run.returncode == 0, run.stderr
        value = catalogue(frames[-1]['display_types'],
                          [{key: item for key, item in row.items() if key != 'event'}
                           for row in frames[:-1]])
        assert len(value['entries']) == 1000
        assert value['entries'][0]['label'] == 'Calibration café'
        assert value['entries'][0]['source_path'] == '/tmp/café.ccmx'
        assert value['entries'][1]['index'] == 1 and not value['entries'][1]['available']
        assert value['entries'][1]['label'] is None
        assert value['entries'][1]['native_label_bytes'] == 'Invalid '.encode().hex() + 'c0af'
        assert value['entries'][2]['available']
        assert [row['index'] for row in value['entries'] if row['native_default']] == [2, 999]
        assert value['readback']['refresh_mode'] is None
        assert value['readback']['base_calibration_id'] == 0
        assert all(len(line.encode()) <= 16384 for line in run.stdout.splitlines())


def native_catalogue(fixture='success'):
    worker = supplied_stub('COLOR_MEASUREMENT_GENERIC_STUB')
    meter = Instrument(worker, MODEL, expected_serial='CM-COLORIMETER-0001',
        environment=dict(os.environ, CM_COLORIMETER_FIXTURE=fixture),
                expected_helper_sha256=hashlib.sha256(
                    Path(worker).read_bytes()).hexdigest())
    reading = meter.measure('emissive_spot', spectral=False)
    binding = next(event for event in reading.events if event['event'] == 'connected')
    return meter, binding['display_types']


@pytest.mark.parametrize('fixture,defaults', [
    ('display_default_none', []), ('display_default_later', [1]),
    ('display_default_multiple', [0, 1]), ('display_default_unavailable', [1]),
])
def test_actual_native_default_flags_survive_missing_multiple_and_unavailable_cases(
    fixture, defaults,
):
    _, value = native_catalogue(fixture)
    assert [row['index'] for row in value['entries'] if row['native_default']] == defaults
    assert value['applied_choice_id'] is None
    assert Configuration.from_options({}, display_types=value).display_type is None
    if fixture == 'display_default_unavailable':
        assert not value['entries'][1]['available']
        with pytest.raises(ValueError, match='unavailable'):
            DisplayTypeSelection.from_catalogue(value, value['entries'][1]['id'])


@pytest.mark.parametrize('before,after', [
    ('success', 'display_reordered'), ('display_ccmx', 'display_ccmx_changed'),
])
def test_changed_native_catalogue_refuses_before_a_read(before, after):
    meter, value = native_catalogue(before)
    selection = DisplayTypeSelection.from_catalogue(value, value['entries'][2]['id'])
    meter.environment['CM_COLORIMETER_FIXTURE'] = after
    with pytest.raises(InstrumentError) as refusal:
        meter.measure('emissive_spot', spectral=False,
                   configuration=Configuration(display_type=selection))
    assert refusal.value.stage == 'display_type_stale'
    assert not any(event['event'] == 'result' for event in refusal.value.events)


def test_named_native_selection_retains_applied_identity_without_guessing_readback_index():
    meter, value = native_catalogue()
    selection = DisplayTypeSelection.from_catalogue(value, value['entries'][0]['id'])
    reading = meter.measure('emissive_spot', spectral=False,
                         configuration=Configuration(display_type=selection))
    types = next(e['display_types'] for e in reading.events if e['event'] == 'connected')
    assert types['applied_choice_id'] == selection.choice_id
    assert 'selected_index' not in types['readback']
    assert types['readback']['base_calibration_id'] == 1


@pytest.mark.parametrize('fixture', [
    'display_invalid_label', 'display_invalid_path', 'display_invalid_both',
])
def test_native_invalid_text_isolated_and_later_index_is_not_renumbered(fixture):
    meter, value = native_catalogue(fixture)
    assert value['status'] == 'available' and value['count'] == 3
    assert [row['available'] for row in value['entries']] == [True, False, True]
    if fixture != 'display_invalid_path':
        assert value['entries'][1]['label'] is None
        assert bytes.fromhex(value['entries'][1]['native_label_bytes']) == b'Invalid \xc0\xaf'
    if fixture != 'display_invalid_label':
        assert value['entries'][1]['source_path'] is None
        assert bytes.fromhex(value['entries'][1]['native_source_path_bytes']).endswith(b'\xff.ccmx')
    selected = DisplayTypeSelection.from_catalogue(value, value['entries'][2]['id'])
    assert selected.index == 2
    reading = meter.measure('emissive_spot', spectral=False,
                          configuration=Configuration(display_type=selected))
    binding = next(event for event in reading.events if event['event'] == 'connected')
    assert binding['display_types']['applied_choice_id'] == selected.choice_id
    # Deliberately bypass catalogue resolution to exercise the native guard.
    disabled = DisplayTypeSelection(1, value['catalogue_id'], value['entries'][1]['id'])
    with pytest.raises(InstrumentError) as refusal:
        meter.measure('emissive_spot', spectral=False,
                      configuration=Configuration(display_type=disabled))
    assert refusal.value.stage == 'display_type_unavailable'
    assert not any(event['event'] == 'result' for event in refusal.value.events)


def test_changed_invalid_native_bytes_refuse_an_otherwise_valid_stale_choice():
    meter, value = native_catalogue('display_invalid_label')
    selected = DisplayTypeSelection.from_catalogue(value, value['entries'][2]['id'])
    meter.environment['CM_COLORIMETER_FIXTURE'] = 'display_invalid_label_changed'
    with pytest.raises(InstrumentError) as refusal:
        meter.measure('emissive_spot', spectral=False,
                      configuration=Configuration(display_type=selected))
    assert refusal.value.stage == 'display_type_stale'


def test_all_invalid_native_entries_keep_default_but_offer_no_override():
    meter, value = native_catalogue('display_invalid_all')
    assert value['status'] == 'available' and len(value['entries']) == 3
    assert not any(row['available'] for row in value['entries'])
    with pytest.raises(ValueError, match='unavailable'):
        DisplayTypeSelection.from_catalogue(value, value['entries'][0]['id'])
    assert meter.measure('emissive_spot', spectral=False).patches[0].xyz is not None


def test_an_installed_correction_cannot_be_silently_overwritten():
    value = observed()
    value['entries'][0].update(kind='ccmx', identity_scope='loaded_correction_data',
                               source_path='/simulated/installed.ccmx')
    with pytest.raises(ValueError, match='alternative selections'):
        Configuration.from_options({'display_type': value['entries'][0]['id'],
                                    'correction': {'text': 'not parsed after conflict'}},
                                   display_types=value)


def test_direct_native_selection_refuses_a_second_correction():
    from color_measurement.colorimeter_correction import CorrectionMatrix

    meter, value = native_catalogue('display_ccmx')
    selection = DisplayTypeSelection.from_catalogue(value, value['entries'][2]['id'])
    matrix = CorrectionMatrix(((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                              instrument='X-Rite i1 DisplayPro')
    with pytest.raises(InstrumentError) as refusal:
        meter.measure('emissive_spot', spectral=False,
            configuration=Configuration(display_type=selection, correction=matrix))
    assert refusal.value.stage == 'display_correction_conflict'
    assert not any(event['event'] == 'result' for event in refusal.value.events)


@pytest.mark.parametrize('applies_in_ambient', [False, True])
def test_installed_ambient_matrix_uses_the_existing_native_driver_policy(applies_in_ambient):
    value = observed()
    value['entries'][0].update(kind='ccmx', identity_scope='loaded_correction_data',
                               source_path='/simulated/installed.ccmx')
    front = InstrumentFrontend(Instrument('/never-started', MODEL),
        mode_capabilities=mode_word('ambient_spot', spectral=False),
        trigger_capabilities=CAPABILITY2['disptype'], filter_capabilities=0,
        provenance='simulated', display_types=value, ambient_ccmx=applies_in_ambient)
    snapshot = json.loads(front.describe_json())
    request = json.dumps({'schema_version': 1, 'device_id': snapshot['device_id'],
        'capability_id': snapshot['capability_id'], 'mode': 'ambient_spot', 'workflow': 'spot',
        'patch_ids': ['A1'], 'layout': {},
        'options': {'display_type': value['entries'][0]['id']}})
    if applies_in_ambient:
        selected = front._validate_request(request)[2].display_type
        assert selected.choice_id == value['entries'][0]['id']
    else:
        with pytest.raises(ValueError, match='installed CCMX in ambient'):
            front.prepare(request)
