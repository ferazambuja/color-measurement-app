"""Exact-word baseline queries; source-bound cached DTP41, no transport linked."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from color_measurement.argyll_capability import (
    MODE_FLAGS,
    MODE_ROUTES,
    baseline_mode_words,
    mode_word,
    supported_modes,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("persona,driver,enum_name,no_modes", [
    ("i1pro2", "i1pro", "i1p_mode", "i1p_no_modes"),
    ("i1pro3", "i1pro3", "i1p3_mode", "i1p3_no_modes"),
    ("munki", "munki", "mk_mode", "mk_no_modes"),
])
def test_spectral_persona_matches_actual_upstream_mode_conversion(
    argyll_archive, tmp_path, persona, driver, enum_name, no_modes,
):
    """Compare the real stub predicate with a complete upstream converter.

    Only capability words and inert structs are supplied. No constructor,
    transport, calibration, measurement or upstream runtime is linked.
    """
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler unavailable for source-bound mode comparison")

    def enum(text, name):
        end = text.index("} " + name + ";") + len("} " + name + ";")
        return text[text.rindex("typedef enum {", 0, end):end]

    with zipfile.ZipFile(argyll_archive) as archive:
        header = archive.read("Argyll_V3.5.0/spectro/inst.h").decode()
        implementation = archive.read(f"Argyll_V3.5.0/spectro/{driver}.c").decode()
        modes = archive.read(f"Argyll_V3.5.0/spectro/{driver}_imp.h").decode()
        license_text = archive.read("Argyll_V3.5.0/License2.txt")
    signature = f"static {enum_name} {driver}_convert_mode("
    assert implementation.count(signature) == 1
    begin = implementation.index(signature)
    end = implementation.index("/* Check device measurement mode */", begin)
    converter = implementation[begin:end]
    assert converter.rstrip().endswith("}")
    stub = (ROOT / "tools/native_argyll/chart_stub.c").read_text()
    begin = stub.index("static inst_code check_mode(")
    predicate = stub[begin:stub.index("static inst_code set_mode(", begin)]
    assert predicate.rstrip().endswith("}")
    macros = "\n".join(line for line in header.splitlines()
                       if line.startswith(("#define IMODETST(", "#define IMODETST2(")))
    polarization = re.search(r"^\s*inst_opt_filter_pol\s*=\s*(0x[0-9a-f]+),", header, re.M)
    assert polarization is not None
    candidates = [f"inst_mode_{name}" for name in MODE_ROUTES]
    candidates += ["inst_mode_emis_spot | inst_mode_emis_nonadaptive",
                   "inst_mode_emis_tele | inst_mode_emis_nonadaptive"]
    code = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
/* ENUMS */
/* MACROS */
typedef struct { int filt; } i1pro3imp;
typedef struct { inst_mode cap; void *m; } inst;
typedef inst i1pro;
typedef inst i1pro3;
typedef inst munki;
static int initialized = 1;
static void require(int condition) { assert(condition); }
static int am(const char *name) { return !strcmp(name, "PERSONA"); }
static int is(const char *name) { (void)name; return 0; }
static void capabilities(inst *p, inst_mode *cap, void *cap2, void *cap3) {
    (void)cap2; (void)cap3; *cap = p->cap;
}
/* CONVERTER */
/* PREDICATE */
int main(void) {
    i1pro3imp internal = {inst_opt_filter_pol};
    inst p = {0, &internal};
    inst_mode candidates[] = { CANDIDATES };
    inst_mode full = inst_mode_ref_spot | inst_mode_ref_strip | inst_mode_trans_spot
        | inst_mode_trans_strip | inst_mode_emis_spot | inst_mode_emis_tele
        | inst_mode_emis_strip | inst_mode_emis_ambient | inst_mode_emis_ambient_flash
        | inst_mode_emis_nonadaptive | inst_mode_colorimeter | inst_mode_spectral
        | inst_mode_highres;
    unsigned cases = 0;
    for (int variant=0; variant<4; variant++) {
        p.cap = full;
        if (variant==1) p.cap &= ~(inst_mode_ambient | inst_mode_ambient_flash);
        if (variant==2) p.cap &= ~inst_mode_spectral;
        if (variant==3) p.cap &= ~inst_mode_highres;
        for (unsigned i=0; i<sizeof(candidates)/sizeof(candidates[0]); i++)
        for (int output=0; output<4; output++) {
            inst_mode word = candidates[i] | inst_mode_colorimeter
                | ((output & 1) ? inst_mode_spectral : 0)
                | ((output & 2) ? inst_mode_highres : 0);
            int actual = DRIVER_convert_mode(&p, word) != NO_MODES;
            int simulated = check_mode(&p, word) == inst_ok;
            if (actual != simulated) {
                fprintf(stderr, "word=%#x cap=%#x actual=%d simulated=%d\n",
                        word, p.cap, actual, simulated);
                return 1;
            }
            cases++;
        }
    }
    printf("%u\n", cases);
    return 0;
}
'''
    code = (code.replace("/* ENUMS */", "\n".join((enum(header, "inst_mode"),
            enum(header, "inst_code"), enum(modes, enum_name))))
        .replace("/* MACROS */", macros + "\n#define inst_opt_filter_pol " + polarization[1])
        .replace("PERSONA", persona).replace("/* CONVERTER */", converter)
        .replace("/* PREDICATE */", predicate).replace("CANDIDATES", ", ".join(candidates))
        .replace("DRIVER_convert_mode", driver + "_convert_mode").replace("NO_MODES", no_modes))
    # Retain the attributed inputs beside the generated inert comparison.
    (tmp_path / f"{driver}.original.c").write_text(implementation)
    (tmp_path / f"{driver}_imp.original.h").write_text(modes)
    (tmp_path / "inst.original.h").write_text(header)
    (tmp_path / "License2.txt").write_bytes(license_text)
    source = tmp_path / "mode-comparison.c"
    source.write_text(code)
    executable = tmp_path / "mode-comparison"
    subprocess.run([compiler, "-std=c99", str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(4 * 4 * len(candidates))


def test_queries_cover_request_words_but_do_not_invent_option_checks():
    all_modes = 0
    for word in (*MODE_ROUTES.values(), *MODE_FLAGS.values()):
        all_modes |= word
    words = baseline_mode_words(all_modes)
    assert len(words) == len(set(words)) == 40
    for name in supported_modes(all_modes, 0x1f):
        for spectral in (False, True):
            assert mode_word(name, spectral=spectral) in words
    assert all(not word & MODE_FLAGS["highres"] for word in words)
    assert baseline_mode_words(MODE_ROUTES["emis_spot"] | MODE_FLAGS["colorimeter"]) == (
        mode_word("emissive_spot", spectral=False),)


def test_initialized_dtp41_queries_reuse_cache_even_after_probe_error(argyll_archive, tmp_path):
    """Calls REAL three DTP41 functions plus collector. Commands are counted fakes.

    This proves cached description/binding queries add no I/O; it does not call
    init_inst, validate transport or make check_mode a readiness guarantee.
    """
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler unavailable for source-bound mode-query proof")
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        spec = importlib.util.spec_from_file_location("native_mode_builder",
                                                     ROOT / "tools/argyll_native_check.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.write_mode_check_candidates(tmp_path)
        with zipfile.ZipFile(argyll_archive) as archive:
            original = archive.read("Argyll_V3.5.0/spectro/dtp41.c").decode()
        functions = []
        # These three small pinned functions have only indented inner braces.
        # Keep their original bodies intact; the binary uses fake commands only.
        for signature in ("static void\tdiscover_capabilities(", "void dtp41_capabilities(",
                          "static inst_code\ndtp41_check_mode("):
            assert original.count(signature) == 1
            start = original.index(signature)
            end = original.index("\n}\n", start) + 3
            functions.append(original[start:end])
        functions = "\n".join(functions)
    finally:
        sys.path.pop(0)
    definitions = "\n".join(f"#define inst_mode_{name} 0x{word:x}u"
                            for name, word in {**MODE_ROUTES, **MODE_FLAGS, "none": 0}.items())
    code = r'''
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef unsigned inst_mode;
typedef unsigned inst2_capability;
typedef unsigned inst3_capability;
typedef unsigned inst_code;
typedef struct _inst inst;
struct _inst {
    inst_mode cap;
    inst2_capability cap2;
    inst3_capability cap3;
    int inited;
    void (*capabilities)(inst *, inst_mode *, inst2_capability *, inst3_capability *);
    inst_code (*check_mode)(inst *, inst_mode);
};
typedef inst dtp41;
#define inst_ok 0u
#define inst_unsupported 0x50000u
#define inst2_prog_trig 0x100u
#define inst2_user_trig 0x200u
#define inst2_user_switch_trig 0x400u
#define inst3_none 0u
#define MAX_MES_SIZE 500
#define IMODETST(mbits, mode) (((mbits) & (mode)) == (mode))
#define IMODETST2(mcap, mbits, mode) (IMODETST(mcap, mode) && IMODETST(mbits, mode))
static unsigned commands;
static int failed_probe;
static inst_code dtp41_command(dtp41 *p, const char *request, char *reply,
                              int count, double timeout) {
    (void)p; (void)reply; (void)count; (void)timeout;
    assert(!strcmp(request, "0119CF\r") || !strcmp(request, "0019CF\r"));
    commands++;
    return failed_probe ? inst_unsupported : inst_ok;
}
/* DEFINITIONS */
/* FUNCTIONS */
#include "mode_checks.h"
int main(void) {
    for (failed_probe = 0; failed_probe <= 1; failed_probe++) {
        inst meter = {0};
        cm_mode_checks checks;
        inst_mode cap;
        meter.inited=1;
        meter.capabilities=dtp41_capabilities;
        meter.check_mode=dtp41_check_mode;
        commands=0;
        meter.capabilities(&meter, &cap, NULL, NULL);
        assert(commands==2);  /* The EXISTING initial query populates the cache. */
        cm_collect_mode_checks(&meter, cap, &checks);
        cm_collect_mode_checks(&meter, cap, &checks);
        assert(commands==2 && checks.count==(failed_probe ? 4u : 8u));
        for (unsigned i=0; i<checks.count; i++) assert(checks.checks[i].code==inst_ok);
        fputs("{\"case\":\"cached\"", stdout); cm_print_mode_checks(stdout, &checks); puts("}");
        meter.check_mode=NULL;
        cm_collect_mode_checks(&meter, cap, &checks);
        assert(commands==2 && !checks.method_available);
        fputs("{\"case\":\"missing\"", stdout); cm_print_mode_checks(stdout, &checks); puts("}");
    }
    return 0;
}
'''.replace("/* DEFINITIONS */", definitions).replace("/* FUNCTIONS */", functions)
    source = tmp_path / "mode-checks.c"
    source.write_text(code)
    executable = tmp_path / "mode-checks"
    subprocess.run([compiler, "-std=c99", "-I", str(tmp_path), "-I",
                    str(ROOT / "tools/native_argyll"), str(source), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    rows = [json.loads(line) for line in subprocess.run(
        [str(executable)], check=True, capture_output=True, text=True).stdout.splitlines()]
    for row in rows:
        value = row["baseline_mode_checks"]
        assert value["scope"] == "initialized_before_mode_configuration"
        assert sorted(item["mode_word"] for item in value["checks"]) == list(
            baseline_mode_words(value["mode_capabilities"]))
        assert {item["native_code"] for item in value["checks"]} == (
            {None} if row["case"] == "missing" else {0})
