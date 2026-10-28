"""Actual bridge client under an inert message peer; no browser or native UI proof."""

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from color_measurement import presentation


def test_renderer_freshness_refuses_changed_source_and_generated_output(tmp_path):
    from color_measurement.desktop_assets_manifest import (
        check_reference,
        check_renderer,
        renderer_sources,
    )

    assets = Path(presentation.__file__).with_name("desktop_assets")
    # The checkout layout: the package beside its builder sources, which the
    # wheel does not ship. Both forms of the check are proved here.
    package = tmp_path / "src" / "color_measurement"
    shutil.copytree(assets.parent, package, ignore=shutil.ignore_patterns("__pycache__"))
    sources = tmp_path / "tools" / "desktop_builder" / "renderer"
    shutil.copytree(renderer_sources(assets), sources)
    for name in ("compiler-identity.json", "package-lock.json"):
        shutil.copyfile(renderer_sources(assets).parent / name, sources.parent / name)
    copy = package / "desktop_assets"
    assert renderer_sources(copy) == sources
    check_renderer(copy, sources)
    check_renderer(copy)
    check_reference(copy)
    for path, check in (
        (sources / "app.js", lambda: check_renderer(copy, sources)),
        (sources / "vendor" / "three.module.js", lambda: check_renderer(copy, sources)),
        (copy / "workspace.js", lambda: check_renderer(copy, sources)),
        (copy / "workspace.js", lambda: check_renderer(copy)),
        (copy / "workspace-manifest.json", lambda: check_renderer(copy)),
        (package / "science.py", lambda: check_reference(copy)),
        (copy / "reference-data.js", lambda: check_reference(copy)),
        (copy / "resources" / "CIE_xyz_1931_2deg.csv", lambda: check_reference(copy)),
    ):
        original = path.read_bytes()
        path.write_bytes(original + b"\n// Changed bytes must invalidate the renderer.\n")
        with pytest.raises(ValueError, match="Bundled .* is stale"):
            check()
        path.write_bytes(original)
    nominal = copy / "nominal-chart-guides.json"
    original = nominal.read_bytes()
    nominal.write_bytes(original + b"\n ")
    for check in (lambda: check_renderer(copy), lambda: check_renderer(copy, sources)):
        with pytest.raises(ValueError, match="Bundled renderer is stale"):
            check()
    nominal.write_bytes(original)
    # An installed wheel carries no sources: it authenticates the bundle it ships
    # and cannot see a source edit, which the checkout form refuses.
    source = sources / "app.js"
    source.write_bytes(source.read_bytes() + b"\n// edited after the bundle was built\n")
    check_renderer(copy)
    with pytest.raises(ValueError, match="Bundled renderer is stale"):
        check_renderer(copy, sources)
    installed = tmp_path / "venv" / "lib" / "site-packages" / "color_measurement" / "desktop_assets"
    assert renderer_sources(installed) is None
    source.unlink()
    with pytest.raises(ValueError, match="entrypoint is missing"):
        renderer_sources(copy)
    shutil.rmtree(sources)
    with pytest.raises(ValueError, match="entrypoint is missing"):
        renderer_sources(copy)


def test_nominal_guides_refuse_scientific_fields_duplicate_keys_and_malformed_shapes(tmp_path):
    from copy import deepcopy

    from color_measurement.desktop_assets_manifest import nominal_guide_identity

    original = Path(presentation.__file__).with_name("desktop_assets")
    path = tmp_path / "nominal-chart-guides.json"
    packet = json.loads((original / path.name).read_bytes())
    path.write_text(json.dumps(packet))
    assert list(nominal_guide_identity(tmp_path)) == [path.name]
    invalid = [None, [], dict(packet, version=True), dict(packet, guides=[])]
    for field, value in (("cells", None), ("cells", [None]), ("rows", True),
                         ("image_sha256", 42), ("xyz", [20, 30, 40])):
        changed = deepcopy(packet)
        changed["guides"]["classic"][field] = value
        invalid.append(changed)
    for field, value in (("index", True), ("rgb8", [True, 20, 30]), ("rgb8", [256, 0, 0]),
                         ("source_xy", [float("nan"), 0]), ("white_xyz", [95, 100, 108])):
        changed = deepcopy(packet)
        changed["guides"]["sg"]["cells"][0][field] = value
        invalid.append(changed)
    for changed in invalid:
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="Nominal guide resource is missing or invalid"):
            nominal_guide_identity(tmp_path)
    path.write_text(json.dumps(packet).replace('"version": 1', '"version": 1, "version": 1'))
    with pytest.raises(ValueError, match="Nominal guide resource is missing or invalid"):
        nominal_guide_identity(tmp_path)


def test_generation_refuses_a_spoofed_launcher_and_changed_native_compiler(tmp_path):
    import json
    import runpy

    root = Path(__file__).resolve().parents[1]
    generator = runpy.run_path(str(root / "tools" / "generate_desktop_assets.py"))
    generate = generator["generate_renderer"]
    original = root / "tools" / "desktop_builder"
    builder = tmp_path / "builder"
    sources = builder / "renderer"
    sources.mkdir(parents=True)
    for name in ("compiler-identity.json", "package-lock.json"):
        shutil.copyfile(original / name, builder / name)
    identity = json.loads((builder / "compiler-identity.json").read_text())
    launcher = builder / "node_modules" / "esbuild" / "bin" / "esbuild"
    binary = builder / "node_modules" / identity["package"] / identity["binary_path"]
    launcher.parent.mkdir(parents=True)
    binary.parent.mkdir(parents=True)
    launcher.write_text("Version-only launcher is never executed\n")
    binary.write_text("Changed native compiler is never executed\n")
    with pytest.raises(ValueError, match="locked desktop builder"):
        generate(tmp_path, sources, tmp_path / "version-only-esbuild")
    with pytest.raises(ValueError, match="Native compiler bytes differ"):
        generate(tmp_path, sources, launcher)
    assert not (tmp_path / "workspace.js").exists()


def test_delayed_owner_return_reuses_mutation_uuid_and_dialog_wait_has_no_deadline():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the inert JavaScript bridge proof")
    client = (
        Path(__file__).resolve().parents[1] / "tools" / "desktop_builder" / "renderer" / "owner.js"
    )
    script = r"""
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
const callbacks=new Map(),messages=[],effects=new Map();
let timerId=0;
const context={crypto:{randomUUID},
  setTimeout(fn){callbacks.set(++timerId,fn);return timerId;},
  clearTimeout(id){callbacks.delete(id);},
  window:{ownerTransport:{isReady:true,postMessage(message){
    messages.push(message);
    if(message.method==='command'){
      const key=message.params.command_id;
      if(!effects.has(key)){effects.set(key,message.params);return;}
      assert.deepEqual(message.params,effects.get(key));
      queueMicrotask(()=>context.window.ownerReply({id:message.id,ok:true,
        value:{admission:'admitted',state:'completed'}}));
    }
  }}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replaceAll('export ',''),context);
const future=vm.runInContext(
  "command({generation:2,batch_id:'b'},'measure',{draft_version:3})",context);
assert.equal(effects.size,1);
for(const [id,callback] of [...callbacks.entries()]){callbacks.delete(id);callback();}
await future;
assert.equal(effects.size,1);
assert.equal(messages.filter(m=>m.method==='command').length,2);
assert.notEqual(messages[0].id,messages[1].id);
assert.equal(messages[0].params.command_id,messages[1].params.command_id);
for(const method of ['create_batch','another_folder','review']){
  const dialog=vm.runInContext(`call('${method}',{})`,context);
  assert.equal(callbacks.size,0,'Human dialog must wait without an owner deadline');
  const message=messages.at(-1);
  context.window.ownerReply({id:message.id,ok:true,value:null});
  assert.equal(await dialog,null);
}
console.log('Lost-return effect identity and human-dialog wait passed');
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(client)],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout


#: A hang guard only: each integration case keeps its own limit. The cohort
#: took 185-276 s under other lanes' load and tripped a 300 s bound, so it takes
#: the gate's shared pytest-stage deadline and never cuts off a cohort the stage
#: still allows. ``test_test_all`` fails if the two differ.
RENDERER_COHORT_SECONDS = 1800


def _renderer_test_result(test_file, *flags):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for renderer execution acceptance")
    reporter = (Path(__file__).resolve().parents[1]
                / "tools/desktop_builder/renderer-test-reporter.mjs")
    return subprocess.run(
        [node, "--test", f"--test-reporter={reporter.as_uri()}", *flags, str(test_file)],
        text=True,
        capture_output=True,
        timeout=RENDERER_COHORT_SECONDS,
        env=dict(os.environ, CM_RENDERER_PYTHON=sys.executable),
    )


JOINED_RENDERER_LEAVES = (
    "actual Argyll C path connects cr250 through the renderer and saved review",
    "actual Argyll C path connects cr100 through the renderer and saved review",
    "actual Argyll C path connects pr655 through the renderer and saved review",
    "actual Argyll C path retains cr250_native_error without a false reading",
    "actual i1Pro3 renderer joins configured modes new batch and saved review",
)


def _joined_renderer_case_count(receipt, *, required):
    """Check exact leaf identities in the existing accepted Node receipt."""
    names = Counter(case["name"] for case in receipt["named_leaf_cases"])
    if required:
        incorrect = {name: names[name] for name in JOINED_RENDERER_LEAVES if names[name] != 1}
        assert not incorrect, f"Missing or duplicated required joined renderer leaves: {incorrect}"
    return sum(names[name] for name in JOINED_RENDERER_LEAVES)


@pytest.mark.parametrize("fault", ["missing", "duplicated", "substituted", "duplicate_instead"])
def test_joined_renderer_coverage_requires_every_exact_leaf_once(fault, monkeypatch):
    names = list(JOINED_RENDERER_LEAVES)
    if fault == "missing":
        names.pop()
    elif fault == "duplicated":
        names.append(names[0])
    elif fault == "substituted":
        names[-1] = "actual Argyll C path an unrelated case"
    else:
        names[-1] = names[0]  # Five names still do not prove five required leaves.
    receipt = {"accepted": True, "named_leaf_cases": [{"name": name} for name in names]}
    # Exercise the actual publishing anchor: a detached validator would not stop
    # the gate from accepting a count produced without checking these identities.
    monkeypatch.setenv("CM_RENDERER_NATIVE_REPORT", "recording-only; no worker runs")
    monkeypatch.setitem(globals(), "_renderer_test_result", lambda *_: subprocess.CompletedProcess(
        [], 0, stdout=json.dumps(receipt), stderr=""))
    with pytest.raises(AssertionError, match="required joined renderer leaves"):
        test_actual_renderer_dialog_identity_and_camera_behaviors(lambda *_: None)


def test_joined_renderer_coverage_accepts_all_five_and_keeps_partial_scope_explicit():
    receipt = {"named_leaf_cases": [{"name": name} for name in JOINED_RENDERER_LEAVES]}
    assert _joined_renderer_case_count(receipt, required=True) == 5
    assert _joined_renderer_case_count({"named_leaf_cases": []}, required=False) == 0


def test_actual_renderer_dialog_identity_and_camera_behaviors(record_property):
    """Actual renderer execution with jsdom; no layout, WebGL or native AX proof."""
    node = shutil.which("node")
    builder = Path(__file__).resolve().parents[1] / "tools" / "desktop_builder"
    if node is None or not (builder / "node_modules" / "jsdom").is_dir():
        pytest.skip("Install the locked desktop builder dependencies for renderer DOM checks")
    result = _renderer_test_result(builder / "renderer.test.mjs")
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True and receipt["named_leaf_cases"]
    # The full gate requires the number of executed renderer leaves, not only
    # the passing Python anchor that represents their execution.
    record_property("renderer_cases", len(receipt["named_leaf_cases"]))
    # Registered only with CM_RENDERER_NATIVE_REPORT; the full form requires them.
    record_property("renderer_native_cases", _joined_renderer_case_count(
        receipt, required=bool(os.environ.get("CM_RENDERER_NATIVE_REPORT"))))
    print(result.stdout, end="")


@pytest.mark.parametrize("source,flags", [
    ("", []),
    ("import test from 'node:test'; test('first named case', () => {});", [
        "--test-name-pattern=absent case",
    ]),
    ("import test from 'node:test'; test('first named case', () => {}); "
     "test('other named case', () => {});", ["--test-name-pattern=first named case"]),
    ("import test from 'node:test'; test('first named case', () => {}); "
     "test('other named case', () => {});", ["--test_name_pattern=first named case"]),
    ("import test from 'node:test'; test.skip('skipped case', () => {});", []),
    ("import test from 'node:test'; test.todo('todo case');", []),
    ("import {describe} from 'node:test'; describe('empty suite', () => {});", []),
    ("import test from 'node:test'; test('executed case', () => {}); "
     "test.skip('skipped case', () => {});", []),
    ("import test from 'node:test'; test('executed case', () => {}); test.todo('todo case');", []),
    ("import test from 'node:test'; test('executed case', () => {}); "
     "test('cancelled case', {timeout: 10}, async () => new Promise(() => {}));", []),
], ids=["empty", "filtered-all", "filtered-some", "filtered-underscore", "skipped", "todo",
        "suite-only", "mixed-skip", "mixed-todo", "cancelled"])
def test_renderer_acceptance_refuses_empty_filtered_or_unexecuted_cases(tmp_path, source, flags):
    fixture = tmp_path / "renderer.test.mjs"
    fixture.write_text(source)
    result = _renderer_test_result(fixture, *flags)
    assert result.returncode != 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is False
    if flags:
        assert receipt["filtered"] is True
    if "cancelled case" in source:
        assert receipt["counts"]["cancelled"] > 0


@pytest.mark.parametrize("source", [
    "",
    "import test from 'node:test'; test('first named case', () => process.exit(0)); "
    "test('unexecuted case', () => {});",
], ids=["empty", "early-exit"])
def test_renderer_acceptance_refuses_relative_file_only_success(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    fixture = Path("renderer.test.mjs")
    fixture.write_text(source)
    result = _renderer_test_result(fixture)
    assert result.returncode != 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is False and not receipt["named_leaf_cases"]


def test_renderer_acceptance_requires_successful_named_leaf_events(tmp_path):
    fixture = tmp_path / "renderer.test.mjs"
    fixture.write_text("import test from 'node:test'; import assert from 'node:assert/strict'; "
                       "test('named parent', async t => { await t.test('named leaf', () => "
                       "assert.equal(2 + 2, 4)); }); test('other leaf', () => assert.ok(true));")
    result = _renderer_test_result(fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert [case["name"] for case in receipt["named_leaf_cases"]] == ["named leaf", "other leaf"]
    fixture.write_text("import test from 'node:test'; test('failed leaf', () => { "
                       "throw Error('finite named failure'); });")
    failed = _renderer_test_result(fixture)
    assert failed.returncode != 0
    assert "finite named failure" in failed.stdout + failed.stderr


@pytest.mark.parametrize("selector", [
    "--test-name-pattern=first", "--test-skip-pattern=second", "--test-only", "--test-shard=1/2",
])
def test_renderer_acceptance_refuses_environment_selectors(tmp_path, monkeypatch, selector):
    fixture = tmp_path / "renderer.test.mjs"
    fixture.write_text("import test from 'node:test'; test('first', () => {}); "
                       "test('second', () => {});")
    monkeypatch.setenv("NODE_OPTIONS", selector)
    result = _renderer_test_result(fixture)
    assert result.returncode != 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is False and receipt["filtered"] is True


@pytest.mark.parametrize("channel", ["execArgv", "NODE_OPTIONS"])
@pytest.mark.parametrize("selector", [
    "--experimental-test-tag-filter=selected", "--test-rerun-failures=/owned/prior.json",
])
def test_renderer_acceptance_refuses_selectors_on_actual_runtime_events(
    tmp_path, selector, channel,
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for renderer reporter selector checks")
    reporter = (Path(__file__).resolve().parents[1]
                / "tools/desktop_builder/renderer-test-reporter.mjs")
    fixture = tmp_path / "renderer.test.mjs"
    fixture.write_text("import test from 'node:test'; test('first named case', () => {}); "
                       "test('other named case', () => {});")
    # Capture real unfiltered events first, then inject the selector only at
    # the acceptance boundary. This avoids depending on tag-filter support or
    # reading/writing a failure-replay file in the running Node version.
    script = r"""
import assert from 'node:assert/strict';
import {run} from 'node:test';
const [reporterPath, fixture, selector, channel] = process.argv.slice(2);
const {default: reporter} = await import(reporterPath);
const events = [];
for await (const event of run({files: [fixture]})) events.push(event);
let baseline;
for await (const output of reporter(events)) baseline = JSON.parse(output);
assert.equal(baseline.accepted, true);
assert.equal(baseline.named_leaf_cases.length, 2);
if (channel === 'NODE_OPTIONS') process.env.NODE_OPTIONS = selector;
else process.execArgv.push(selector);
for await (const output of reporter(events)) process.stdout.write(output);
"""
    driver = tmp_path / "reporter-check.mjs"
    driver.write_text(script)
    result = subprocess.run(
        [node, str(driver), reporter.as_uri(), str(fixture), selector, channel],
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is False and receipt["filtered"] is True
    assert len(receipt["named_leaf_cases"]) == 2


def test_renderer_acceptance_uses_common_node_plan_and_outcome_events_without_test_ids():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for renderer reporter event checks")
    reporter = (Path(__file__).resolve().parents[1]
                / "tools/desktop_builder/renderer-test-reporter.mjs")
    # Node 22's producer has nesting/location/plan/outcome/summary fields but
    # does not supply the newer testId, parentId or entryFile identifiers.
    script = r"""
const {default: reporter} = await import(process.argv[1]);
const location = {file: '/renderer.test.mjs', line: 8, column: 1};
const pass = (name, nesting, kind = 'test') => ({type: 'test:pass', data: {
  name, nesting, ...location, details: {type: kind, duration_ms: 1}
}});
const events = [pass('nested leaf', 2),
  {type: 'test:plan', data: {nesting: 2, ...location, count: 1}},
  pass('nested parent', 1),
  {type: 'test:plan', data: {nesting: 1, ...location, count: 1}},
  pass('top parent', 0), pass('other leaf', 0),
  {type: 'test:summary', data: {success: true, counts: {
    tests: 4, failed: 0, passed: 4, cancelled: 0, skipped: 0, todo: 0
  }}}
];
for await (const output of reporter(events)) process.stdout.write(output);
"""
    result = subprocess.run([node, "--input-type=module", "-e", script, reporter.as_uri()],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert [case["name"] for case in receipt["named_leaf_cases"]] == ["nested leaf", "other leaf"]
