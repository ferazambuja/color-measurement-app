"""Broad build safety and original-i1Pro behavior; no instrument runtime executes."""

import copy
import importlib.util
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from conftest import assert_validator_survives_optimization

TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location(
    "legacy_native_build", TOOLS / "argyll_native_check.py",
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.fixture(scope="module")
def production_source(argyll_archive, tmp_path_factory):
    source, report = native.prepare(argyll_archive,
                                    tmp_path_factory.mktemp("i1pro-production") / "build")
    assert report["driver_scope"] == "all_public"
    return source


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_broad_recipe_links_declared_sources_without_executing_real_workers(
    tmp_path, monkeypatch, system,
):
    monkeypatch.syspath_prepend(str(TOOLS))
    from native_argyll import driver_manifest

    calls = []
    driver_sources = tuple(f"spectro/{family}{suffix}.c"
                           for family in ("i1pro", "i1pro3", "munki")
                           for suffix in ("", "_imp"))
    monkeypatch.setattr(driver_manifest, "instrument_sources", lambda _: driver_sources)
    monkeypatch.setattr(native.platform, "system", lambda: system)
    monkeypatch.setattr(native.shutil, "which", lambda name: f"/tool/{name}")
    monkeypatch.setattr(native, "upstream_dependencies", lambda *args: [])
    monkeypatch.setattr(native, "retain_upstream_notices", lambda *args: {})
    monkeypatch.setattr(native, "require_passive_serial", lambda *args: None)
    # This command-creation unit uses toy compiler replies. Actual preprocessor,
    # constructor-symbol and catalogue reconciliation has separate source tests.
    reconciliations = []
    manifest = {"coverage_summary": {"toy_manifest": True}}

    def reconcile(*args):
        reconciliations.append(args)
        return manifest

    monkeypatch.setattr(driver_manifest, "reconcile_linked_drivers", reconcile)

    def record(args, output, report):
        calls.append(args)
        if "-E" in args:
            assert args[0] == "/tool/cc"
            return "#define OTHER_OPTION 1\n" if "-dM" in args else _preprocessed()
        if args[0] == "/tool/objdump":
            return ("file format pei-x86-64\nCharacteristics 0x22\n\texecutable\n"
                    "Magic 020b (PE32+)\nDLL Name: KERNEL32.dll\n")
        assert args[0] in {"/tool/cc", "/tool/ar"}, "A compiled executable was run"
        destination = args[2] if args[0] == "/tool/ar" else args[args.index("-o") + 1]
        Path(destination).write_bytes(b"inert build artifact")
        return ""

    monkeypatch.setattr(native, "command", record)
    source = tmp_path / "upstream"
    implementation = source / "spectro/i1pro_imp.c"
    implementation.parent.mkdir(parents=True)
    implementation.write_text(_preprocessed())
    report = {"driver_scope": "all_public", "patches": []}
    flags = native.build_library(source, tmp_path, report, "/tool/cc")
    units = report["library_build_link"]["translation_units"]
    expected_units = {*native.SOURCES, *driver_sources}
    if system == "Windows":
        expected_units.discard("spectro/pollem.c")
    assert set(units) == expected_units
    assert set(driver_sources) <= set(units)
    assert ("spectro/pollem.c" in units) == (system != "Windows")
    assert "-DSALONEINSTLIB" in flags and "-DENABLE_USB" in flags
    assert not any("ENABLE_WRITE" in flag for flag in flags)
    assert {"-DENABLE_SERIAL", "-DENABLE_FAST_SERIAL"} <= set(flags)
    assert report["library_build_link"]["executed"] is False
    assert report["usb_match_scope"] == "all_supported"
    assert report["generic_worker_build_link"]["executed"] is False
    assert report["description_worker_build_link"]["executed"] is False
    assert report["driver_manifest"] is manifest
    assert report["coverage_summary"] is manifest["coverage_summary"]
    assert len(reconciliations) == 1
    assert reconciliations[0] == (source, tmp_path, report, "/tool/cc", flags,
                                  tmp_path / "libcm_argyll_offline.a", record)
    if system == "Windows":
        assert report["generic_worker_build_link"]["runtime_dll_imports"] == ["KERNEL32.dll"]
        assert report["description_worker_build_link"]["runtime_dll_imports"] == ["KERNEL32.dll"]
    for args in calls:
        if args[0] == "/tool/cc":
            assert ("-DWINVER=0x0600" in args) == (system == "Windows")
            assert ("-D_WIN32_WINNT=0x0600" in args) == (system == "Windows")
    assert report["eeprom_write_policy"]["status"] == "passed"
    assert report["eeprom_write_policy"]["enable_write"] is False
    assert len([args for args in calls if "-E" in args]) == 2
    worker = "generic-worker-never-run" + (".exe" if system == "Windows" else "")
    assert any(args[-1] == str(tmp_path / worker) for args in calls)
    compiled_local = {Path(arg).name for args in calls for arg in args
                      if arg.startswith(str(native.NATIVE)) and arg.endswith(".c")}
    assert compiled_local == {"link_probe.c", "session.c", "session_attach.c",
                              "description.c", "description_attach.c"}


def test_production_entrypoints_compile_in_actual_upstream_header_context(
    argyll_archive, tmp_path,
):
    """Compile the real workers' include order, without linking or running them."""
    system = native.platform.system()
    cc = shutil.which(native.compiler_name())
    if cc is None or system not in {"Darwin", "Linux", "Windows"}:
        pytest.skip("Host native compiler unavailable")
    output = tmp_path / "production-context"
    source, report = native.prepare(argyll_archive, output)
    flags = native.library_compile_flags(source, output, report, system)
    for name in ("session.c", "description.c"):
        unit = str(native.NATIVE / name)
        if system == "Windows":
            # A Windows CI build of the real worker failed here while the isolated platform probe
            # passed: upstream chose XP before the SDK declared the strict flag.
            previous = [arg for arg in flags if not arg.startswith(
                ("-DWINVER=", "-D_WIN32_WINNT="))]
            earlier = subprocess.run([cc, *previous, "-fsyntax-only", unit],
                                     cwd=output, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=30)
            (output / f"{name}-previous.log").write_text(
                earlier.stdout + earlier.stderr, encoding="utf-8")
            assert earlier.returncode != 0
            assert "WC_ERR_INVALID_CHARS" in earlier.stderr
        native.command([cc, *flags, "-fsyntax-only", unit], output, report)


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_legacy_recognizer_recipe_retains_support_sources_and_usb_declarations(
    argyll_archive, production_source, tmp_path, monkeypatch, system,
):
    """Exercise archive extraction and command creation, stopping before compilation.

    Windows path spelling is simulated independently of the host filesystem;
    this proves neither a Windows compiler run nor any instrument operation.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    monkeypatch.syspath_prepend(str(TOOLS))
    from native_argyll import legacy_scan_recognition as proof

    path_type = PureWindowsPath if system == "Windows" else PurePosixPath
    monkeypatch.setattr(proof, "Path", lambda value: (
        Path(value) if Path(value).is_absolute() else path_type(value)
    ))
    monkeypatch.setattr(proof.platform, "system", lambda: system)
    monkeypatch.setattr(proof.platform, "platform", lambda: f"simulated-{system}")
    monkeypatch.setattr(proof.shutil, "which", lambda _: "/tool/cc")
    output = tmp_path / "recognizer"

    class StopBeforeCompile(Exception):
        pass

    def inspect_compile(argv, **kwargs):
        with zipfile.ZipFile(argyll_archive) as archive:
            for name in ("numlib/numsup.c", "rspl/rspl1.c", "License2.txt"):
                assert (output / "upstream" / name).read_bytes() == archive.read(
                    proof.PREFIX + name
                ), name
        assert argv.count("-DENABLE_USB") == 1
        if system == "Windows":
            assert {"-DNT", "-DEN_LIBUSB0", "-lwinmm"} <= set(argv)
            assert not {"-DUNIX", "-pthread"}.intersection(argv)
        else:
            assert {"-DUNIX", "-pthread"} <= set(argv)
            assert not {"-DNT", "-DEN_LIBUSB0", "-lwinmm"}.intersection(argv)
        raise StopBeforeCompile

    monkeypatch.setattr(proof.subprocess, "run", inspect_compile)
    with pytest.raises(StopBeforeCompile):
        proof.prove(argyll_archive, output, source=production_source)


def _preprocessed(writer="return 0;", updater="return 0;"):
    return ("i1pro_code i1pro_writeEEProm(void *p, char *buf, int addr, int count) {\n"
            + writer + "\n}\ni1pro_code i1pro_update_log(void *p) {\n" + updater + "\n}\n")


@pytest.mark.parametrize("preprocessed,macros", [
    (_preprocessed(), "#define ENABLE_WRITE 1\n"),
    (_preprocessed("p->icom->usb_control(p);"), ""),
    (_preprocessed("p->icom->usb_write(p);"), ""),
    (_preprocessed(updater="i1pro_writeEEProm(p,buf,0,1);"), ""),
    ("int i1pro_writeEEProm(void);\n", ""),
])
def test_eeprom_policy_rejects_active_or_unestablished_write_branches(preprocessed, macros):
    with pytest.raises(ValueError):
        native.legacy_eeprom_bodies(preprocessed, macros)


def test_eeprom_policy_accepts_only_present_disabled_function_definitions():
    # Lifecycle call sites and forward declarations are not additional definitions.
    prefix = ("i1pro_code i1pro_update_log(void *p);\nvoid lifecycle(void *p) {\n"
              "if (i1pro_update_log(p) != 0) { return; }\n}\n")
    bodies = native.legacy_eeprom_bodies(prefix + _preprocessed(), "#define OTHER_OPTION 1\n")
    assert set(bodies) == {"i1pro_writeEEProm", "i1pro_update_log"}


def test_every_library_linking_i1pro_refuses_enabled_eeprom_before_compilation(
    tmp_path, monkeypatch,
):
    """The broad acquisition library must enforce the same no-write policy.

    The old policy ran only after the selected-family build, leaving the broad
    library without an explicit compiler check. Compiler replies are substituted
    here; the separate source-bound preprocessing proof covers actual source.
    """
    monkeypatch.syspath_prepend(str(TOOLS))
    from native_argyll import driver_manifest

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(driver_manifest, "instrument_sources", lambda _: (
        "spectro/i1pro.c", "spectro/i1pro_imp.c",
    ))
    monkeypatch.setattr(native, "require_passive_serial", lambda *args: None)
    source = tmp_path / "source"
    implementation = source / "spectro/i1pro_imp.c"
    implementation.parent.mkdir(parents=True)
    implementation.write_text(_preprocessed())
    calls = []

    def preprocess_only(args, output, report):
        calls.append(args)
        assert "-E" in args, "Library compilation began before EEPROM policy refusal"
        assert args[-1] == str(implementation)
        return "#define ENABLE_WRITE 1\n" if "-dM" in args else _preprocessed()

    monkeypatch.setattr(native, "command", preprocess_only)
    with pytest.raises(ValueError, match="ENABLE_WRITE must remain disabled"):
        native.build_library(source, tmp_path, {"driver_scope": "all_public"}, "/tool/cc")
    assert len(calls) == 2
    assert "-DENABLE_WRITE" in calls[-1]


@pytest.fixture
def pinned_archive(argyll_archive, monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    return argyll_archive


def test_original_ambient_capability_and_factory_coefficients_from_production_source(
    production_source, tmp_path,
):
    """Actual predicate and init guard; inert types/data, no EEPROM or runtime."""
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        pytest.skip("C compiler unavailable")
    text = (production_source / "spectro/i1pro_imp.c").read_text()
    assert "#undef FAKE_AMBIENT" in text
    start = text.index("int i1pro_imp_ambient(i1pro *p) {")
    predicate = text[start:text.index("\n}\n", start) + 3]
    start = text.index("\t\tif ((m->amb_coef[0] = m->data->get_doubles")
    guard = text[start:text.index("\t\t/* Default to original EEProm", start)]
    harness = r'''
/* Extracted Argyll fragments: Graeme W. Gill, GPL-2.0-or-later.
 * Types and data provider below are inert test substitutes. */
#include <assert.h>
#include <stddef.h>
enum { instI1Pro, instI1Pro2, instI1Monitor };
enum { key_amb_coef, I1PRO_HW_CALIBINFO = 0x19 };
#define a1logd(...) ((void)0)
typedef struct data data;
struct data {
    double *(*get_doubles)(data *, int *, int);
    int count, missing;
    double values[36];
};
typedef struct {
    int capabilities, capabilities2, nwav[1];
    double *amb_coef[1];
    data *data;
} i1proimp;
typedef struct { int inited, dtype; i1proimp *m; } i1pro;
static double *get_doubles(data *d, int *n, int key) {
    assert(key == key_amb_coef); *n = d->count;
    return d->missing ? NULL : d->values;
}
@@PREDICATE@@
static int init_guard(i1pro *p) {
    i1proimp *m = p->m;
    int count;
@@GUARD@@
    return 0;
}
int main(void) {
    for (int model = instI1Pro; model <= instI1Pro2; ++model)
    for (int bits = 0; bits < 4; ++bits)
    for (int cap2 = 0; cap2 <= 1; ++cap2)
    for (int count = 35; count <= 37; ++count)
    for (int missing = 0; missing <= 1; ++missing) {
        data d = {get_doubles, count, missing, {0}};
        i1proimp m = {bits * 0x2000, cap2, {36}, {NULL}, &d};
        i1pro p = {0, model, &m};
        assert(i1pro_imp_ambient(&p) == 0);
        int code = init_guard(&p);
        int bad_coefficients = missing || count != 36;
        assert(code == (bits && bad_coefficients ? 0x19 : 0));
        if (!code) {
            p.inited = 1;
            assert(i1pro_imp_ambient(&p) == (bits != 0));
            assert((m.amb_coef[0] == NULL) == bad_coefficients);
        }
    }
    return 0;
}
'''
    path = tmp_path / "ambient-source-check.c"
    path.write_text(harness.replace("@@PREDICATE@@", predicate).replace("@@GUARD@@", guard))
    executable = tmp_path / "ambient-source-check.exe"
    subprocess.run([compiler, "-std=c99", str(path), "-o", str(executable)],
                   check=True, capture_output=True, timeout=30)
    subprocess.run([str(executable)], check=True, capture_output=True, timeout=5)


def test_fixed_reflective_filter_and_factory_reference_from_production_source(
    production_source, tmp_path
):
    """Compile authenticated getter, cap3, result and init branches with inert data."""
    import re

    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        pytest.skip("C compiler unavailable")
    driver = (production_source / "spectro/i1pro.c").read_text()
    imp = (production_source / "spectro/i1pro_imp.c").read_text()
    headers = "\n".join((production_source / path).read_text() for path in
                        ("spectro/inst.h", "spectro/i1pro_imp.h", "xicc/xspect.h"))
    # xspect.h is the active measurement enum. inst.h retains a NEVER copy.
    active = headers
    symbols = (
        "inst3_filter_none",
        "inst3_filter_UVCut",
        "inst_opt_filter_none",
        "inst_opt_filter_UVCut",
        "inst_stat_get_filter",
        "I1PRO_CAP2_UV_FILT",
        "I1PRO_HW_CALIBINFO",
    )
    constants = []
    for name in symbols:
        match = re.search(r"\b" + name + r"\s*(?:=\s*)?(0x[0-9a-fA-F]+|[0-9]+)\b", active)
        assert match, name
        constants.append(f"{name} = {match[1]}")
    # Only the live xspect.h definitions supply result enums.
    active = (production_source / "xicc/xspect.h").read_text()
    for name in ("inst_mrt_reflective", "inst_mrt_transmissive", "inst_mrc_none", "inst_mrc_uvcut"):
        match = re.search(r"\b" + name + r"\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)\b", active)
        assert match, name
        constants.append(f"{name} = {match[1]}")
    cap = driver[
        driver.index("\tif (s != NULL && s->reflective)") : driver.index(
            "\n\treturn inst_ok;", driver.index("\tif (s != NULL && s->reflective)")
        )
    ]
    getter = driver[
        driver.index("\tif (m == inst_stat_get_filter)") : driver.index("\n\t/* Set xcalstd */")
    ]
    result = imp[
        imp.index("\t\t\tif (s->trans)\n\t\t\t\tvals[i].mtype") : imp.index(
            "\n\t\t}\n", imp.index("\t\t\tif (s->trans)\n\t\t\t\tvals[i].mtype")
        )
    ]
    start = imp.index("\t\tif ((m->white_ref[0] = m->data->get_doubles")
    guard = imp[start:imp.index("\n\t\t}", start) + 4]
    start = imp.index("\t\tif ((ip = m->data->get_ints(m->data, &count, key_physfilt))")
    filter_init = imp[start:imp.index('\t\ta1logd(p->log,2,"Physical filter flag', start)]
    start = imp.index("\t\tif ((ip = m->data->get_ints(m->data, &count, key2_capabilities))")
    rev_e_init = imp[start:imp.index("\t\tif (p->log->debug", start)]
    assert "if (p->dtype == instI1Pro2)" in imp[imp.index("/* Read Rev E specific keys */"):start]
    harness = r'''
/* Argyll excerpts: Graeme W. Gill, GPL-2.0-or-later. Inert surrounding types. */
#include <assert.h>
#include <stddef.h>
#include <stdarg.h>
#define a1logd(...) ((void)0)
enum { instI1Pro, instI1Pro2, instI1Monitor };
enum { inst_ok = 0, key_white_ref, key_physfilt, key2_capabilities, @@CONSTANTS@@ };
typedef int inst_opt_filter;
typedef struct data data;
struct data {
    double *(*get_doubles)(data *, int *, int); int count, missing; double values[36];
    int *(*get_ints)(data *, unsigned *, int); int physfilt, capabilities2;
};
typedef struct { int capabilities2, physfilt, nwav[1]; double *white_ref[1]; data *data; } i1proimp;
typedef struct { i1proimp *m; int cap3, dtype; } i1pro;
typedef struct { int reflective, trans; } state;
static void capabilities(i1pro *p, state *s) { i1proimp *m = p->m; @@CAP@@ }
static int getter(i1pro *p, int m, ...) { @@GETTER@@ return -1; }
static int condition(i1pro *p, state *s) {
    i1proimp *m = p->m;
    struct {int mtype, mcond;} vals[1] = {{0,0}};
    int i = 0;
    @@RESULT@@
    assert(vals[0].mtype == inst_mrt_reflective);
    return vals[0].mcond;
}
static double *get_doubles(data *d, int *n, int key) {
    assert(key == key_white_ref); *n = d->count; return d->missing ? NULL : d->values;
}
static int init_guard(i1pro *p) { i1proimp *m = p->m; int count; @@GUARD@@ return 0; }
static int *get_ints(data *d, unsigned *count, int key) {
    *count = 1;
    assert(key == key_physfilt || key == key2_capabilities);
    return key == key_physfilt ? &d->physfilt : &d->capabilities2;
}
static int init_filters(i1pro *p) {
    i1proimp *m = p->m; int *ip; unsigned count;
    @@FILTER_INIT@@
    if (p->dtype == instI1Pro2) { @@REV_E_INIT@@ }
    return 0;
}
int main(void) {
    assert(inst3_filter_none == 0x10 && inst3_filter_UVCut == 0x80);
    assert(inst_opt_filter_none == 0 && inst_opt_filter_UVCut == 4);
    assert(inst_mrc_none == 0 && inst_mrc_uvcut == 3 && I1PRO_CAP2_UV_FILT == 0x20);
    const int raw[] = {0, 0x80, 0x81, 0x82, 0x83};
    for (int model=instI1Pro; model<=instI1Pro2; ++model)
    for (int bit=0; bit<2; ++bit)
    for (unsigned physical=0; physical<sizeof(raw)/sizeof(raw[0]); ++physical) {
        data d = {get_doubles, 36, 0, {0}, get_ints, raw[physical], bit ? I1PRO_CAP2_UV_FILT : 0};
        i1proimp m = {0, 0, {36}, {NULL}, &d};
        i1pro p = {&m, 0, model}; state s = {0, 0};
        assert(init_filters(&p) == 0 && m.physfilt == raw[physical]);
        int ultraviolet = raw[physical] == 0x82;
        int reported_bit = model == instI1Pro2 ? bit : ultraviolet;
        assert(m.capabilities2 == (reported_bit ? I1PRO_CAP2_UV_FILT : 0));
        capabilities(&p, &s); assert(p.cap3 == 0);
        s.reflective = 1; capabilities(&p, &s);
        assert(p.cap3 == (reported_bit ? 0x80 : 0x10));
        inst_opt_filter filter = -1;
        assert(getter(&p, inst_stat_get_filter, &filter) == 0);
        assert(filter == (ultraviolet ? 4 : 0));
        assert(condition(&p, &s) == (reported_bit ? 3 : 0));
        /* Rev E can disagree; unknown raw codes still make the getter return none. */
    }
    for (int model=instI1Pro; model<=instI1Pro2; ++model)
    for (int count=35; count<=37; ++count) for (int missing=0; missing<2; ++missing) {
        data d = {get_doubles, count, missing, {0}};
        i1proimp m = {0, 0, {36}, {NULL}, &d}; i1pro p = {&m, 0, model};
        assert(init_guard(&p) == ((missing || count != 36) ? I1PRO_HW_CALIBINFO : 0));
    }
}
'''
    for tag, text in {"CONSTANTS": ", ".join(constants), "CAP": cap, "GETTER": getter,
                      "RESULT": result, "GUARD": guard, "FILTER_INIT": filter_init,
                      "REV_E_INIT": rev_e_init}.items():
        harness = harness.replace("@@" + tag + "@@", text)
    source = tmp_path / "fixed-filter-source.c"
    source.write_text(harness)
    executable = tmp_path / "fixed-filter-source.exe"
    subprocess.run([compiler, "-std=c99", str(source), "-o", str(executable)],
                   check=True, capture_output=True, timeout=30)
    subprocess.run([str(executable)], check=True, capture_output=True, timeout=5)



# Reference validity now belongs to the attributed upstream patch, rather than
# a private-field guard in a separate family acquisition module.


def test_reference_checks_all_bands_without_changing_finite_floors(
    pinned_archive, production_source, tmp_path
):
    """Compile actual standard/high-resolution arithmetic with inert types only.

    Baseline accepts a finite input whose completed factor underflows to zero.
    Patched source refuses it at bands 0 and 10 on both grids while preserving
    finite low/signed white floors.
    """
    from native_argyll.proof_inputs import before_source

    cc = shutil.which("cc")
    if not cc:
        pytest.skip("C compiler unavailable")
    source_path = "spectro/i1pro_imp.c"
    original = before_source("legacy_scan_recognition", source_path, pinned_archive)
    changed = (production_source / source_path).read_text()
    for patched, source in ((0, original), (1, changed)):
        body = source[source.index("i1pro_code i1pro_compute_white_cal("):]
        body = body[:body.index("/* For adaptive mode, compute a new integration")]
        inputs = ""
        if patched:
            inputs = body[body.index("\t/* CM: validate every reference band. */"):
                          body.index("#ifdef HIGH_RES\n\t/* If we need to, fine calibrate")]
        arithmetic = body[body.index("\tif (white_ref0 == NULL)"):]
        harness = r'''
#include <math.h>
#include <assert.h>
#include <stddef.h>
#define HIGH_RES
#define I1PRO_OK 0
#define I1PRO_CAL_TRANSWHITEWARN 1
#define I1PRO_RD_WHITEREFERROR 2
typedef struct { int nwav[2], hr_inited; } context;
static int compute(context *m, double *cal_factor0, double *white_ref0, double *white_read0,
                   double *cal_factor1, double *white_ref1, double *white_read1) {
    int j, warn = I1PRO_OK;
@@INPUTS@@
@@ARITHMETIC@@
int main(void) {
    double a[36], b[36], ref0[36], ref1[36], c[36], d[36];
    for (int hr=0; hr<2; hr++) for (int trans=0; trans<2; trans++) {
        context m = {{36,36},hr};
        for (int i=0; i<36; i++) {a[i]=b[i]=2000.;ref0[i]=ref1[i]=1.;}
        assert(compute(&m,c,trans?NULL:ref0,a,d,trans?NULL:ref1,b)==0);
        assert(c[0]==.0005 && c[10]==.0005 && (!hr || (d[0]==.0005 && d[10]==.0005)));
        for (int band=0; band<=10; band+=10) for (int grid=0; grid<=hr; grid++) {
            double *white=grid?b:a;
            white[band]=-1.;
            int code=compute(&m,c,trans?NULL:ref0,a,d,trans?NULL:ref1,b);
            assert(code==(trans?I1PRO_CAL_TRANSWHITEWARN:I1PRO_OK));
            assert(isfinite(c[band]) && c[band]>0 && (!hr || (isfinite(d[band]) && d[band]>0)));
            white[band]=INFINITY;
            code=compute(&m,c,trans?NULL:ref0,a,d,trans?NULL:ref1,b);
            if (CM_PATCHED) assert(code==I1PRO_RD_WHITEREFERROR);
            else assert(code!=I1PRO_RD_WHITEREFERROR && (grid?d[band]:c[band])==0.);
            white[band]=NAN;
            code=compute(&m,c,trans?NULL:ref0,a,d,trans?NULL:ref1,b);
            if (CM_PATCHED) assert(code==I1PRO_RD_WHITEREFERROR);
            else assert(code!=I1PRO_RD_WHITEREFERROR && isnan(grid?d[band]:c[band]));
            white[band]=2000.;
            if (trans) {
                /* Finite inputs can still produce infinite completed factors.
                 * Isolate each grid so the standard guard cannot mask hires. */
                for (int i=0; i<36; i++) white[i]=1e-320;
                code=compute(&m,c,NULL,a,d,NULL,b);
                assert(code==(CM_PATCHED?I1PRO_RD_WHITEREFERROR:I1PRO_OK));
                if (!CM_PATCHED) assert(isinf(grid?d[band]:c[band]));
                for (int i=0; i<36; i++) white[i]=2000.;
            } else {
                double *ref=grid?ref1:ref0;
                double invalid[]={0.,-1.,NAN,INFINITY};
                for (int i=0; i<4; i++) {
                    ref[band]=invalid[i];
                    code=compute(&m,c,ref0,a,d,ref1,b);
                    assert(code==(CM_PATCHED?I1PRO_RD_WHITEREFERROR:I1PRO_OK));
                }
                ref[band]=1e-300;
                white[band]=1e100;
                code=compute(&m,c,ref0,a,d,ref1,b);
                assert(code==(CM_PATCHED?I1PRO_RD_WHITEREFERROR:I1PRO_OK));
                if (!CM_PATCHED) assert((grid?d[band]:c[band])==0.);
                white[band]=2000.;
                ref[band]=1.;
            }
        }
        if (CM_PATCHED) {
            for(int i=0;i<36;i++)a[i]=b[i]=0.;
            assert(compute(&m,c,NULL,a,d,NULL,b)==I1PRO_RD_WHITEREFERROR);
        }
    }
    return 0;
}
'''.replace("@@INPUTS@@", inputs).replace("@@ARITHMETIC@@", arithmetic)
        unit = tmp_path / f"reference-{patched}.c"
        unit.write_text(harness)
        executable = tmp_path / f"reference-{patched}"
        subprocess.run([cc, "-std=gnu99", f"-DCM_PATCHED={patched}", str(unit),
                        "-o", str(executable)], check=True, capture_output=True)
        subprocess.run([str(executable)], check=True, capture_output=True)


def test_legacy_recognition_verdict_rejects_wrong_averages_with_or_without_optimization(
        pinned_archive, production_source, tmp_path):
    from native_argyll import legacy_scan_recognition as recognition

    if shutil.which("cc") is None:
        pytest.skip("C compiler unavailable for source-bound offline proof")
    report = recognition.prove(pinned_archive, tmp_path / "recognition-verdict",
                               source=production_source)
    bad = copy.deepcopy(report["results"])
    for variant in ("baseline", "patched"):
        bad[variant]["negative"]["raw_patch_averages"][0][0] += 1
    assert_validator_survives_optimization(
        recognition, "_validate", report["results"], bad)


def test_worker_file_macros_do_not_disclose_source_or_build_roots(tmp_path):
    """Exercise compiler mapping, including upstream nested below the build root."""
    cc = shutil.which(native.compiler_name())
    if cc is None:
        pytest.skip("C compiler unavailable")
    output = tmp_path / "private-build-location"
    source = output / "private-upstream-location"
    source.mkdir(parents=True)
    flags = native.library_compile_flags(source, output, {}, native.platform.system())
    names = ((source / "spectro/probe.c", "./argyll-source/spectro/probe.c"),
             (output / "generated.c", "./native-build/generated.c"),
             (native.NATIVE / "session.c", "./color-measurement-app/tools/native_argyll/session.c"))
    # These are inert __FILE__ probes, never production worker code.
    unit = '#include <stdio.h>\n'
    for index, (path, _) in enumerate(names):
        unit += f'#line 1 "{path.as_posix()}"\nconst char *name{index} = __FILE__;\n'
    unit += 'int main(void) { puts(name0); puts(name1); puts(name2); return 0; }\n'
    executable = native.executable_path(output, "file-macro-probe")
    subprocess.run([cc, *flags, "-x", "c", "-", "-o", str(executable)], input=unit,
                   text=True, check=True, capture_output=True)
    ran = subprocess.run([str(executable)], text=True, check=True, capture_output=True)
    assert [Path(line).as_posix() for line in ran.stdout.splitlines()] == [
        Path(expected).as_posix() for _, expected in names]
