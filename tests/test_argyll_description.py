"""Adversarial generic description boundary checks without native instruments."""

import copy
import hashlib
import importlib.util
import json
import platform
import sys
import zipfile
from pathlib import Path

import pytest
from conftest import native_display_unsupported

import color_measurement.argyll_description as api
from color_measurement.argyll_capability import baseline_mode_words
from color_measurement.native_protocol import ProtocolError
from color_measurement.native_receipt import native_worker_name


@pytest.fixture
def candidate():
    return {"path": "fixture:display:1", "dtype": 19, "transport": 4,
            "initialization_assessed": True, "initialization_refusal": None}


@pytest.mark.parametrize("transport, accepted", [
    (0x0001, True), (0x00ff, True), (0xffff, True),
    (0x0100, False),    # low byte zero: no transport class
    (0x10000, False),   # also low byte zero
    (0x10001, False),   # low byte set, above the 16-bit word: only the bound refuses it
    (0x1ffff, False),
    (-1, False), (True, False), (1.0, False), ("4", False),
])
def test_candidate_transport_is_an_exact_16_bit_word_with_a_transport_class(
    candidate, transport, accepted,
):
    """The two transport checks fail for different reasons and each must be able to
    fail alone: 0x10001 passes the low-byte check and is refused only by the 16-bit
    bound."""
    from color_measurement.argyll_description import validate_candidate

    value = {**candidate, "transport": transport}
    if accepted:
        assert validate_candidate(value) is value
    else:
        with pytest.raises(ProtocolError, match="Invalid candidate metadata"):
            validate_candidate(value)


@pytest.fixture
def events(candidate):
    base = {"schema_version": 1, "provenance": "simulated", "backend_version": "3.5.0"}
    return [
        {**base, "event": "started", "operation": "describe", "initialization_refusals": []},
        {**base, "event": "description", "candidate": candidate, "initialized_dtype": 19,
         "native_identity": None,
         "display_types": native_display_unsupported(),
         "serial": None, "access_scope": "selected_device_active_initialization",
         "capabilities": {"primary": 0x10042, "secondary": 0, "tertiary": 0xffffffff},
         "baseline_mode_checks": {"scope": "initialized_before_mode_configuration",
                                  "mode_capabilities": 0x10042,
                                  "checks": [{"mode_word": word, "native_code": 0}
                                             for word in baseline_mode_words(0x10042)]},
         "current_configuration": {"status": "unavailable", "native_code": 0x50000,
                                   "index": None, "modes": None, "calibration_conditions": None},
         "strip_policies": {name: {"max_patches": 0, "requirement": None, "layout_fields": []}
                            for name in ("reflection", "transmission", "emission")},
         "application_measurement_routes": []},
        {**base, "event": "terminal", "status": "completed", "initialization_performed": True,
         "initialization_completed": True, "active_access_attempted": True,
         "callback_refused": False, "cleanup": "destructor_returned",
         "software_stopped": True, "instrument_state": "unknown"},
    ]


@pytest.fixture
def transport(tmp_path, monkeypatch):
    helper = tmp_path / native_worker_name("description-stub-worker")
    helper.write_bytes(b"Not executable; substituted by test transport")
    calls, supplied = [], []

    class FakeSession:
        def __init__(self, command, **kwargs):
            calls.append({"command": command, **kwargs})
            operation_id = command[command.index("--operation-id") + 1]
            self.messages = iter([
                {"operation_id": operation_id, "sequence": i, **copy.deepcopy(event)}
                for i, event in enumerate(supplied)
            ])

        def receive_message(self):
            return next(self.messages)

        def finish_messages(self, *, expected_returncode=0):
            calls[-1]["expected_returncode"] = expected_returncode

        def __enter__(self):
            return self

        def __exit__(self, *_):
            calls[-1]["closed"] = True

    monkeypatch.setattr(api, "HelperSession", FakeSession)
    return api.NativeDescriptionClient.for_offline_tests(helper), supplied, calls, helper


def test_preserves_unknown_identity_raw_masks_and_configuration(transport, candidate, events):
    client, supplied, calls, _ = transport
    supplied.extend(events)
    result = json.loads(client.describe_json(json.dumps(candidate)))
    assert result["data"]["serial"] is None
    assert result["data"]["capabilities"]["tertiary"] == 0xffffffff
    assert result["data"]["current_configuration"]["modes"] is None
    assert result["data"]["application_measurement_routes"] == []
    assert result["backend"]["build_report_sha256"] is None
    assert calls[0]["command"][1:3] == ["--fixture", "success"]
    assert calls[0]["expected_returncode"] == 0 and calls[0]["closed"]


IDENTITY = "Wrong metadata event identity, version or sequence"
INITIALIZED = "Invalid initialized identity or application routes"


# A bad primary mask also disagrees with the baseline checks; each case names the
# refusal it must reach first.
@pytest.mark.parametrize("change,refusal", [
    ({"schema_version": True}, IDENTITY), ({"provenance": "physical"}, IDENTITY),
    ({"backend_version": "3.4.0"}, IDENTITY), ({"sequence": 999}, IDENTITY),
    ({"operation_id": "foreign"}, IDENTITY),
    ({"event": "measurement"}, "Unexpected or repeated native metadata event"),
    ({"initialized_dtype": 23}, INITIALIZED), ({"initialized_dtype": True}, INITIALIZED),
    ({"serial": ""}, INITIALIZED), ({"serial": "\ud800"}, INITIALIZED),
    ({"application_measurement_routes": ["measure"]}, INITIALIZED),
    ({"capabilities": {"primary": True, "secondary": 0, "tertiary": 0}},
     "Invalid raw capability masks"),
    ({"capabilities": {"primary": -1, "secondary": 0, "tertiary": 0}},
     "Invalid raw capability masks"),
    ({"ambient_ccmx": 1}, INITIALIZED), ({"ambient_matrix_threshold": True}, INITIALIZED),
    ({"ambient_matrix_threshold": 0}, INITIALIZED),
    ({"ambient_matrix_threshold": "5"}, INITIALIZED),
    ({"baseline_mode_checks": None}, "Invalid baseline mode checks"),
    ({"baseline_mode_checks": {}}, "Invalid baseline mode checks"),
])
def test_refuses_bad_or_cross_family_description(transport, candidate, events, change, refusal):
    client, supplied, calls, _ = transport
    events[1].update(change)
    supplied.extend(events)
    with pytest.raises(ProtocolError, match=refusal):
        client.describe_json(json.dumps(candidate))
    assert calls[0]["closed"]
    assert len(client.last_events) == 1


@pytest.mark.parametrize("field,value", [
    ("status", "failed"), ("cleanup", "not_constructed"), ("callback_refused", True),
    ("cleanup", []), ("cleanup", {}),
    ("initialization_completed", False), ("initialization_performed", False),
    ("active_access_attempted", False), ("software_stopped", False),
    ("instrument_state", "ready"),
])
def test_refuses_inconsistent_terminal_but_keeps_description(
    transport, candidate, events, field, value,
):
    client, supplied, calls, _ = transport
    events[-1][field] = value
    supplied.extend(events)
    with pytest.raises(ProtocolError):
        client.describe_json(json.dumps(candidate))
    assert client.last_events[-1]["event"] == "description"
    assert calls[0]["closed"]


def test_checked_identity_refinement_is_separate_from_passive_identity(
    transport, candidate, events,
):
    client, supplied, _, _ = transport
    candidate["dtype"] = 22
    events[1]["candidate"] = candidate
    events[1]["initialized_dtype"] = 21
    supplied.extend(events)
    result = json.loads(client.describe_json(json.dumps(candidate), expected_initialized_dtype=21))
    assert result["data"]["candidate"]["dtype"] == 22
    assert result["data"]["initialized_dtype"] == 21


def test_native_refusal_returns_failure_without_retry_or_readiness(transport, candidate, events):
    client, supplied, calls, _ = transport
    events[1] = {**events[0], "event": "error", "stage": "initialization_refused",
                 "native_code": 0, "refusal_reason": "initialization_downloads_pld"}
    events[2].update(status="failed", initialization_performed=False,
                     initialization_completed=False, active_access_attempted=False,
                     cleanup="not_constructed")
    supplied.extend(events)
    result = json.loads(client.describe_json(json.dumps(candidate)))
    assert result["status"] == "failed" and result["data"] is None
    assert len(calls) == 1 and calls[0]["expected_returncode"] == 1
    assert result["events"][-1]["instrument_state"] == "unknown"


@pytest.mark.parametrize("dtype", [28, 29, 30])
def test_spyder_sensor_read_refusal_preserves_reason_and_inactive_state(
    transport, candidate, events, dtype,
):
    client, supplied, calls, _ = transport
    reason = "initialization_reads_sensor"
    candidate.update(dtype=dtype, transport=2, initialization_refusal=reason)
    events[0]["initialization_refusals"] = [{"dtype": dtype, "reason": reason}]
    events[1] = {**events[0], "event": "error", "stage": "initialization_refused",
                 "native_code": 0, "refusal_reason": reason}
    events[2].update(status="failed", initialization_performed=False,
                     initialization_completed=False, active_access_attempted=False,
                     cleanup="not_constructed")
    supplied.extend(events)
    result = json.loads(client.describe_json(json.dumps(candidate)))
    assert result["status"] == "failed" and result["data"] is None
    assert result["error"]["refusal_reason"] == reason
    assert result["events"][-1]["active_access_attempted"] is False
    assert len(calls) == 1 and calls[0]["expected_returncode"] == 1


def test_passive_candidates_preserve_distinct_instances(transport, candidate, events):
    client, supplied, _, _ = transport
    events[0]["operation"] = "list_passive"
    events[1] = {**events[0], "event": "candidates", "initialization_performed": False,
                 "discovery_scope": "os_path_metadata_without_protocol_identification",
                 "candidates": [candidate, dict(candidate, path="fixture:display:2")]}
    events[-1].update(initialization_performed=False, initialization_completed=False,
                      active_access_attempted=False, cleanup="not_constructed")
    supplied.extend(events)
    result = json.loads(client.list_candidates_json())
    assert len(result["data"]["candidates"]) == 2


@pytest.mark.parametrize("arguments,refusal", [
    ({"allow_serial_identification": 1}, "allow_serial_identification must be bool"),
    # Falsy but not a bool: only the type check refuses it.
    ({"allow_serial_identification": 0}, "allow_serial_identification must be bool"),
    ({"expected_serial": ""}, "expected_serial must be nonempty"),
    ({"expected_initialized_dtype": True}, "expected_initialized_dtype must be a positive"),
    ({"expected_initialized_dtype": 0}, "expected_initialized_dtype must be a positive"),
    ({"allow_serial_identification": True}, "Serial identification needs unknown candidate"),
])
def test_bad_selection_is_rejected_before_subprocess(transport, candidate, arguments, refusal):
    client, _, calls, _ = transport
    with pytest.raises(ValueError, match=refusal):
        client.describe_json(json.dumps(candidate), **arguments)
    assert calls == []


@pytest.mark.parametrize("selected,expected", [(23, 39), (21, 22), (22, 23), (19, 5)])
def test_known_candidate_refuses_an_unrelated_expected_type_before_subprocess(
    transport, candidate, selected, expected,
):
    client, _, calls, _ = transport
    candidate["dtype"] = selected
    with pytest.raises(ValueError, match="known selected candidate"):
        client.describe_json(json.dumps(candidate), expected_initialized_dtype=expected)
    assert calls == []


def test_missing_build_binding_and_changed_helper_are_refused_before_execution(transport):
    client, _, calls, helper = transport
    unbound = api.NativeDescriptionClient(helper, timeout=1)
    with pytest.raises(ValueError, match="from_build"):
        unbound.list_candidates_json()
    helper.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        client.list_candidates_json()
    assert calls == []


def test_receipt_binding_catalog_does_not_spawn_and_detects_helper_mismatch(tmp_path, monkeypatch):
    helper = tmp_path / native_worker_name("argyll-description-worker-never-run")
    helper.write_bytes(b"Not an executable")
    report = {"status": "passed", "argyll_version": "3.5.0",
              "archive_sha256": api.ARCHIVE_SHA256, "driver_scope": "all_public",
              "build_scope": "all_public", "build_system": platform.system(),
              "description_worker_build_link": {
                  "status": "passed", "executed": False,
                  "executable_sha256": hashlib.sha256(helper.read_bytes()).hexdigest()},
              "driver_manifest": {"archive_sha256": api.ARCHIVE_SHA256,
                                  "build_scope": "all_public", "instrument_types": []}}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    monkeypatch.setattr(api, "HelperSession", lambda *a, **k: pytest.fail("Unexpected execution"))
    client = api.NativeDescriptionClient.from_build(path)
    assert json.loads(client.catalog_json()) == report["driver_manifest"]
    helper.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="match"):
        api.NativeDescriptionClient.from_build(path)


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_offline_description_requires_this_hosts_dedicated_filename(tmp_path, monkeypatch, system):
    monkeypatch.setattr(platform, "system", lambda: system)
    basename = "description-stub-worker"
    helper = tmp_path / (basename + (".exe" if system == "Windows" else ""))
    helper.write_bytes(b"Inert substitute")
    client = api.NativeDescriptionClient.for_offline_tests(helper)
    assert client._helper == helper
    wrong = helper.with_name(basename + ("" if system == "Windows" else ".exe"))
    helper.rename(wrong)
    with pytest.raises(ValueError, match="Offline checks require"):
        api.NativeDescriptionClient.for_offline_tests(wrong)


def test_duplicate_json_keys_are_not_silently_overwritten(transport, candidate):
    """A complete candidate with one field repeated: only the duplicate-key hook
    can refuse it, so an incomplete-candidate refusal cannot stand in for it."""
    client, _, calls, _ = transport
    duplicated = json.dumps(candidate)[:-1] + f', "dtype": {candidate["dtype"]}}}'
    with pytest.raises(ValueError, match="Invalid metadata JSON") as caught:
        client.describe_json(duplicated)
    assert "Duplicate JSON field: dtype" in str(caught.value.__cause__)
    assert calls == []


@pytest.mark.parametrize("code", [0, 1])
def test_metadata_eof_checks_explicit_terminal_exit_code(code):
    from color_measurement.native_protocol import HelperSession

    with HelperSession([sys.executable, "-c", f"raise SystemExit({code})"]) as session:
        session.finish_messages(expected_returncode=code)


@pytest.mark.parametrize(("code", "expected"), [(1, 0), (0, 1)])
def test_metadata_eof_refuses_unexpected_terminal_exit_code(code, expected):
    """The exit-code check must refuse a mismatch in both directions, not only a non-zero exit."""
    from color_measurement.native_protocol import HelperSession

    with HelperSession([sys.executable, "-c", f"raise SystemExit({code})"]) as session:
        with pytest.raises(ProtocolError, match="exit code disagrees"):
            session.finish_messages(expected_returncode=expected)


def test_metadata_eof_refuses_late_output_after_terminal():
    from color_measurement.native_protocol import HelperSession

    with HelperSession([sys.executable, "-c", 'print("unexpected late output")']) as session:
        with pytest.raises(ProtocolError, match="Late output"):
            session.finish_messages()


def test_unavailable_configuration_cannot_smuggle_stale_return_values(
    transport, candidate, events,
):
    client, supplied, _, _ = transport
    events[1]["current_configuration"]["modes"] = 0xffffffff
    supplied.extend(events)
    with pytest.raises(ProtocolError, match="preserve unknowns"):
        client.describe_json(json.dumps(candidate))


def test_selected_serial_expectation_is_checked_in_result(transport, candidate, events):
    client, supplied, _, _ = transport
    events[1]["serial"] = "unexpected"
    supplied.extend(events)
    with pytest.raises(ProtocolError, match="identity"):
        client.describe_json(json.dumps(candidate), expected_serial="expected")


def test_instance_ownership_is_released_after_protocol_failure(transport, candidate, events):
    client, supplied, _, _ = transport
    invalid = copy.deepcopy(events)
    invalid[1]["initialized_dtype"] = 23
    supplied.extend(invalid)
    with pytest.raises(ProtocolError):
        client.describe_json(json.dumps(candidate))
    supplied[:] = events
    assert json.loads(client.describe_json(json.dumps(candidate)))["status"] == "completed"


def manifest_module():
    path = Path(__file__).parents[1] / "tools/native_argyll/driver_manifest.py"
    spec = importlib.util.spec_from_file_location("driver_manifest_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_constructor_parser_keeps_shared_models_and_excludes_other_conditionals():
    module = manifest_module()
    source = """inst *cm_new_inst_checked(int unused) {
      if (itype == instUnknown) { itype = identify(); }
      icom = new_icoms(path, log);
      if (expected_type != instUnknown && itype != expected_type) {
        icom->del(icom); return NULL;
      }
      if (itype == instExample || itype == instSibling)
        p = (inst *)new_example(icom, itype);
      p->vdel = virtual_del;
    }"""
    assert module.parse_dispatch(source) == {
        "instExample": "new_example", "instSibling": "new_example",
    }


def test_checked_constructor_patch_rejects_dispatch_before_guard(argyll_archive):
    module = manifest_module()
    with zipfile.ZipFile(argyll_archive) as archive:
        source = archive.read("Argyll_V3.5.0/spectro/inst.c").decode()
    marker = "#ifdef ENABLE_SERIAL\n\tif (itype == instDTP22)"
    early = "if (itype == instExample) p = (inst *)new_example(icom, itype);\n"
    with pytest.raises(ValueError, match="constructor.*before.*guard"):
        module.checked_constructor_patch(source.replace(marker, early + marker))


def test_compiled_dispatch_rejects_constructor_before_guard():
    module = manifest_module()
    source = """inst *cm_new_inst_checked(int unused) {
      icom = new_icoms(path, log);
      if (itype == instExample) p = (inst *)new_example(icom, itype);
      if (expected_type != instUnknown && itype != expected_type) {
        icom->del(icom); return ((void *)0);
      }
      p->vdel = virtual_del;
    }"""
    with pytest.raises(ValueError, match="constructor.*before.*guard"):
        module.parse_dispatch(source)


def test_checked_constructor_accepts_later_dispatch_and_ignores_literal_examples(argyll_archive):
    module = manifest_module()
    with zipfile.ZipFile(argyll_archive) as archive:
        source = archive.read("Argyll_V3.5.0/spectro/inst.c").decode()
    marker = "#ifdef ENABLE_SERIAL\n\tif (itype == instDTP22)"
    notes = '/* new_example(icom, itype); */\nlog("new_example(icom, itype)");\n'
    # Real new_icoms allocation and non-code constructor text may precede dispatch.
    source = source.replace(marker, notes + marker)
    source = source.replace("p = (inst *)new_kleink10(icom, itype);",
                            "p = (inst *)new_kleink10(icom, itype);\n"
                            "if (itype == instExample) p = (inst *)new_example(icom, itype);")
    changed = module.checked_constructor_patch(source)
    assert changed.index("itype != expected_type") < changed.index("if (itype == instExample)")


@pytest.mark.parametrize("null", ["NULL", "0", "((void *)0)"])
@pytest.mark.parametrize("unknown", ["instUnknown", "devUnknown"])
def test_compiled_constructor_guard_requires_refusal(null, unknown):
    module = manifest_module()
    body = """inst *cm_new_inst_checked(int unused) {
      if (expected_type != instUnknown && itype != expected_type) {
        icom->del(icom); return NULL;
      }
      if (itype == instExample) p = (inst *)new_example(icom, itype);
      p->vdel = virtual_del;
    }""".replace("return NULL", f"return {null}").replace("instUnknown", unknown)
    assert module.parse_dispatch(body) == {"instExample": "new_example"}
    with pytest.raises(ValueError, match="guard is missing or changed"):
        module.parse_dispatch(body.replace(f"return {null};", ""))


def test_passive_serial_patches_are_exact_and_preserve_selected_open(argyll_archive):
    import re
    import zipfile

    module = manifest_module()
    from native_argyll.no_termination import _code, _function

    with zipfile.ZipFile(argyll_archive) as archive:
        for name in ("icoms_ux.c", "icoms_nt.c"):
            path = "spectro/" + name
            original = archive.read("Argyll_V3.5.0/" + path).decode()
            changed = module.passive_serial_patches()[path](original)
            body, _ = _function(changed, "int serial_get_paths(", path)
            assert not re.search(r"\b(?:open|CreateFile[AW]?|fast_ser_dev_type)\s*\(", _code(body))
            # Only enumeration changes. Selected serial opening remains present.
            assert "icoms_set_ser_port" in changed
            with pytest.raises(ValueError):
                module.passive_serial_patches()[path](changed)
        original = archive.read("Argyll_V3.5.0/spectro/usbio_w0.c").decode()
        changed = module.passive_usb_patch(original)
        assert "LIBUSB_IOCTL_SET_DEBUG_LEVEL" not in changed
        assert "LIBUSB_IOCTL_GET_DESCRIPTOR" in changed
        with pytest.raises(ValueError):
            module.passive_usb_patch(changed)
    with pytest.raises(ValueError):
        module.passive_serial_patches()["spectro/icoms_ux.c"](
            'instType itype = fast_ser_dev_type(icom, 0, NULL, NULL);')


def test_manifest_reads_generic_identification_from_the_shared_serial_policy():
    module = manifest_module()
    policy = (Path(__file__).parents[1] / "tools/native_argyll/serial_policy.h").read_text()
    assert module.parse_serial_driver_types(policy) == {
        "instDTP22", "instDTP41", "instDTP51", "instDTP92", "instDTP94",
        "instSpectrolino", "instSpectroScan", "instSpectroScanT",
        "instSpecbos1201", "instSpecbos2501", "instSpecbos", "instSpectraval",
        "instKleinK10", "instSMCube",
    }
    with pytest.raises(ValueError, match="boundary not found"):
        module.parse_serial_driver_types(policy.replace("cm_serial_driver_type(", "removed("))


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_manifest_separates_linked_types_from_actual_measurement_modes(
    tmp_path, monkeypatch, system,
):
    """Linkage, shared route eligibility and physical validation stay distinct.

    Known firmware-loading initialization has no eligible acquisition route;
    ordinary disclosed effects can be accepted. Modes still require a live check.
    """
    module = manifest_module()
    monkeypatch.setattr(module.platform, "system", lambda: system)
    catalog = [
        {"enum": name, "native_type": index, "source_label": name}
        for index, name in enumerate(
            ("instI1Pro3", "instI1Pro", "instI1Pro2", "instI1Monitor",
             "instDescriptionOnly", "instRefused", "instSpyder2", "instSpectrocam"), start=1
        )
    ]
    monkeypatch.setattr(module, "parse_instrument_types",
                        lambda *_: copy.deepcopy(catalog))
    monkeypatch.setattr(module, "__file__", str(tmp_path / "driver_manifest.py"))
    # The manifest reads the COMPILED policy (cc -E), because a contributed enum
    # exists only under CM_CONTRIBUTED_<enum>; the fake compiler below returns
    # this text for description.c. serial_policy.h is hashed, not parsed raw.
    description = (
        'static int cm_serial_driver_type(instType dtype) {\n'
        'case instRefused: return 1;\n}\n'
        'static int cm_typed_serial(instType dtype) {\n'
        'case instDescriptionOnly: return 1;\n'
        '}\nstatic int cm_unidentified_serial(const icompath *path) { return 0; }\n'
        'static const char *init_refusal(int dtype) {\n'
        'case instRefused: return "initialization_reads_sensor";\n'
        'case instSpyder2: return "initialization_downloads_pld";\n'
        'case instI1Pro: case instI1Pro2: case instI1Monitor:\n'
        'return "initialization_resets_device";\n'
        '}\n'
        'static int init_harmless(instType dtype) {\n'
        'case instI1Pro3: case instDescriptionOnly: return 1;\n'
        '}\nstatic int init_assessed(instType dtype) { return 0; }\n'
    )
    (tmp_path / "description.c").write_text(description)
    policy = ("static int cm_typed_serial(instType dtype) {\n"
              "#ifdef CM_CONTRIBUTED_instDescriptionOnly\n"
              "case instDescriptionOnly: return 1;\n#endif\n}\n"
              "static int cm_unidentified_serial(const icompath *path) { return 0; }\n")
    (tmp_path / "serial_policy.h").write_text(policy)
    source, output = tmp_path / "source", tmp_path / "build"
    (source / "spectro").mkdir(parents=True)
    (source / "spectro/insttypes.h").write_text("Synthetic catalog supplied by test")
    output.mkdir()
    library = output / "synthetic-library.a"
    library.write_bytes(b"Not a native library; no executable is built or run")
    preprocessed = """inst *cm_new_inst_checked(int unused) {
      if (expected_type != instUnknown && itype != expected_type) {
        icom->del(icom); return NULL;
      }
      if (itype == instI1Pro3) p = (inst *)new_i1pro3(icom, itype);
      if (itype == instI1Pro || itype == instI1Pro2 || itype == instI1Monitor)
        p = (inst *)new_i1pro(icom, itype);
      if (itype == instDescriptionOnly || itype == instRefused || itype == instSpyder2)
        p = (inst *)new_description_only(icom, itype);
      p->vdel = virtual_del;
    }"""

    def command(arguments, *_):
        if "-E" in arguments:
            return description if arguments[-1].endswith("description.c") else preprocessed
        expected = ["-gU"] if system == "Darwin" else ["-g", "--defined-only"]
        assert arguments[0] == "nm" and arguments[1:-1] == expected
        return "T _new_i1pro3\nT _new_i1pro\nT _new_description_only\n"

    report = {"argyll_version": "3.5.0", "archive_sha256": api.ARCHIVE_SHA256,
              "platform": "synthetic", "build_scope": "all_public"}
    manifest = module.reconcile_linked_drivers(
        source, output, report, "compiler-not-executed", [], library, command,
    )
    assert manifest["build_scope"] == "all_public"
    # Seven linked types, six eligible for acquisition; no hardware validation.
    assert manifest["coverage_summary"] == {
        "linked_instrument_type_count": 7,
        "application_measurement_instrument_type_count": 6,
        "application_measurement_mode_route_count": 6,
        "hardware_tested": False,
    }
    rows = {row["enum"]: row for row in manifest["instrument_types"]}
    for name in ("instI1Pro3", "instI1Pro", "instI1Pro2", "instI1Monitor",
                 "instDescriptionOnly", "instRefused"):
        assert rows[name]["application_measurement_routes"] == [
            "generic_ladder:capability_derived"
        ], f"{name} has an eligible shared route"
    for name in ("instI1Pro", "instI1Pro2", "instI1Monitor"):
        assert rows[name]["description_route"] == "initialization_refused"
        assert rows[name]["initialization_refusal"] == "initialization_resets_device"
    # Not linked: no constructor, so nothing can drive it whatever the catalog says.
    assert rows["instSpyder2"]["application_measurement_routes"] == []
    assert rows["instSpectrocam"]["application_measurement_routes"] == []
    assert rows["instRefused"]["description_route"] == "initialization_refused"
    assert rows["instRefused"]["initialization_refusal"] == "initialization_reads_sensor"
    assert rows["instSpectrocam"]["description_route"] == "not_linked"
    # The typed route is published per row from the compiled policy, and only there.
    assert rows["instDescriptionOnly"]["typed_serial_selection"] is True
    assert not any(row["typed_serial_selection"] for name, row in rows.items()
                   if name != "instDescriptionOnly")

    assert rows["instSpectrocam"]["exclusion_reason"]
    assert all(row["physical_tested"] is False for row in rows.values())
    retained = json.loads((output / "driver-manifest.json").read_text())
    assert retained["coverage_summary"] == manifest["coverage_summary"]

    # The flag-loss direction. The series declares the type and serial_policy.h
    # guards it, but the compiled policy lacks it -- what a dropped
    # -DCM_CONTRIBUTED_<enum> produces. That must refuse, not publish a catalog
    # with no typed rows and nothing left for the typed checks to run.
    description = description.replace("case instDescriptionOnly: return 1;\n", "", 1)
    lost = dict(report, contribution={"instrument_types_added": ["instDescriptionOnly"]})
    with pytest.raises(ValueError, match="lost their typed serial route"):
        module.reconcile_linked_drivers(
            source, output, lost, "compiler-not-executed", [], library, command)


def test_a_typed_serial_guard_must_name_the_type_it_admits():
    """A guard for one enum admitting another would let the declaration check
    pass for the wrong type, so the raw policy parse refuses the mismatch."""
    module = manifest_module()
    good = ("static int cm_typed_serial(instType t) {\n#ifdef CM_CONTRIBUTED_instA\n"
            "case instA: return 1;\n#endif\n}\nstatic int cm_unidentified_serial(")
    assert module.guarded_typed_serial(good) == {"instA"}
    with pytest.raises(ValueError, match="guard names instA"):
        module.guarded_typed_serial(good.replace("case instA:", "case instB:"))


# --- provenance: the initialisation disclosures come from the pinned source -----
#
# Every other pinned table in this project is re-derived from the digest-verified
# archive by a test that fails when upstream moves -- `argyll_capability.py` has
# had that since it existed. The two disclosure tables in `description.c` had
# nothing of the kind, and both of the defects found in them survived because of
# it: the Huey's initialisation LED sequence went undisclosed, and the citation
# for it named the wrong line.

_INIT_SOURCES = {
    "instColorMunki": ("munki_imp.c", "munki_imp_init"),
    "instEX1": ("ex1.c", "ex1_init_inst"),
    "instHCFR": ("hcfr.c", "hcfr_init_inst"),
    "instI1Disp1": ("i1disp.c", "i1disp_init_inst"),
    "instI1Disp2": ("i1disp.c", "i1disp_init_inst"),
    "instI1Pro3": ("i1pro3_imp.c", "i1pro3_imp_init"),
}

#: Contributed drivers called harmless. Their source is the authenticated series
#: in this repository, not the pinned archive, and `description.c` names them only
#: under `CM_CONTRIBUTED_<enum>`, so a stock build compiles none of them.
_CONTRIBUTED_INIT_SOURCES = {
    "instCR250": ("cr250.c", "static inst_code init_inst"),
    # The CR-100 row of the same driver: new_cr250 installs the same init_inst.
    "instCR100": ("cr250.c", "static inst_code init_inst"),
    "instCR300": ("cr250.c", "static inst_code init_inst"),
}
_SERIES_SPECTRO = (Path(__file__).resolve().parents[1]
                   / "tools/native_argyll/contributions/cr_pr/files/spectro")

#: Calls that change the DEVICE rather than read it or set host state. Narrow on
#: purpose: a grep cannot prove "no side effects", but it can prove that the
#: specific effects this project discloses others for are absent here.
_DEVICE_EFFECTS = (
    "_reset(",            # resets_device
    "set_LEDs(",          # changes_indicator_leds
    "writeEEProm(", "WriteEEProm(",
    "AmbMeasure(",        # measures_ambient
    "_download",          # downloads_pld
    "msec_sleep(50)",     # the flash sequences all pace themselves with these
)



def _function_body(text: str, name: str) -> str:
    """One C function body, by brace matching from its definition."""
    start = text.index(name + "(")
    opening = text.index("{", start)
    depth, index = 0, opening
    while index < len(text):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening:index + 1]
        index += 1
    raise AssertionError(f"Unterminated body for {name}")


@pytest.fixture(scope="module")
def spectro(argyll_archive):
    import zipfile

    with zipfile.ZipFile(argyll_archive) as zipped:
        return {
            name.rsplit("/", 1)[-1]: zipped.read(name).decode("latin-1")
            for name in zipped.namelist()
            if name.startswith("Argyll_V3.5.0/spectro/") and name.endswith(".c")
        }


def _description_c() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1]
            / "tools/native_argyll/description.c").read_text()


def test_every_instrument_called_harmless_names_the_source_that_says_so():
    """`init_harmless` is an assertion about upstream, so it cannot grow without
    naming what was read. Adding an instrument to the C table and not here fails."""
    import re

    block = _description_c()
    harmless = block[block.index("static int init_harmless"):]
    harmless = harmless[:harmless.index("static int init_assessed")]
    assert set(re.findall(r"case (inst\w+):", harmless)) == (
        set(_INIT_SOURCES) | set(_CONTRIBUTED_INIT_SOURCES)), (
        "init_harmless and its cited sources disagree; assess the driver and cite it")


def test_the_contributed_harmless_driver_reads_identity_only():
    """CR-250, CR-100 and CR-300 share harmless, identity-only initialization.

    They are admitted to the typed serial route
    (serial_policy.h), whose rule is: no constructor I/O, identity verified by
    reads alone, and an init_inst that changes nothing on the device.

    The failure this must catch is the start-up path growing a command that is
    not an identity read -- a settings write, a measurement, an inventory read --
    while the table still calls it harmless and the route still opens the port
    without identification. Each claim is re-derived from the series source.
    """
    import re

    driver = (_SERIES_SPECTRO / "cr250.c").read_text()
    engine = (_SERIES_SPECTRO / "cr250_imp.c").read_text()
    sends = r"\b(?:cr250_imp_(?:command|measure|inventory)|read_value|unique_entry|config_read)\("

    init_coms = _function_body(driver, "static inst_code init_coms")
    assert set(re.findall(r"\bcr250_imp_\w+(?=\()", init_coms)) == {
        "cr250_imp_recover", "cr250_imp_identify"}, "init_coms now calls more of the engine"
    assert not re.search(sends, init_coms), "init_coms now sends a command directly"
    init_inst = _function_body(driver, "static inst_code init_inst")
    assert not re.search(r"\bcr250_imp_\w+\(|->(?:write|read)\(", init_inst)
    assert not re.search(sends, init_inst), "init_inst now talks to the device"

    recover = _function_body(engine, "int cr250_imp_recover")
    assert not re.search(sends + r"|->(?:write|read)\(", recover), "recovery now sends"
    identify = _function_body(engine, "int cr250_imp_identify")
    assert not re.search(r"\bcr250_imp_(?:command|measure|inventory)\(|->write\(", identify)
    assert re.findall(r'config_read\(p, "([^"]+)"', identify) == [
        "RC Model", "RC ID", "RC Firmware", "RC InstrumentType"], (
        "identification now sends something other than the four identity reads")
    # config_read is the read-configuration helper; "RC" is the protocol's read.
    assert all(command.startswith("RC ") for command in
               re.findall(r'config_read\(p, "([^"]+)"', engine))
    constructor = _function_body(driver, "cr250 *new_cr250")
    assert "p->init_coms = init_coms;" in constructor
    assert "p->init_inst = init_inst;" in constructor
    for symbol, model in (("instCR250", "CR-250"), ("instCR100", "CR-100"),
                          ("instCR300", "CR-300")):
        assert re.search(r'\{\s*' + symbol + r',\s*"' + model + r'"', driver), (
            f"{symbol} no longer shares the reviewed CR startup path")


@pytest.mark.parametrize("symbol", ["instPR655", "instPR670"])
def test_pr_remote_mode_initialization_is_disclosed_per_model(symbol):
    """The selected PR startup writes Q/PHOTO before checking its exact model.

    That remote-mode effect is assessed, but it is not a harmless identity read.
    Keep both the refusal and its published event under the exact contribution
    guard, so stock builds cannot name an unavailable enum.
    """
    import re

    source = _description_c()
    module = manifest_module()
    assert module.parse_init_refusals(source)[symbol] == "initialization_enters_remote_mode"
    assert symbol not in module.parse_init_harmless(source)
    guard = r"#ifdef CM_CONTRIBUTED_" + symbol + r"\s+(.*?)#endif"
    bodies = re.findall(guard, source, re.S)
    assert any('case ' + symbol + ': return "initialization_enters_remote_mode";' in body
               for body in bodies), "startup refusal lost its exact model guard"
    assert any("init_refusal_event(" + symbol + ", &comma);" in body for body in bodies), (
        "the started event no longer publishes the contributed PR startup effect")

    driver = (_SERIES_SPECTRO / "pr655.c").read_text()
    engine = (_SERIES_SPECTRO / "pr655_imp.c").read_text()
    contract = (_SERIES_SPECTRO / "pr655.txt").read_text()
    assert "pr655_imp_open(&p->engine, timeout)" in _function_body(
        driver, "static inst_code init_coms")
    opening = _function_body(engine, "int pr655_imp_open")
    assert re.findall(r'send_command\(p, "([^"]+)"', opening) == ["Q", "PHOTO"]
    assert 'response(p, "D111"' in opening
    assert 'p->expected_model == 655 ? "PR-655" : "PR-670"' in opening
    assert "Remote mode disables the touch screen." in contract
    assert "an uncertain exchange prevents Q and can leave the screen disabled" in contract


@pytest.mark.parametrize("dtype", [42, 43])
def test_pr_remote_mode_acceptance_uses_the_existing_named_effect(candidate, dtype):
    """Both contributed PR models use the existing explicit acceptance argument."""
    reason = "initialization_enters_remote_mode"
    candidate.update(dtype=0, transport=0x0811)
    selected, arguments = api.description_arguments(
        json.dumps(candidate), expected_initialized_dtype=dtype,
        accepted_initialization_reason=reason)
    assert selected == candidate
    assert arguments[arguments.index("--accepted-initialization-reason") + 1] == reason
    assert arguments[arguments.index("--expected-initialized-dtype") + 1] == str(dtype)
    assert "--allow-serial-identification" not in arguments


def test_no_instrument_called_harmless_touches_its_device_on_init(spectro):
    """The claim these entries make, checked against the pinned source.

    If a future pinned release adds a reset, an LED write or an EEPROM write to
    one of these `init_inst` bodies, this fails instead of the table continuing to
    call it harmless.
    """
    for instrument, (source, function) in sorted(_INIT_SOURCES.items()):
        body = _function_body(spectro[source], function)
        found = [effect for effect in _DEVICE_EFFECTS if effect in body]
        assert not found, f"{instrument}: {function} in {source} now calls {found}"


def test_the_disclosed_initialisations_still_do_what_they_are_disclosed_for(spectro):
    """The other direction: a disclosure that upstream no longer justifies is as
    wrong as a missing one, and would refuse an instrument for nothing."""
    huey = _function_body(spectro["huey.c"], "huey_init_inst")
    assert "huey_set_LEDs(p, 0x0)" in huey, "the Huey no longer writes its LEDs on init"
    # Not one write -- a sequence, with sleeps. `initialization_changes_indicator_leds`
    # understated this until the citation was re-read.
    assert huey.count("huey_set_LEDs(") >= 8 and "msec_sleep(50)" in huey, (
        "the Huey's initialisation LED sequence has changed; re-read the disclosure")

    # The pair that makes the i1Pro3's SILENCE meaningful rather than an omission.
    assert "i1pro_reset(p, 0x1f)" in _function_body(spectro["i1pro_imp.c"], "i1pro_imp_init"), (
        "the legacy i1Pro no longer resets on init; its disclosure is now wrong")
    i1pro3 = _function_body(spectro["i1pro3_imp.c"], "i1pro3_imp_init")
    assert not any(effect in i1pro3 for effect in _DEVICE_EFFECTS), (
        "the i1Pro3 now touches its device on init and must be disclosed")


def test_a_case_label_separated_by_a_comment_still_reaches_its_reason():
    """The defect itself, in miniature.

    `init_refusal`'s labels fall through to a shared return. A comment between two
    groups of them is not a break in the fall-through, but it is a break in `\\s*`,
    so matching raw C dropped the earlier labels -- and published those
    instruments as carrying NO refusal while the C refused them. Fail-open: a
    client would open them believing nothing was disclosed.
    """
    module = manifest_module()
    source = '''static const char *init_refusal(instType dtype) {
    switch (dtype) {
        case instAlpha: return "one_reason";
        case instBeta: case instGamma:
        /* A citation for the next pair, which is where this broke. */
        case instDelta:
            return "another_reason";
        default: return NULL;
    }
}
static int init_harmless(instType dtype) { return 0; }'''
    assert module.parse_init_refusals(source) == {
        "instAlpha": "one_reason", "instBeta": "another_reason",
        "instGamma": "another_reason", "instDelta": "another_reason",
    }


def test_a_case_label_reaching_no_reason_is_refused_rather_than_dropped():
    module = manifest_module()
    source = '''static const char *init_refusal(instType dtype) {
    switch (dtype) {
        case instOrphan:
            break;
        case instAlpha: return "one_reason";
        default: return NULL;
    }
}
static int init_harmless(instType dtype) { return 0; }'''
    with pytest.raises(ValueError, match="instOrphan"):
        module.parse_init_refusals(source)


def test_the_published_refusal_table_is_the_one_the_c_actually_applies():
    """The manifest is what a client reads; `init_refusal` is what runs.

    They were allowed to disagree, and did: instColorHug and instColorHug2 were
    published with no refusal for as long as it took to notice, because the
    reconciliation checked only for an enum the parser INVENTED, never one it
    LOST. This compares the published catalog to the C directly.
    """
    import json

    module = manifest_module()
    from_c = module.parse_init_refusals(_description_c())
    catalog = json.loads(
        (Path(__file__).parents[1] / "tests/fixtures/discovery/catalog.json")
        .read_text(encoding="utf-8"))
    rows = {row["enum"]: row for row in catalog["instrument_types"]}
    published = {enum: row["initialization_refusal"] for enum, row in rows.items()
                 if row["initialization_refusal"] is not None}
    assert published == from_c, (
        "the published catalog and description.c disagree; regenerate the manifest: "
        f"only in C {sorted(set(from_c) - set(published))}, "
        f"only published {sorted(set(published) - set(from_c))}")
    assert {enum for enum, row in rows.items()
            if row["description_route"] == "initialization_refused"} == set(from_c)


def test_current_huey_driver_publishes_emission_quantities(prepared_production):
    source = (prepared_production / "spectro/huey.c").read_text()
    from native_argyll.spyder_calibration_slots import _function
    body = _function(source, "huey_read_sample")
    assert body.count("val->mtype = inst_mrt_emission;") == 1
    assert "val->mtype = inst_mrt_none;" not in body


# These guards derive the exceptional policy from the reviewed source control
# flow. This is deliberately not a general C analyser or a production generator:
# an unrecognised shape/new candidate fails for source review rather than guessing.
def _code(text):
    import re
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"', '', text, flags=re.S)


def _compiled(code):
    """The lines a build compiles, as far as the file itself decides: the #ifdef
    arm of a macro the file #undefs and never #defines is dropped. Every other
    conditional arm is kept, so this can only over-include."""
    import re
    undefined = (set(re.findall(r'^[ \t]*#[ \t]*undef[ \t]+(\w+)', code, re.M))
                 - set(re.findall(r'^[ \t]*#[ \t]*define[ \t]+(\w+)', code, re.M)))
    kept, outer, live = [], [], True
    for line in code.splitlines():
        directive = re.match(r'[ \t]*#[ \t]*(\w+)[ \t]*(\w*)', line)
        word = directive[1] if directive else None
        if word in ('if', 'ifdef', 'ifndef'):
            outer.append(live)
            live = live and not (word == 'ifdef' and directive[2] in undefined)
        elif word in ('else', 'elif') and outer:
            live = outer[-1]
        elif word == 'endif' and outer:
            live = outer.pop()
        elif live:
            kept.append(line)
    return '\n'.join(kept)


def _balanced(text, opening):
    left = text[opening]
    right = {'{': '}', '(': ')'}[left]
    depth = 0
    for end in range(opening, len(text)):
        depth += (text[end] == left) - (text[end] == right)
        if depth == 0:
            return text[opening:end+1], end+1
    raise AssertionError('Unterminated source block or signature')


def _defined_body(code, name):
    import re
    definitions = []
    for match in re.finditer(r'\b' + re.escape(name) + r'\s*\(', code):
        _, end = _balanced(code, match.end()-1)
        brace = re.match(r'\s*\{', code[end:])
        if brace:
            definitions.append(_balanced(code, end + brace.end()-1)[0])
    assert len(definitions) == 1, f'Review changed definition: {name}'
    return definitions[0]


def test_ambient_matrix_policy_matches_pinned_driver_control_flow(spectro, tmp_path):
    """Candidates come from real capability bodies, effects from acquisition bodies.

    Reject unfamiliar source shape. No instrument implementation is executed;
    only the small project policy is compiled with inert types for comparison.
    """
    import re
    import shutil
    import subprocess
    from pathlib import Path

    code = {name: _code(text) for name, text in spectro.items()}
    candidates = set()
    for filename, text in code.items():
        for name in set(re.findall(r'\b(\w+_capabilities)\s*\(', text)):
            body = _defined_body(text, name)
            # K10 composes emission with the raw ambient action bit. Include
            # either spelling; this is a candidate census, not check_mode().
            ambient = re.search(r'\binst_mode_(?:emis_)?ambient\b', body)
            if ambient and 'inst2_ccmx' in body:
                candidates.add(filename)
    sources = {
        'i1disp.c': ('i1disp_take_XYZ_measurement', 'instI1Disp2'),
        'huey.c': ('huey_take_XYZ_measurement', 'instHuey'),
        'spyd2.c': ('spyd2_read_sample', 'instSpyder5'),
        'i1d3.c': ('i1d3_take_XYZ_measurement', 'instI1Disp3'),
        'spydX.c': ('spydX_read_sample', 'instSpyderX'),
        'spydX2.c': ('spydX2_read_sample', 'instSpyderX2'),
        'kleink10.c': ('k10_read_sample', 'instKleinK10'),
    }
    assert candidates == set(sources), 'Review changed ambient/CCMX capability census'
    correction = r'icmMulBy3x3\s*\([^,]+,\s*p->ccmat\s*,'
    derived = {}
    # Which drivers type EVERY reading by the matrix they applied, whatever the
    # mode (cm_matrix_sets_type): the route must then agree with the matrix.
    typed = (r'if\(\(p->ccmat\[0\]\[0\]\+p->ccmat\[1\]\[1\]\+p->ccmat\[2\]\[2\]\)'
             r'/3\.0>5\.0\)val->mtype=')
    matrix_typed = {
        symbol for filename, (function, symbol) in sources.items()
        if re.search(typed, re.sub(r'\s+', '', _defined_body(code[filename], function)))}
    for filename, (function, symbol) in sources.items():
        body = _defined_body(code[filename], function)
        calls = list(re.finditer(correction, body))
        assert len(calls) == 1, f'Review changed CCMX application: {filename}'
        # i1disp has a negative guard, the other bypasses use the positive else.
        guard = re.search(r'if\s*\(\s*(!?)IMODETST\(p->mode,\s*inst_mode_emis_ambient\)'
                          r'\s*\)\s*\{', body)
        if filename == 'i1disp.c':
            guards = list(re.finditer(r'if\s*\(\s*!IMODETST\(p->mode,\s*'
                                      r'inst_mode_emis_ambient\)\s*\)\s*\{', body))
            assert len(guards) == 1
            branch, _ = _balanced(body, guards[0].end()-1)
            assert re.search(correction, branch)
            derived[symbol] = False
        elif guard:
            assert not guard[1]
            ambient, end = _balanced(body, guard.end()-1)
            alternative = re.match(r'\s*else\s*\{', body[end:])
            assert alternative, f'Review changed ambient branch: {filename}'
            emission, _ = _balanced(body, end + alternative.end()-1)
            assert re.search(correction, emission), f'Review moved CCMX: {filename}'
            derived[symbol] = bool(re.search(correction, ambient))
        else:
            # K10 applies the matrix at function scope, not inside a mode arm.
            before = body[:calls[0].start()]
            assert before.count('{') - before.count('}') == 1
            # Brace depth alone would miss a newly added single-statement if.
            assert re.search(r'amutex_unlock\(p->lock\);\s*$', before)
            assert ('if((p->ccmat[0][0]+p->ccmat[1][1]+p->ccmat[2][2])/3.0>5.0)'
                    'val->mtype=inst_mrt_ambient;elseval->mtype=inst_mrt_emission;'
                    in re.sub(r'\s+', '', body))
            derived[symbol] = True

    cc = shutil.which('cc') or shutil.which('gcc')
    if not cc:
        pytest.skip('C compiler unavailable for inert policy comparison')
    # Exercise the actual project predicate for every constructor-dispatch name,
    # including sibling models and the unknown sentinel, not only one per driver.
    symbols = sorted(set(re.findall(r'\bitype\s*==\s*(inst\w+)',
                                   _defined_body(code['inst.c'], 'new_inst'))))
    assert set(derived) <= set(symbols)
    unit = tmp_path / 'policy.c'
    header = Path(__file__).resolve().parents[1] / 'tools/native_argyll/driver_policy.h'
    unit.write_text('#include <stdio.h>\ntypedef struct {int dtype;} inst;\n'
        + 'enum {' + ','.join(symbols) + '};\n#include "' + str(header) + '"\n'
        + 'int main(void) {\n' + '\n'.join(
            f'inst m{i} = {{{symbol}}}; printf("%d %d", cm_ambient_ccmx(&m{i}),'
            f' cm_matrix_sets_type(&m{i})); cm_print_matrix_threshold(stdout, &m{i});'
            ' printf("\\n");'
            for i, symbol in enumerate(symbols))
        # The threshold, either side of the source's "> 5.0".
        + '\ndouble at[3][3] = {{5,0,0},{0,5,0},{0,0,5}};'
        + '\ndouble above[3][3] = {{5,0,0},{0,5,0},{0,0,5.01}};'
        + '\nprintf("%d %d\\n", cm_matrix_labels_ambient(at), cm_matrix_labels_ambient(above));'
        + '\nreturn 0;}\n')
    executable = tmp_path / 'policy-check'
    subprocess.run([cc, str(unit), '-o', str(executable)], check=True, capture_output=True)
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    *rows, threshold = result.stdout.splitlines()
    words = [row.split(',', 1) for row in rows]
    assert [tuple(int(value) for value in flags.split()) for flags, _ in words] == [
        (derived.get(symbol, False), symbol in matrix_typed) for symbol in symbols]
    assert matrix_typed == {'instKleinK10'}
    assert threshold == '0 1'
    # What the binding publishes is the compiled comparison's own constant: the
    # source's 5.0 for the matrix-typed driver, null for every other.
    published = {symbol: json.loads('{' + fragment + '}')['ambient_matrix_threshold']
                 for symbol, (_, fragment) in zip(symbols, words, strict=True)}
    assert published == {symbol: 5.0 if symbol in matrix_typed else None
                         for symbol in symbols}


def test_calibration_default_is_the_upstream_backfilled_function(spectro):
    import re
    code = _code(spectro['inst.c'])
    constructor = _defined_body(code, 'new_inst')
    assert re.search(r'if\s*\(p->get_n_a_cals\s*==\s*NULL\)\s*'
                     r'p->get_n_a_cals\s*=\s*inst_get_n_a_cals;', constructor)
    default = _defined_body(code, 'inst_get_n_a_cals')
    assert re.sub(r'\s+', '', default) == (
        '{if(n_cals!=NULL)*n_cals=inst_calt_none;'
        'if(a_cals!=NULL)*a_cals=inst_calt_none;returninst_ok;}')


def test_initial_calibration_reuse_semantics_from_pinned_source(spectro, tmp_path):
    """Check the documented upstream option, not an implemented reuse feature.

    Compile only the extracted host-state setters/option arms with inert types.
    No driver, transport, calibration, clock or cache implementation is linked.
    Both dissimilar families and the upstream unsupported default run together.
    """
    import re
    import shutil
    import subprocess

    code = {name: _code(text) for name, text in spectro.items()}
    option = r'if\s*\(\s*(m|ot)\s*==\s*inst_opt_noinitcalib\s*\)\s*\{'
    handlers = {name for name, text in code.items() if re.search(option, text)}
    # This is a census of driver implementations, not a count of model types.
    bounded = {'ex1.c', 'i1pro.c', 'i1pro3.c', 'munki.c', 'smcube.c',
               'spydX.c', 'spydX2.c'}
    assert handlers == bounded | {'ss.c'}, 'Review changed initial-calibration option census'
    constructor = _defined_body(code['inst.c'], 'new_inst')
    dispatch = {}
    for condition, factory in re.findall(
        r'if\s*\(([^{};]*?)\)\s*p\s*=\s*\(inst\s*\*\)\s*'
        r'(new_\w+)\(icom,\s*itype\);', constructor, re.S,
    ):
        for symbol in re.findall(r'itype\s*==\s*(inst\w+)', condition):
            dispatch[symbol] = factory
    catalog = json.loads((Path(__file__).parent / 'fixtures/discovery/catalog.json').read_text())
    from color_measurement.argyll_capability import CONTRIBUTED_INSTRUMENTS

    # The published catalog comes from a composed build: the pinned 38 plus exactly
    # the declared contribution types. This census is about the PINNED inst.c, so
    # it runs over the stock 38 and requires nothing else to have crept in.
    listed = {row['enum'] for row in catalog['instrument_types']}
    public = listed - set(CONTRIBUTED_INSTRUMENTS)
    assert listed - public == set(CONTRIBUTED_INSTRUMENTS)
    dispatch = {symbol: factory for symbol, factory in dispatch.items() if symbol in public}
    assert len(public) == 38 and public - dispatch.keys() == {'instSpectrocam'}

    implemented = set()
    for symbol, factory in dispatch.items():
        filename = factory.removeprefix('new_') + '.c'
        body = _defined_body(code[filename], factory)
        assigned = re.findall(r'p->get_set_opt\s*=\s*(\w+)\s*;', body)
        assert len(assigned) == 1, f'Review option dispatch: {symbol}'
        getter = _defined_body(code[filename], assigned[0])
        supports = re.search(option, getter) is not None
        assert supports == (filename in handlers)
        if supports:
            implemented.add(symbol)
        else:
            assert 'inst_opt_noinitcalib' not in getter
            assert re.sub(r'\s+', '', getter).endswith(
                '{inst_coderv;va_listargs;va_start(args,m);'
                'rv=inst_get_set_opt_def(pp,m,args);va_end(args);returnrv;}}')
    assert len(implemented) == 13 and len(dispatch.keys() - implemented) == 24
    # A reading asks exactly the types whose handler only waives the at-open want
    # AND whose driver reads the flag in code it compiles (driver_policy.h).
    # ss.c's flag masks the need itself; spydX2.c reads it only under
    # ENABLE_BLACK_CAL, which it leaves undefined (:68, :1001-1004).
    heeded = {name for name in bounded if any(
        re.search(r'!\s*\w+->noinitcalib\b', _compiled(code[unit]))
        for unit in (name, name.removesuffix('.c') + '_imp.c') if unit in code)}
    assert heeded == bounded - {'spydX2.c'}, 'Review which drivers read the waiver'
    policy = (Path(__file__).parents[1] / 'tools/native_argyll/driver_policy.h').read_text()
    waiver = policy[policy.index('static int cm_waives_initial_calibration'):]
    waiver = waiver[:waiver.index('\n}\n')]
    assert set(re.findall(r'case\s+(inst\w+)\s*:', waiver)) == {
        symbol for symbol, factory in dispatch.items()
        if factory.removeprefix('new_') + '.c' in heeded}
    assert {symbol for symbol, factory in dispatch.items() if factory == 'new_ss'} == {
        'instSpectrolino', 'instSpectroScan', 'instSpectroScanT'}
    assert 'instI1Disp3' in dispatch.keys() - implemented  # Third, unsupported family.
    default = _defined_body(code['inst.c'], 'inst_get_set_opt_def')
    assert re.sub(r'\s+', '', default) == '{returninst_unsupported;}'

    unit = ['#include <stdarg.h>\n#include <stdio.h>\n'
            '#define a1logd(...) ((void)0)\n'
            'enum {inst_ok=0, inst_unsupported=1};\n'
            'typedef struct {int lo_secs, noinitcalib;} state;']
    cases, expected = [], []
    for filename in sorted(handlers):
        stem = filename.removesuffix('.c')
        body = _defined_body(code[filename], stem + '_get_set_opt')
        matches = list(re.finditer(option, body))
        assert len(matches) == 1
        arm, _ = _balanced(body, matches[0].end()-1)
        # For setters split into *_imp.c, use the implementation's actual body.
        impfile = stem + '_imp.c'
        indirect = filename in bounded and impfile in code
        if indirect:
            unit.append(f'typedef state {stem}imp;')
        unit.append(f'typedef struct {{state *m; int lo_secs, noinitcalib;}} {stem};')
        if filename in bounded:
            setter = stem + '_set_noinitcalib'
            assert re.search(r'\b' + setter + r'\(p,\s*1,\s*losecs\)', arm)
            setter_body = _defined_body(code[impfile if indirect else filename], setter)
            unit.append(f'static void {setter}({stem} *p, int v, int losecs) {setter_body}')
        else:
            # Unknown behavior must be reviewed, not classified by option success.
            assert re.search(r'if\s*\(losecs\s*==\s*0\)\s*p->noinitcalib\s*=\s*1;', arm)
        unit.append(f'static int option_{stem}({stem} *p, int {matches[0][1]}, ...) {arm}')
        for initial in (0, 1):
            for limit in (0, 60):
                for age in (0, 59, 60, 61):
                    target = 's' if indirect else 'p'
                    cases.append(f'{{state s={{{age},{initial}}}; '
                                 f'{stem} p={{&s,{age},{initial}}}; '
                                 f'int rv=option_{stem}(&p,0,{limit}); '
                                 f'printf("%d %d\\n",rv,{target}.noinitcalib);}}')
                    # A refused/ignored request preserves previous state, even true.
                    changes = limit == 0 or (filename in bounded and age < limit)
                    expected.append((0, 1 if changes else initial))
    unit.append(f'static int unsupported(void) {default}')
    unit.append('int main(void) {\n' + '\n'.join(cases)
                + '\nprintf("%d -1\\n",unsupported());\nreturn 0;}')
    expected.append((1, -1))
    cc = shutil.which('cc') or shutil.which('gcc')
    if not cc:
        pytest.skip('C compiler unavailable for inert calibration-option check')
    source, executable = tmp_path / 'reuse.c', tmp_path / 'reuse-check'
    source.write_text('\n'.join(unit))
    subprocess.run([cc, '-std=c99', str(source), '-o', str(executable)],
                   check=True, capture_output=True)
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    assert [tuple(map(int, row.split())) for row in result.stdout.splitlines()] == expected


def test_the_dtp92_stored_rate_getter_succeeds_without_returning_the_rate(spectro):
    """``dtp92_get_refr_rate`` validates a parsed rate and returns ``inst_ok``
    having never copied it out, so the caller keeps the 0.0 it was pre-set to.

    Upstream zeroes ``*ref_rate``, parses the reply into the LOCAL ``refrate``,
    refuses a zero parse, then returns success -- and no path assigns ``refrate``
    to ``*ref_rate``. So ``inst_ok`` from this getter does not mean a rate was
    written, and ``inst.h``'s "on returning a valid reading" is not honoured here.

    This is a pinned-source change alarm: the only out-parameter write is zero.
    An upstream repair changes that fact and requires reassessing the exception.
    It does not exercise application callers and cannot prevent a future timing
    implementation from trusting this status. Before exposing stored-rate reads,
    add behavioral coverage of the actual shared caller's refusal or repair.
    """
    import re

    body = _defined_body(_code(spectro["dtp92.c"]), "dtp92_get_refr_rate")
    flat = re.sub(r"\s+", " ", body)
    assert re.findall(r"\*ref_rate\s*=\s*([^;]+);", body) == ["0.0"]
    assert re.search(r"sscanf\s*\([^;]*&refrate", flat), "Parse target moved"
    assert "if (refrate == 0.0) return inst_misread;" in flat, "Zero guard moved"
    assert flat.rstrip().endswith("return inst_ok; }")


def test_spyder2_invalidating_the_stored_rate_leaves_it_valid_and_positive(spectro):
    """``spyd2_set_refr_rate(0.0)`` is upstream's documented way to mark the
    stored rate invalid. It instead stores ``DEFRRATE`` and clears nothing.

    ``inst.h``'s ``set_refr_rate`` comment says "Set refresh rate to 0.0 to mark
    it as invalid". The body sets ``p->refrate = DEFRRATE`` on that request and
    assigns ``refrvalid`` only on the non-zero branch, so a previously valid set
    stays valid and the getter then reports success with 50 Hz.

    This is why "finite and positive" cannot be the provenance rule on its own:
    the value surviving an explicit invalidation is finite, positive and
    plausible. This test detects upstream changes to that control flow; it does
    not test whether an application promotes a stored value to a measurement.
    Before exposing the setter/getter, exercise set(valid), invalidate, then get
    through the actual shared caller and verify the retained state's origin.
    """
    import re

    source = _code(spectro["spyd2.c"])
    flat = re.sub(r"\s+", " ", _defined_body(source, "spyd2_set_refr_rate"))
    assert "if (ref_rate == 0.0) p->refrate = DEFRRATE; else p->refrvalid = 1;" in flat
    assert re.findall(r"refrvalid\s*=\s*([^;]+);", flat) == ["1"], "Invalidation path changed"
    assert re.search(r"#define\s+DEFRRATE\s+50\b", source), "DEFRRATE is no longer 50"


def test_the_cr250_persona_substitutes_only_what_inst_c_defaults_to(spectro):
    """cr250_persona.c calls new_cr250 directly, so new_inst's backfill of NULL
    methods (inst.c) never runs, and the persona supplies the UI default the
    worker reaches. Each must be upstream's own body, or the persona would drive
    the real driver through a vtable no real session has.

    The failure this catches: upstream changing either default while the
    persona keeps the old behaviour and the connected tests keep passing.
    """
    import re

    flat = lambda body: re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", "", body, flags=re.S)).strip()  # noqa: E731,E501
    inst_c = spectro["inst.c"]
    assert flat(_function_body(inst_c, "static void set_uicallback")) == (
        "{ pp->uicallback = uicallback; pp->uic_cntx = cntx; }")
    assert flat(_function_body(inst_c, "inst_code inst_get_set_opt_def")) == (
        "{ return inst_unsupported; }")
    persona = (Path(__file__).resolve().parents[1]
               / "tools/native_argyll/cr250_persona.c").read_text()
    assert flat(_function_body(persona, "static void set_uicallback")) == (
        "{ pp->uicallback = uicallback; pp->uic_cntx = cntx; }")
    # The actual inst.c now supplies option fallback and display selectors.
    assert "inst_code inst_get_set_opt_def" not in persona


def test_current_initialized_identity_packet_survives_the_real_description_parser(
    transport, candidate, events,
):
    from test_discovery import current_cr_identity

    client, supplied, calls, _ = transport
    events[1]["serial"] = "CR-0001"
    events[1]["native_identity"] = current_cr_identity()
    supplied.extend(events)
    answer = json.loads(client.describe_json(json.dumps(candidate)))
    assert answer["data"]["native_identity"] == current_cr_identity()
    assert answer["backend"]["helper_sha256"] == client._helper_sha256
    assert answer["data"]["initialized_dtype"] == 19
    assert answer["data"]["native_identity"]["identity"]["instrument_type"] == 2
    assert calls[0]["closed"]
    # Model-to-catalogue validation belongs to InstrumentBuild, not an enum
    # ordinal hard-coded into this generic transport parser.


@pytest.mark.parametrize("field,value", [
    ("verified", False), ("serial", "WRONG"), ("instrument_type", 19),
    ("instrument_type", True), ("firmware_components", [1, True]),
    ("model", ""), ("firmware", 126),
    ("firmware_components", []), ("firmware_components", [1, 2, 3, 4]),
    ("driver", "unrecognized-adapter"), ("packet_extra", True), ("identity_extra", True),
])
def test_current_initialized_identity_packet_refuses_malformed_or_uncorrelated_facts(
    transport, candidate, events, field, value,
):
    from test_discovery import current_cr_identity

    client, supplied, calls, _ = transport
    events[1]["serial"] = "CR-0001"
    identity = current_cr_identity()
    if field in {"driver", "packet_extra"}:
        identity[field] = value
    else:
        identity["identity"][field] = value
    events[1]["native_identity"] = identity
    supplied.extend(events)
    with pytest.raises(ProtocolError):
        client.describe_json(json.dumps(candidate))
    assert calls[0]["closed"] and len(client.last_events) == 1


def test_description_requires_explicit_native_identity_availability(transport, candidate, events):
    client, supplied, calls, _ = transport
    del events[1]["native_identity"]
    supplied.extend(events)
    with pytest.raises(ProtocolError, match="native identity availability"):
        client.describe_json(json.dumps(candidate))
    assert calls[0]["closed"]
