"""Exercise descriptions over substituted transports and an extracted dispatch guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from color_measurement.argyll_description import NativeDescriptionClient
from color_measurement.native_receipt import native_worker_name


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def exercise_description(helper: Path, output: Path, catalog=()) -> dict:
    """``catalog`` is this build's instrument rows; its typed serial types need cases."""
    cases = []

    def run(scenario, *, expected_error=None, selection=None, case=None, **kwargs):
        client = NativeDescriptionClient.for_offline_tests(helper, scenario)
        listing = json.loads(client.list_candidates_json())
        if listing["status"] == "failed":
            result = listing
        else:
            candidate = selection or listing["data"]["candidates"][0]
            result = json.loads(client.describe_json(json.dumps(candidate), **kwargs))
        require(result["status"] == ("failed" if expected_error else "completed"), scenario)
        require(result["provenance"] == "simulated", "Lost substituted provenance")
        if expected_error:
            require(result["error"]["stage"] == expected_error, scenario + " wrong failure")
        else:
            require(result["data"]["application_measurement_routes"] == [], "Invented acquisition")
        case = case or scenario
        (output / f"description-{case}.json").write_text(json.dumps(result, indent=2) + "\n")
        cases.append({"case": case, "status": "passed", "native_status": result["status"]})
        return result

    display = run("display_types")["data"]["display_types"]
    require(display["status"]=="available" and len(display["entries"])==3,
            "Description lost the native display catalogue")
    require(len({row["id"] for row in display["entries"]})==3,
            "Duplicate display labels collapsed identities")
    require(display["readback"]["refresh_mode"] is None
            and display["readback"]["base_calibration_id"]==0,
            "Partial readback fabricated a field or lost native zero")
    require(display["entries"][2]["identity_scope"]=="loaded_correction_data",
            "Installed correction provenance lost")
    failed = run("display_types_failure")["data"]["display_types"]
    require(failed["status"]=="failed" and failed["entries"]==[],
            "Failed list retrieval became an available catalogue")
    success = run("success")
    require(success["data"]["capabilities"] == {
        "primary": 0x10018, "secondary": 0, "tertiary": 0,
    }, "Raw capability flags changed")
    checks = success["data"]["baseline_mode_checks"]
    require(checks == {
        "scope": "initialized_before_mode_configuration", "mode_capabilities": 0x10018,
        "checks": [{"mode_word": 0x10018, "native_code": 0}],
    }, "Baseline mode check lost exact word or scope")
    for scenario, code in (("mode_rejected", 0x50000), ("mode_inconclusive", 0x30000),
                           ("mode_missing", None)):
        check = run(scenario)["data"]["baseline_mode_checks"]["checks"]
        require(check == [{"mode_word": 0x10018, "native_code": code}],
                "Mode-check refusal or missing method became acceptance")
    from color_measurement.argyll_capability import mode_word

    i1pro3 = run("i1pro3", expected_serial="CM-CHART-0001")["data"]
    require(i1pro3["candidate"]["dtype"] == i1pro3["initialized_dtype"] == 23
            and i1pro3["candidate"]["transport"] == 2
            and i1pro3["serial"] == "CM-CHART-0001"
            and i1pro3["native_identity"] is None,
            "Substituted i1Pro3 description lost its own USB/type/serial identity")
    i1pro3_checks = {r["mode_word"]: r["native_code"]
                     for r in i1pro3["baseline_mode_checks"]["checks"]}
    for mode in ("reflective_spot", "emissive_spot", "ambient_spot"):
        require(i1pro3_checks.get(mode_word(mode, spectral=True)) == 0,
                f"Substituted i1Pro3 description lost {mode} acceptance")

    rows = run("mode_composite")["data"]["baseline_mode_checks"]["checks"]
    by_word = {row["mode_word"]: row["native_code"] for row in rows}
    for spectral in (False, True):
        require(by_word[mode_word("emissive_spot", spectral=spectral)] == 0x50000,
                "Composite bits invented accepted spot geometry")
        require(by_word[mode_word("emissive_tele", spectral=spectral)] == 0,
                "Actual tele geometry lost its native acceptance")
    for scenario in ("unknown_serial", "empty_serial"):
        require(run(scenario)["data"]["serial"] is None, "Missing serial became a string")
    for scenario in ("configuration_unavailable", "configuration_missing"):
        config = run(scenario)["data"]["current_configuration"]
        require(config == {"status": "unavailable", "native_code": 0x50000,
                           "index": None, "modes": None, "calibration_conditions": None},
                "Unavailable configuration fabricated data")
    config = run("calibration_configuration")["data"]["current_configuration"]
    require(config["modes"] == 0x80000000 and config["calibration_conditions"] == 16,
            "Calibration configuration must be retained without performing calibration")
    require(run("quoted_serial")["data"]["serial"] == 'Q"\\\n\té', "JSON escaping changed")
    require(run("utf8_serial", accepted_initialization_reason=
                "initialization_changes_indicator_leds")["data"]["serial"] == "CM-ñ-1",
            "UTF-8 serial changed in description JSON")
    refinement = run("identity_refinement", expected_initialized_dtype=21,
                     expected_error="initialization_refused")
    require(refinement["events"][-1]["active_access_attempted"] is False,
            "Blocked i1Pro2 refinement initialized its device")
    failures = {
        "initialization_failure": "init_inst_failed",
        "communications_failure": "init_coms_failed",
        "construction_failure": "construction_failed",
        "enumeration_failure": "enumeration_failed",
        "identity_mismatch": "constructed_identity_mismatch",
        "identity_cross_family": "initialized_identity_mismatch",
        "identity_comms_cross_family": "communications_identity_mismatch",
        "callback_refusal": "callback_refused", "callback_ignored": "callback_refused",
        "duplicate_path": "ambiguous_path", "invalid_path": "invalid_candidate_metadata",
        "invalid_utf8_path": "invalid_candidate_metadata",
        "invalid_utf8_serial": "invalid_serial_metadata", "long_serial": "invalid_serial_metadata",
        "candidate_payload_limit": "candidate_payload_limit",
    }
    for scenario, stage in failures.items():
        run(scenario, expected_error=stage)
    for scenario in ("spyder1", "spyder3", "spyder4", "spyder5", "spyderx", "dtp20", "dtp22",
                     "dtp41", "dtp51", "dtp92", "dtp94", "spectrolino", "spectroscan", "smcube",
                     "i1monitor", "i1pro", "i1pro2",
                     # Three instruments that flash their own indicators during
                     # init -- upstream's comment is "just cos we can". They read
                     # as harmless until a provenance test re-derived the claim.
                     "i1d3_flash", "colorhug_flash", "huey_flash"):
        result = run(scenario, expected_error="initialization_refused")
        require(result["events"][-1]["active_access_attempted"] is False,
                "Refusal occurred after active initialization")
        if scenario in ("spyder3", "spyder4", "spyder5"):
            require(result["error"]["refusal_reason"] == "initialization_reads_sensor",
                    "Spyder sensor-reading refusal was lost")
        if scenario in ("i1monitor", "i1pro", "i1pro2"):
            require(result["error"]["refusal_reason"] == "initialization_resets_device",
                    "Legacy i1Pro reset refusal was lost")
        if scenario in ("i1d3_flash", "colorhug_flash", "huey_flash"):
            require(result["error"]["refusal_reason"] == "initialization_changes_indicator_leds",
                    f"{scenario}: indicator-LED refusal was lost")
    for scenario, dtype in (("kleink10", 12), ("specbos1201", 8), ("specbos2501", 9),
                            ("specbos", 10), ("spectraval", 11)):
        result = run(scenario, expected_initialized_dtype=dtype,
                     allow_serial_identification=True, expected_error="initialization_refused")
        require(result["events"][-1]["active_access_attempted"] is False,
                "Serial startup refusal occurred after probing")
    run("serial_mismatch", expected_serial="EXPECTED", expected_error="serial_identity_mismatch")
    typed = {row["enum"]: row["native_type"] for row in catalog
             if row.get("typed_serial_selection")}
    # Each contributed type has an exact production-driver description persona.
    # The generic typed cases below still prove the shared selection refusals.
    native_cases = {
        "instCR250": ("cr250_native", "cr250", "CR-250", "SYNTHETIC-250", 2),
        "instCR100": ("cr100_native", "cr250", "CR-100", "SYNTHETIC-100", 1),
        "instCR300": ("cr300_native", "cr250", "CR-300", "SYNTHETIC-300", 2),
        "instPR655": ("pr655_native", "pr655", "PR-655", "SYNTHETIC-P7", None),
        "instPR670": ("pr670_native", "pr655", "PR-670", "SYNTHETIC-P7", None),
    }
    require(set(typed) <= native_cases.keys(),
            f"Typed serial types without cases: {sorted(typed)}")
    # The CR-100 is another row of the same driver and the same serial_policy.h
    # case: its typed selection reaches initialization with its own type. The
    # refusal paths below are the shared route's, exercised once on the CR-250.
    if "instCR100" in typed:
        result = run("cr250_typed", expected_initialized_dtype=typed["instCR100"])
        require(result["data"]["candidate"]["dtype"] == 0
                and result["data"]["initialized_dtype"] == typed["instCR100"],
                "Typed CR-100 selection lost the listed path or the verified type")
    if "instCR250" in typed:
        cr250 = typed["instCR250"]
        dtp92 = next(row["native_type"] for row in catalog if row["enum"] == "instDTP92")
        result = run("cr250_typed", expected_initialized_dtype=cr250)
        require(result["data"]["candidate"]["dtype"] == 0
                and result["data"]["initialized_dtype"] == cr250,
                "Typed selection lost the listed path or the verified type")
        for scenario, stage in (("cr250_typed_wrong_model", "init_coms_failed"),
                                ("cr250_typed_old_firmware", "init_inst_failed")):
            run(scenario, expected_initialized_dtype=cr250, expected_error=stage)
        mismatch = run("cr250_typed_mismatch", expected_initialized_dtype=cr250,
                       expected_error="constructed_identity_mismatch")
        require(mismatch["events"][-1]["initialization_performed"] is False,
                "A typed constructor of another type reached initialization")
        for kwargs, stage, case in (
            ({"allow_serial_identification": True}, "serial_identification_not_applicable",
             "cr250_typed_identification"),
            # A non-typed serial family for the same port still needs the generic
            # permission. Every serial family has a disclosed initialization, so
            # accept that disclosure -- otherwise the refusal comes from
            # init_blocker first and this never reaches the serial branch.
            ({"expected_initialized_dtype": dtp92,
              "accepted_initialization_reason": "initialization_selects_factory_calibration"},
             "serial_identification_requires_explicit_request", "cr250_typed_other_type"),
        ):
            refused = run("cr250_typed", case=case, expected_error=stage,
                          **{"expected_initialized_dtype": cr250, **kwargs})
            require(refused["events"][-1]["active_access_attempted"] is False,
                    f"{case}: the port was opened before the refusal")
    catalog_by_enum = {row["enum"]: row for row in catalog}
    for enum, (scenario, driver, model, serial, instrument_type) in native_cases.items():
        if enum not in typed:
            continue
        dtype = typed[enum]
        row = catalog_by_enum[enum]
        require(row.get("initialization_assessed") is True,
                f"{model}: production-driver startup was not assessed")
        require(row.get("native_facts") is True and row.get("native_controls") is True,
                f"{model}: compiled identity/control availability was lost")
        reason = "initialization_enters_remote_mode" if driver == "pr655" else None
        require(row.get("initialization_refusal") == reason,
                f"{model}: compiled startup effect differs from the reviewed driver")
        options = dict(expected_initialized_dtype=dtype, expected_serial=serial)
        if reason is not None:
            refused = run(scenario, case=scenario + "_unacknowledged",
                          expected_error="initialization_refused", **options)
            terminal = refused["events"][-1]
            require(refused["error"]["refusal_reason"] == reason
                    and terminal["active_access_attempted"] is False
                    and terminal["initialization_performed"] is False
                    and terminal["cleanup"] == "not_constructed",
                    f"{model}: remote-mode refusal occurred after construction or access")
            # Accepting an unassessed driver cannot waive a disclosed PR effect.
            run(scenario, case=scenario + "_unassessed_acceptance",
                expected_error="initialization_refused", accept_unassessed_initialization=True,
                **options)
            options["accepted_initialization_reason"] = reason
        result = run(scenario, **options)
        data = result["data"]
        require(data["candidate"]["dtype"] == 0 and data["initialized_dtype"] == dtype
                and data["serial"] == serial,
                f"{model}: typed native description lost path, exact model type or serial")
        terminal = result["events"][-1]
        require(terminal["initialization_completed"] is True
                and terminal["cleanup"] == "destructor_returned",
                f"{model}: initialized description did not release its owned driver")
        disclosures = {item["dtype"]: item["reason"]
                       for item in result["events"][0]["initialization_refusals"]}
        require(disclosures.get(dtype) == reason,
                f"{model}: started event lost its compiled startup disclosure")
        if driver == "cr250":
            expected_identity = dict(verified=True, model=model, serial=serial,
                firmware="1.34.0-S28", firmware_components=[1, 34, 0],
                instrument_type=instrument_type)
        else:
            count, step, pixels, last = ((101, 4, 128, 118) if model == "PR-655"
                                        else (201, 2, 256, 242))
            expected_identity = dict(verified=True, model=model, serial=serial,
                firmware="9.01-test", grid=dict(count=count, start_nm=380, end_nm=780,
                    step_nm=step, pixels=pixels, first_pixel=9, last_pixel=last, bandwidth_nm=4.75))
            by_word = {item["mode_word"]: item["native_code"]
                       for item in data["baseline_mode_checks"]["checks"]}
            # The declared startup persona reports Adaptive exposure. Its actual
            # initialized check_mode must accept both geometries and outputs,
            # while refusing nonadaptive intent before any exposure is requested.
            for geometry in ("emissive_spot", "emissive_tele"):
                for spectral in (False, True):
                    require(by_word.get(mode_word(geometry + "_adaptive", spectral=spectral)) == 0,
                            f"{model}: default Adaptive {geometry} was not accepted")
                    code = by_word.get(mode_word(geometry + "_nonadaptive", spectral=spectral))
                    require(type(code) is int and code != 0,
                            f"{model}: default Adaptive {geometry} admitted nonadaptive intent")
        require(data["native_identity"] == dict(
            driver=driver, native_controls=True, identity=expected_identity),
            f"{model}: initialized identity or exact reported grid differs from the actor")
    client = NativeDescriptionClient.for_offline_tests(helper, "no_candidates")
    require(json.loads(client.list_candidates_json())["data"]["candidates"] == [],
            "Empty catalog fabricated devices")
    cases.append({"case": "no_candidates", "status": "passed"})
    client = NativeDescriptionClient.for_offline_tests(helper)
    candidates = json.loads(client.list_candidates_json())["data"]["candidates"]
    require(len(candidates) == 2 and candidates[0]["path"] != candidates[1]["path"]
            and candidates[0]["dtype"] == candidates[1]["dtype"], "Same-model instances collapsed")
    candidate = dict(candidates[0], transport=2)
    result = json.loads(client.describe_json(json.dumps(candidate)))
    require(result["error"]["stage"] == "candidate_identity_mismatch", "Stale selection accepted")
    cases.append({"case": "stale_transport", "status": "passed"})
    return {"status": "passed", "scope": (
        "same core with substituted OS transports and contributed production drivers" if typed
        else "same core with wholly substituted instrument methods"),
        "upstream_runtime_linked": bool(typed), "hardware_tested": False, "cases": cases}


def prove_selection_guard(source, output, report, cc, flags, command) -> dict:
    """Execute the actual checked dispatch prefix with all communication hooks replaced."""
    text = (source / "spectro/inst.c").read_text()
    start = text.index("inst *cm_new_inst_checked(")
    end = text.index("#ifdef ENABLE_SERIAL\n\tif (itype == instDTP22)", start)
    prefix = text[start:end]
    if "expected_type != instUnknown && itype != expected_type" not in prefix:
        raise ValueError("Checked dispatch guard missing")
    prologue = r'''
#include "description.h"
#undef a1logd
#define a1logd(...) ((void)0)
#define dev_category(x) icomt_instrument
static int closed, constructed, identified, case_count, unexpected_family_constructors;
static instType detected;
static void close_stub(icoms *p) { closed++; free(p); }
icoms *new_icoms(icompath *path, a1log *log) {
    (void)log;
    icoms *p=calloc(1,sizeof(*p));
    if (!p) abort();
    p->dtype=path->dtype; p->dctype=path->dctype; p->del=close_stub;
    return p;
}
devType fast_ser_dev_type(icoms *p, int hard,
    inst_code (*callback)(void *, inst_ui_purp), void *context) {
    (void)p; (void)hard; (void)callback; (void)context; identified++; return detected;
}
static instType ser_inst_type(icoms *p,
    inst_code (*callback)(void *, inst_ui_purp), void *context) {
    return fast_ser_dev_type(p,1,callback,context);
}
'''
    ending = r'''
    constructed++; icom->del(icom); return (inst *)(uintptr_t)1;
}
static void check(instType known, instType expected, instType found, int fast, int nocoms,
                  int expected_constructed, int expected_identified) {
    icompath path; memset(&path,0,sizeof(path)); path.name="substituted";
    path.dtype=known; path.dctype=icomt_serial | (fast ? icomt_fastserial : 0);
    closed=constructed=identified=0; detected=found;
    cm_new_inst_checked(&path,nocoms,NULL,NULL,NULL,expected);
    if (!expected_constructed) unexpected_family_constructors += constructed;
    if (closed!=1 || constructed!=expected_constructed || identified!=expected_identified) abort();
    case_count++;
}
int main(void) {
    check(instI1Disp3,instI1Disp3,instUnknown,0,1,1,0);
    check(instI1Disp3,instI1Pro3,instUnknown,0,1,0,0);
    check(instUnknown,instSpecbos,instSpectroScan,1,0,0,1);
    check(instUnknown,instSpecbos,instKleinK10,0,0,0,1);
    check(instUnknown,instSpecbos,instSpecbos,1,0,1,1);
    check(instUnknown,instSpecbos,instUnknown,0,1,0,0);
#ifdef CM_CONTRIBUTED_instCR250
    /* serial_policy.h's typed copy: known type, nocoms=1, no identification. */
    check(instCR250,instCR250,instSpecbos,1,1,1,0);
#endif
#ifdef CM_CONTRIBUTED_instCR100
    check(instCR100,instCR100,instSpecbos,1,1,1,0);
#endif
    printf("{\"cases\":%d,\"unexpected_family_constructors\":%d,\"cleanup_verified\":true}\n",
           case_count, unexpected_family_constructors);
    return 0;
}
'''
    unit = output / "selection-guard-substituted.c"
    unit.write_text(prologue + prefix + ending)
    executable = output / native_worker_name("selection-guard-substituted")
    command([cc, *flags, f"-I{Path(__file__).parent}", str(unit), "-o", str(executable)],
            output, report)
    result = json.loads(command([str(executable)], output, report))
    configured_cr = sum(f"-DCM_CONTRIBUTED_{name}" in flags
                        for name in ("instCR250", "instCR100"))
    require(result.get("cases") == 6 + configured_cr,
            "Selection guard did not exercise every configured constructor case")
    require(result.get("unexpected_family_constructors") == 0,
            "Selection guard reached an unexpected family constructor")
    require(result.get("cleanup_verified") is True,
            "Selection guard did not verify path cleanup")
    return {"status": "passed", "scope": "extracted dispatch prefix; all OS hooks substituted",
            "source_prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest(),
            "generated_source_sha256": hashlib.sha256(unit.read_bytes()).hexdigest(),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), **result}


def prove_session_selection(output, report, cc, flags, command, *, link_flags) -> dict:
    """Compile the actual attach unit with wholly inert enumeration/construction."""
    unit = output / "session-selection-substituted.c"
    unit.write_text(r'''
#include "session.h"
#include <errno.h>
#include <limits.h>
FILE *wire;
static const char *selection_case = "setup", *selection_value = "";
static int selection_case_index = -1;
/* Keep abort visible to the C compiler at every non-returning refusal. */
#define selection_failure(line) do { \
    fprintf(stderr, "FAIL selection case=%s index=%d value=%s line=%d\n", \
            selection_case, selection_case_index, selection_value, (line)); \
    fflush(stderr); \
    abort(); \
} while (0)
static int selection_long32, selection_long32_saturations;
static long selection_strtol(const char *text, char **end, int base) {
    long value = strtol(text, end, base);
    /* Model positive 32-bit long overflow on an LP64 host. */
    if (selection_long32 && (value > INT_MAX || (errno == ERANGE && value == LONG_MAX))) {
        selection_long32_saturations++;
        errno = ERANGE;
        return INT_MAX;
    }
    return value;
}
#define strtol selection_strtol
/* SELECTION_PARSER */
#undef strtol
static int constructed, closed, handed_nocoms;
static icompath path;
static const icompath *handed;
static const char *handed_name;
static instType handed_type;
static instType handed_expected;
static unsigned handed_dctype;
static inst meter, *answer = &meter;
static icompath *get_path(icompaths *p, int index) { (void)p; return index == 1 ? &path : NULL; }
static void close_paths(icompaths *p) { closed++; free(p); }
icompaths *new_icompaths_sel(a1log *log, icom_type mask) {
    icompaths *p = calloc(1, sizeof(*p)); (void)log;
    if (mask != (icomt_instrument | icomt_portattr_all)) selection_failure(__LINE__);
    p->get_path = get_path; p->del = close_paths; return p;
}
/* serial_policy.h's typed copy asks insttypes.c for the category. The real
 * dev_category runs in the description stub worker; this harness substitutes
 * enumeration, so it answers as the composed insttypes.c does for the CR-250
 * (icomt_instrument, like every measurement instrument) and refuses any other. */
icom_type dev_category(instType itype) {
#ifdef CM_CONTRIBUTED_instCR250
    if (itype == instCR250) return icomt_instrument;
#endif
#ifdef CM_CONTRIBUTED_instCR100
    if (itype == instCR100) return icomt_instrument;
#endif
    selection_failure(__LINE__);
}
instType inst_enum(char *name) {
#ifdef CM_CONTRIBUTED_instCR250
    if (!strcmp(name, "Colorimetry Research CR-250")) return instCR250;
#endif
    if (!strcmp(name, "instKleinK10")) return instKleinK10;
    (void)name; return instI1Pro3;
}
inst *cm_new_inst_checked(icompath *p, int n, a1log *l,
    inst_code (*cb)(void *, inst_ui_purp), void *ctx, instType expected) {
    (void)l; (void)cb; (void)ctx;
    if (expected != p->dtype && !(n == 0 && p->dtype == instUnknown
                                  && expected == instKleinK10
                                  && (p->dctype & icomt_serial))) selection_failure(__LINE__);
    handed = p; handed_name = p->name; handed_type = p->dtype;
    handed_expected = expected;
    handed_dctype = (unsigned)p->dctype; handed_nocoms = n;
    constructed++; return answer;
}
static int refused(const char *stage) {
    return !cm_attach(NULL,NULL) && cm_attach_stage() && !strcmp(cm_attach_stage(), stage);
}
int main(void) {
    char *argv[] = {"probe", "--usb-index", "1", "--expected-serial", "",
        "--instrument", "instI1Pro3", "--allow-serial-identification", "0",
        "--expected-path", "selected-path"};
    int parser_cases = 0;
    {
        cm_selection selected;
        char *values[] = {"1", "2147483647", "0", "-1", "+1", "1x",
                          "2147483648", "999999999999999999999999999999999"};
        int expected[] = {1, INT_MAX, 0, 0, 0, 0, 0, 0};
        int i, parsed;
        if (INT_MAX != 2147483647L) selection_failure(__LINE__);
        for (i = 0; i < (int)(sizeof(values) / sizeof(values[0])); i++) {
            argv[2] = values[i];
            selection_case = "selection-index-native"; selection_case_index = i;
            selection_value = argv[2];
            errno = ERANGE;
            parsed = cm_parse_selection(11, argv, &selected);
            if (expected[i] ? (parsed != 10 || selected.index != expected[i]) : parsed != -1)
                selection_failure(__LINE__);
            parser_cases++;
        }
        selection_long32 = 1;
        for (i = 0; i < 3; i++) {
            argv[2] = values[i == 2 ? 6 : i];
            selection_case = "selection-index-long32"; selection_case_index = i;
            selection_value = argv[2];
            errno = 0;
            parsed = cm_parse_selection(11, argv, &selected);
            if (i == 2 ? parsed != -1 : (parsed != 10 || selected.index != expected[i]))
                selection_failure(__LINE__);
            parser_cases++;
        }
        selection_case = "selection-parser-accounting"; selection_case_index = -1;
        selection_value = "";
        selection_long32 = 0;
        if (selection_long32_saturations != 1 || constructed || closed) selection_failure(__LINE__);
        argv[2] = "1";
    }
    selection_case = "selected-usb-path";
    wire=stdout; path.dtype=instI1Pro3; path.dctype=icomt_usb;
    if (cm_configure(11,argv) != 10) selection_failure(__LINE__);
    path.name="other-unit-same-model";
    if (cm_attach(NULL,NULL) || constructed || closed!=1 ||
        strcmp(cm_attach_stage(),"selected_path_changed")) selection_failure(__LINE__);
    path.name="selected-path";
    if (!cm_attach(NULL,NULL) || constructed!=1 || closed!=2 || handed!=&path || handed_nocoms!=1)
        selection_failure(__LINE__);
    int cases = 2;
    {
        /* A generic serial path has no type until explicitly identified. The
         * absent path and missing permission must not reach a constructor. */
        int before = constructed, released = closed;
        selection_case = "serial-path-selection";
        argv[2]="2";
        if (cm_configure(11,argv) != 10 || !refused("no_path_at_index")
            || constructed!=before || closed!=++released) selection_failure(__LINE__);
        argv[2]="1";
        path.dtype=instUnknown; path.dctype=icomt_serial | icomt_fastserial;
        if (cm_configure(11,argv) != 10 || !refused("serial_identification_required")
            || constructed!=before || closed!=++released) selection_failure(__LINE__);
        argv[8]="1";
        selection_case = "usb-only-refuses-serial-identification";
        if (cm_configure(11,argv) != 10 || !refused("expected_type_has_no_serial_driver")
            || constructed!=before || closed!=++released) selection_failure(__LINE__);
        /* Klein K-10 is a real serial-identification family in the shared policy. */
        argv[6]="instKleinK10"; answer=NULL;
        selection_case = "serial-identification-failure";
        if (cm_configure(11,argv) != 10 || !refused("serial_identification_failed")
            || constructed!=before+1 || closed!=++released || handed!=&path
            || handed_nocoms!=0 || handed_type!=instUnknown
            || handed_expected!=instKleinK10 || handed_name!=path.name
            || !(handed_dctype & icomt_serial)) selection_failure(__LINE__);
        answer=&meter;
        selection_case = "serial-identification-success";
        if (cm_configure(11,argv) != 10 || !cm_attach(NULL,NULL)
            || constructed!=before+2 || closed!=++released || handed!=&path
            || handed_nocoms!=0 || handed_type!=instUnknown
            || handed_expected!=instKleinK10 || handed_name!=path.name
            || !(handed_dctype & icomt_serial)) selection_failure(__LINE__);
        argv[8]="0"; cases += 5;
    }
#ifdef CM_CONTRIBUTED_instCR250
    {
        /* serial_policy.h: the caller's type goes on a copy of the pinned path,
         * with nocoms=1; the enumerated entry is never rewritten. */
        selection_case = "typed-serial-path";
        char *typed[] = {"probe", "--usb-index", "1", "--expected-serial", "",
            "--instrument", "Colorimetry Research CR-250", "--allow-serial-identification", "0",
            "--expected-path", "/dev/cu.usbmodem1"};
        path.dtype=instUnknown; path.dctype=icomt_serial | icomt_fastserial | icomt_seriallike;
        path.name="/dev/cu.usbmodem1"; constructed=0;
        if (cm_configure(11,typed) != 10 || !cm_attach(NULL,NULL) || constructed!=1
            || handed==&path || handed_nocoms!=1 || handed_type!=instCR250
            || handed_name!=path.name || !(handed_dctype & icomt_serial)
            || (handed_dctype & icomt_cat_mask) != icomt_instrument
            || path.dtype!=instUnknown) selection_failure(__LINE__);
        typed[8]="1";
        if (cm_configure(11,typed) != 10
            || !refused("serial_identification_not_applicable")) selection_failure(__LINE__);
        typed[8]="0";
        if (cm_configure(9,typed) != 8 || !refused("typed_serial_requires_path")) '''
    r'''selection_failure(__LINE__);
        if (cm_configure(11,typed) != 10) selection_failure(__LINE__);
        path.name="/dev/cu.usbmodem2";
        if (!refused("selected_path_changed")) selection_failure(__LINE__);
        path.name="/dev/cu.usbmodem1"; path.dtype=instI1Pro3; path.dctype=icomt_usb;
        if (!refused("different_instrument_at_index")) selection_failure(__LINE__);
        path.dtype=instUnknown; path.dctype=icomt_serial | icomt_fastserial; answer=NULL;
        if (!refused("typed_serial_construction_failed") || constructed!=2) '''
    r'''selection_failure(__LINE__);
        answer=&meter; cases += 6;
    }
#endif
    printf("{\"cases\":%d,\"selection_parser_cases\":%d,"
           "\"long32_saturations\":%d,\"wrong_path_constructors\":0,"
           "\"cleanup_verified\":true}\n", cases, parser_cases, selection_long32_saturations);
    return 0;
}
''')
    from .no_termination import _function
    native = Path(__file__).parent
    source = (native / "session.c").read_text()
    parser = "\n".join(_function(source, signature, "session.c")[0] for signature in
                       ("static int i32(", "int cm_parse_selection("))
    unit.write_text(unit.read_text().replace("/* SELECTION_PARSER */", parser))
    executable = output / native_worker_name("session-selection-substituted")
    command([cc, *flags, f"-I{native}", str(native / "session_attach.c"), str(unit),
             *link_flags, "-o", str(executable)], output, report)
    result = json.loads(command([str(executable)], output, report))
    require(result.get("selection_parser_cases") == 11, "Selection parser coverage changed")
    require(result.get("long32_saturations") == 1, "32-bit long saturation control did not run")
    return {"status": "passed",
            "scope": ("actual attach unit and selection parser; OS enumeration and constructors "
                      "substituted; 32-bit long overflow simulated"),
            "generated_source_sha256": hashlib.sha256(unit.read_bytes()).hexdigest(),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), **result}
