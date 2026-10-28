"""Source-local identity coverage for patch recipes and imported repairs.

IDs describe repair groups, not a count of independent defects. This check
is self-contained and does not depend on an external inventory or checkout.
"""

import ast
import copy
import dataclasses
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from native_argyll.contribution import load_series
from native_argyll.proof_inputs import fixture_manifest

ROOT = Path(__file__).resolve().parents[1] / "tools/native_argyll"
CATALOGUE = json.loads((ROOT / "repair_catalogue.json").read_text())


def validate(fixtures, catalogue, series):
    covered, proof_names = set(), set()
    modules = {}
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        modules[path.stem] = tree
        transforms = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and (node.name == "patch" or node.name.endswith("_patch"))
        ]
        if not transforms:
            continue
        identities = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and any(
                isinstance(target, ast.Name) and target.id == "REPAIR_ID" for target in node.targets
            )
        ]
        assert len(identities) == 1, f"Unmapped recipe module: {path.name}"
        identity = identities[0]
        assert identity in catalogue, f"Unknown recipe identity: {identity}"
        assert catalogue[identity]["module"] == path.stem, identity
        if catalogue[identity]["implementation"] == "application-policy":
            covered.add(identity)
    # Explicit source producers cover generated adapters and root policies that
    # do not follow the named patch-function convention. This is a declared
    # preparation census, not semantic inference from arbitrary Python code.
    for identity, owner in catalogue.items():
        if "functions" not in owner:
            continue
        path = (
            ROOT.parent / "argyll_native_check.py"
            if owner["module"] == ("argyll_native_check")
            else ROOT / (owner["module"] + ".py")
        )
        names = {
            node.name
            for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef)
        }
        assert set(owner["functions"]) <= names, identity
        covered.add(identity)
    for identity, attribution in fixtures["repair_ids"].items():
        assert identity in catalogue, f"Unmapped repair: {identity}"
        owner = catalogue[identity]
        assert owner["implementation"] == "argyll-source", identity
        assert attribution["function"] == owner["function"], identity
        assert set(attribution["paths"]) == set(owner["paths"]), identity
    expected_fixtures = {
        identity for identity in catalogue if identity.startswith("native-resync:")
    }
    assert set(fixtures["repair_ids"]) == expected_fixtures, "Uncovered fixture IDs"
    for identity, owner in catalogue.items():
        if owner["implementation"] != "argyll-source" or "module" not in owner:
            continue
        module = owner["module"]
        assert module in modules, f"Missing proof module: {module}"
        names = {node.name for node in modules[module].body if isinstance(node, ast.FunctionDef)}
        assert "prove" in names, f"Missing proof entrypoint: {module}"
        declared = owner.get("proofs", [module])
        paths = set()
        for name in declared:
            assert name in fixtures["proofs"], f"Missing frozen proof: {name}"
            paths.update(fixtures["proofs"][name])
            proof_names.add(name)
        assert paths == set(owner["paths"]), f"Misattributed proof paths: {identity}"
    assert proof_names == fixtures["proofs"].keys(), "Unmapped frozen proof inputs"
    assert len(series) == 2, "Uncovered repair IDs: both source projections are required"
    for source in series:
        paths = {entry.path for entry in source.entries}
        assert source.source_origin["repository"] == "argyll-contributions"
        if source.name == "argyll-repairs":
            declared = {
                path
                for owner in catalogue.values()
                if owner["implementation"] == "argyll-source"
                for path in owner["paths"]
            }
            assert paths == declared, "Unmapped imported repair paths"
        for identity, owner in catalogue.items():
            if owner["implementation"] != "argyll-source":
                continue
            # One file can contain many repairs and a repair can span files.
            # Every projection must import all declared source paths. The strict
            # manifest authenticates each selected postimage independently;
            # CR/PR shared registrations/selectors intentionally differ.
            assert set(owner["paths"]) <= paths, f"Uncovered repair IDs: {identity}"
            covered.add(identity)
    assert covered == catalogue.keys(), f"Uncovered repair IDs: {catalogue.keys() - covered}"


def sources():
    return [load_series(ROOT / "contributions" / name) for name in ("repairs", "cr_pr")]


def test_expanded_recipes_and_imports_have_declared_identity():
    validate(fixture_manifest(), CATALOGUE, sources())


def projection_parity(repairs, cr_pr):
    """A relocated repair imported by both projections has one postimage.

    The two recipes come from different source commits, so a repair advanced on
    one line only would give the generic and CR/PR builds different code for it.
    Shared registration files carry the contribution's provenance and may differ.
    """
    generic = {entry.path: entry for entry in repairs.entries}
    combined = {entry.path: entry for entry in cr_pr.entries}
    assert generic.keys() <= combined.keys(), "CR/PR projection omits a relocated repair"
    shared = [path for path in generic if generic[path].provenance == combined[path].provenance]
    assert shared, "No relocated repair is imported by both projections"
    assert [
        path for path in shared if generic[path].post_sha256 != combined[path].post_sha256
    ] == [], "Projections import different postimages of the same repair"


def test_both_projections_import_the_same_relocated_repairs():
    projection_parity(*sources())


def test_a_repair_advanced_in_one_projection_is_refused():
    repairs, cr_pr = sources()
    changed = list(cr_pr.entries)
    index = next(i for i, entry in enumerate(changed) if entry.path == "spectro/i1pro3.c")
    changed[index] = dataclasses.replace(changed[index], post_sha256="0" * 64)
    with pytest.raises(AssertionError, match="different postimages"):
        projection_parity(repairs, dataclasses.replace(cr_pr, entries=tuple(changed)))
    with pytest.raises(AssertionError, match="omits a relocated repair"):
        projection_parity(
            repairs,
            dataclasses.replace(
                cr_pr,
                entries=tuple(entry for entry in cr_pr.entries if entry.path != "spectro/i1pro3.c"),
            ),
        )


@pytest.mark.parametrize("mutation", ["missing", "unknown", "wrong-function", "wrong-path"])
def test_unmapped_or_misattributed_replacement_is_rejected(mutation):
    fixtures = copy.deepcopy(fixture_manifest())
    identities = fixtures["repair_ids"]
    identity = next(iter(identities))
    entry = identities[identity]
    if mutation == "missing":
        identities[None] = identities.pop(identity)
    elif mutation == "unknown":
        identities["unregistered-repair"] = identities.pop(identity)
    elif mutation == "wrong-function":
        entry["function"] = "unrelated_function"
    else:
        entry["paths"] = ["spectro/unrelated.c"]
    with pytest.raises(AssertionError):
        validate(fixtures, CATALOGUE, sources())


def test_an_adopted_id_cannot_lose_its_only_implementation():
    fixtures = copy.deepcopy(fixture_manifest())
    del fixtures["repair_ids"][next(iter(fixtures["repair_ids"]))]
    with pytest.raises(AssertionError, match="Uncovered fixture IDs"):
        validate(fixtures, CATALOGUE, sources())


def test_both_source_projections_are_required():
    with pytest.raises(AssertionError, match="both source projections are required"):
        validate(fixture_manifest(), CATALOGUE, [])


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("repairs-undeclared-path", "Unmapped imported repair paths"),
        ("repairs-missing-path", "Unmapped imported repair paths"),
        ("cr-pr-missing-path", "Uncovered repair IDs: argyll:huey-measurement-type"),
    ],
)
def test_imported_repair_cannot_silently_lose_coverage(mutation, message):
    repairs, cr_pr = sources()
    if mutation == "repairs-undeclared-path":
        # A new imported file with no declared identity is an unmapped repair.
        extra = dataclasses.replace(repairs.entries[0], path="spectro/undeclared.c")
        repairs = dataclasses.replace(repairs, entries=(*repairs.entries, extra))
    elif mutation == "repairs-missing-path":
        repairs = dataclasses.replace(
            repairs,
            entries=tuple(entry for entry in repairs.entries if entry.path != "spectro/huey.c"),
        )
    else:
        # The combined projection must import every declared repair path too.
        cr_pr = dataclasses.replace(
            cr_pr, entries=tuple(entry for entry in cr_pr.entries if entry.path != "spectro/huey.c")
        )
    with pytest.raises(AssertionError, match=message):
        validate(fixture_manifest(), CATALOGUE, [repairs, cr_pr])


def test_a_new_patch_module_cannot_omit_its_identity(tmp_path, monkeypatch):
    (tmp_path / "new_repair.py").write_text("def patch(text):\n    return text\n")
    monkeypatch.setitem(validate.__globals__, "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="Unmapped recipe module: new_repair.py"):
        validate(fixture_manifest(), CATALOGUE, [])


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("missing-module", "Missing proof module"),
        ("missing-proof", "Missing frozen proof"),
        ("wrong-path", "Misattributed proof paths"),
    ],
)
def test_named_proof_ownership_cannot_silently_change(mutation, message):
    fixtures = copy.deepcopy(fixture_manifest())
    catalogue = copy.deepcopy(CATALOGUE)
    if mutation == "missing-module":
        catalogue["recipe:ccss-cgats"]["module"] = "unregistered_proof"
    elif mutation == "missing-proof":
        del fixtures["proofs"]["ccss_cgats"]
    else:
        fixtures["proofs"]["ccss_cgats"] = {"spectro/unrelated.c": {}}
    with pytest.raises(AssertionError, match=message):
        validate(fixtures, catalogue, sources())


def test_a_new_policy_module_cannot_use_an_unknown_identity(tmp_path, monkeypatch):
    (tmp_path / "new_policy.py").write_text(
        "REPAIR_ID = 'unregistered-policy'\ndef patch(text):\n    return text\n"
    )
    monkeypatch.setitem(validate.__globals__, "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="Unknown recipe identity: unregistered-policy"):
        validate(fixture_manifest(), CATALOGUE, [])


def test_proof_modules_do_not_import_retired_transform_owners():
    retired = {"native_resync", "native_resync_edits"}
    for path in [*ROOT.glob("*.py"), ROOT.parent / "argyll_native_check.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
                if node.module == "native_argyll":
                    imports.extend(alias.name for alias in node.names)
            assert not any(name.rsplit(".", 1)[-1] in retired for name in imports), (
                f"Retired source-transform dependency: {path.name}:{node.lineno}"
            )


def test_imported_calibration_repair_never_uses_the_historical_transform(
    argyll_archive,
    tmp_path,
    monkeypatch,
):
    import argyll_native_check as native
    from native_argyll import spyder_calibration_slots as fixture

    def forbidden(*args, **kwargs):
        pytest.fail("Historical repair fixture entered production composition")

    for name in ("native_argyll.native_resync", "native_argyll.native_resync_edits"):
        monkeypatch.setitem(sys.modules, name, None)
    for owner in CATALOGUE.values():
        if owner["implementation"] != "argyll-source" or "module" not in owner:
            continue
        module = importlib.import_module("native_argyll." + owner["module"])
        for name, value in vars(module).copy().items():
            if callable(value) and (name == "patch" or name.endswith("_patch")):
                monkeypatch.setattr(module, name, forbidden)
    from native_argyll import driver_manifest

    for name in ("huey_measurement_type_patch", "standalone_header_patch"):
        if hasattr(driver_manifest, name):
            monkeypatch.setattr(driver_manifest, name, forbidden)
    source, _ = native.prepare(argyll_archive, tmp_path / "prepared")
    imported = next(entry for entry in sources()[0].entries if entry.path == fixture.SOURCE_PATH)
    assert fixture.digest((source / fixture.SOURCE_PATH).read_bytes()) == imported.post_sha256


def test_proof_inputs_accept_a_comment_in_actual_production_source(argyll_archive, tmp_path):
    import argyll_native_check as native

    output = tmp_path / "production"
    source, report = native.prepare(argyll_archive, output)
    path = source / "spectro/i1pro_imp.c"
    path.write_bytes(path.read_bytes() + b"\n/* Reviewed production comment. */\n")
    native.prepare_proof_inputs(argyll_archive, source, output, report)
    stage = report["native_resync_repair"]
    assert (
        stage["after_sha256"]["spectro/i1pro_imp.c"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert path.read_bytes().endswith(b"/* Reviewed production comment. */\n")


def test_tampered_predecessor_cannot_enter_the_preparation_report(
    argyll_archive, tmp_path, monkeypatch
):
    import argyll_native_check as native
    from native_argyll import proof_inputs

    output = tmp_path / "production"
    source, report = native.prepare(argyll_archive, output)
    fixtures = tmp_path / "frozen-inputs"
    shutil.copytree(proof_inputs.FIXTURES, fixtures)
    entry = fixture_manifest()["proofs"]["native_resync"]["spectro/i1pro_imp.c"]
    blob = fixtures / entry["blob"]
    blob.write_bytes(blob.read_bytes() + b"changed")
    monkeypatch.setattr(proof_inputs, "FIXTURES", fixtures)
    with pytest.raises(ValueError, match="Proof predecessor blob differs from its digest"):
        native.prepare_proof_inputs(argyll_archive, source, output, report)
    assert "native_resync_repair" not in report, "Unverified predecessors entered proof report"
