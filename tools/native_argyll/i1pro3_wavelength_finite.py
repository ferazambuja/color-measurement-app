"""i1Pro3 scans refuse nonfinite wavelength-domain results through the existing error.

The functions are Graeme W. Gill's GPL-2.0-or-later ArgyllCMS 3.5.0 work,
``spectro/i1pro3_imp.c``. Pristine line numbers follow.

``i1pro3_scan_emis_meas`` (:9628) selects each recognized patch's wavelength
rows, asks ``i1pro3_multimeas_check_wav_consistency`` whether they agree
(:9911), averages them (:9921) and multiplies the calibration factors (:9924).
The helper forms an aggregate mean (:11983) and compares each row's deviation
with it; the even/odd helper ``i1pro3_multimeas_check_wav_consistency2`` does
the same after its phase correction (:12061). With a nonfinite aggregate every
deviation comparison can be false, the maximum stays zero and the helper
reports agreement. The averaging (:12214) and the multiplication can then
overflow finite rows separately: finite bands whose sums cancel, or a finite
calibration factor times a finite band, leave nonfinite bands in ``specrd``
while the function returns ``I1PRO3_OK``.

Three bounded changes, none altering arithmetic order, thresholds or finite
signed values: both helpers refuse a nonfinite aggregate before the 40-unit
noise floor, and the scan refuses a nonfinite calibrated band with the cleanup
its adjacent consistency refusal already performs (:9912-9918), returning the
existing ``I1PRO3_RD_READINCONS``. The first helper is also called by the
polarized reflective strip path (:9325) and the second by the ordinary
reflective strip path (:8632); their arithmetic is proven here, those callers
are not exercised. The flash branch and the outer instrument API are outside
this proof.

The reviewed fork proposal (``argyll-3.5.0-i1pro3-wavelength-finite-proposed.patch``)
expresses the same three tests through its own C89 helper; the composed build
already tests this function with ``isfinite`` (``scan_normalization.py``) and
``<math.h>`` is included at :106, so this transform does the same. The pin is
each function: the scan after the scan-normalization guards, the helpers as
pinned upstream, and each repaired body.
"""

from __future__ import annotations

import difflib
import io
import json
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers, scan_normalization
from native_argyll.legacy_nonadaptive_calibration import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.proof_inputs import (
    before_source,
    production_proof,
    production_support,
    production_text,
)
from native_argyll.scan_normalization import NUMLIB_SHA256, SOURCE_PATH, _function

REPAIR_ID = 'recipe:i1pro3-wavelength-finite'

SCAN = scan_normalization.SCAN_SIGNATURE
HELPERS = ("int i1pro3_multimeas_check_wav_consistency(\n",
           "int i1pro3_multimeas_check_wav_consistency2(\n")
FUNCTIONS = (SCAN, *HELPERS)
READINCONS = 0x39


# Cases run through the extracted scan: every combination of resolution,
# position map and emissive/transmissive mode. Rows hold 10000+j, or the
# scenario's values; the third patch of three is rows 314-367, the second 214-267.
SCAN_CASES = ("valid", "negative", "zero", "subnormal", "inconsistent", "nan_cell",
              "nan_row", "inf_cell", "negative_inf", "row_sum_overflow",
              "band_sum_overflow", "calibration_overflow", "later_band_overflow",
              "later_calibration_overflow", "first_calibration_overflow",
              "last_calibration_overflow", "nan_factor")
SCAN_CONTROLS = SCAN_CASES[:5]
GRID = tuple((hr, zebra, emissive) for hr in (0, 1) for zebra in (0, 1) for emissive in (0, 1))
# (code, patches averaged, consistency checks, nonfinite bands as (constant, per nwav)).
SCAN_EXPECTED = {
    "nan_cell": ((0, 3, 3, (1, 0)), (READINCONS, 1, 2, (0, 0))),
    "nan_row": ((0, 3, 3, (0, 1)), (READINCONS, 1, 2, (0, 0))),
    "inf_cell": ((0, 3, 3, (1, 0)), (READINCONS, 1, 2, (0, 0))),
    "negative_inf": ((READINCONS, 1, 2, (0, 0)), (READINCONS, 1, 2, (0, 0))),
    "row_sum_overflow": ((0, 3, 3, (0, 3)), (READINCONS, 0, 1, (0, 0))),
    "band_sum_overflow": ((0, 3, 3, (6, 0)), (READINCONS, 1, 1, (2, 0))),
    "calibration_overflow": ((0, 3, 3, (0, 3)), (READINCONS, 1, 1, (0, 1))),
    "later_band_overflow": ((0, 3, 3, (2, 0)), (READINCONS, 2, 2, (2, 0))),
    "later_calibration_overflow": ((0, 3, 3, (0, 1)), (READINCONS, 2, 2, (0, 1))),
    "first_calibration_overflow": ((0, 3, 3, (3, 0)), (READINCONS, 1, 1, (1, 0))),
    "last_calibration_overflow": ((0, 3, 3, (3, 0)), (READINCONS, 1, 1, (1, 0))),
    "nan_factor": ((0, 3, 3, (3, 0)), (READINCONS, 1, 1, (1, 0))),
}
NWAV = (36, 109)
# The helpers called directly over six rows: (original, repaired) result.
HELPER_EXPECTED = {
    "wav1": {"valid": (0, 0), "negative": (0, 0), "zero": (0, 0), "subnormal": (0, 0),
             "inconsistent": (1, 1), "nan_cell": (0, 1), "nan_row": (0, 1), "inf_cell": (0, 1),
             "negative_inf": (1, 1), "row_sum_overflow": (0, 1), "band_sum_overflow": (0, 0),
             "calibration_overflow": (0, 0), "later_band_overflow": (0, 0),
             "later_calibration_overflow": (0, 0), "phase_difference": (1, 1)},
    "wav2": {"valid": (0, 0), "negative": (0, 0), "zero": (0, 0), "subnormal": (0, 0),
             "inconsistent": (1, 1), "nan_cell": (0, 1), "nan_row": (0, 1), "inf_cell": (0, 1),
             "negative_inf": (0, 1), "row_sum_overflow": (0, 1), "band_sum_overflow": (0, 0),
             "calibration_overflow": (0, 0), "later_band_overflow": (0, 0),
             "later_calibration_overflow": (0, 0), "phase_difference": (0, 0),
             "unequal": (0, 0), "empty": (1, 1), "unequal_nan": (0, 1), "phase_overflow": (0, 1)},
}

_CONVERT = r'''
static void convert(i1pro3 *p,int hr,int mode,double **out,double **in,int n) {
    (void)in;
    if(mode!=0 || n!=500) unexpected(); converted++;
    for(int i=0;i<n;i++) for(int j=0;j<((i1pro3imp *)p->m)->nwav[hr];j++) {
        double value=10000.+j;
        if(is("negative")) value=-value;
        if(is("zero")) value=0.;
        if(is("subnormal")) value=DBL_MIN/8.;
        if(is("inconsistent") && i==250) value*=10.;
        if(is("nan_cell") && i==250 && j==10) value=NAN;
        if(is("nan_row") && i==250) value=NAN;
        if(is("inf_cell") && i==250 && j==10) value=INFINITY;
        if(is("negative_inf") && i==250 && j==10) value=-INFINITY;
        if(is("row_sum_overflow")) value=DBL_MAX/4.;
        if(is("band_sum_overflow")) value=j<2 ? (j==0 ? 1e307 : -1e307) : 0.;
        if(is("later_band_overflow") && i>=200 && i<300)
            value=j<2 ? (j==0 ? 1e307 : -1e307) : 0.;
        if(is("later_calibration_overflow")) value=i>=200 && i<300 ? 10000. : .25;
        out[i][j]=value;
    }
}
'''
_HEAP = r'''
static void *heap_owned[4096];
static int heap_blocks;
static void *heap_malloc(size_t size) {
    void *p=malloc(size); if(!p || heap_blocks==4096) unexpected();
    heap_owned[heap_blocks++]=p; return p;
}
static void heap_free(void *p) {
    if(!p) return;
    for(int i=0;i<heap_blocks;i++) if(heap_owned[i]==p) {
        heap_owned[i]=heap_owned[--heap_blocks]; free(p); return;
    }
    unexpected();
}
#define malloc(size) heap_malloc(size)
#define free(p) heap_free(p)
'''
_WRAPPERS = r'''
static int checked(i1pro3 *p,int hr,double t,double **v,int n) {
    consistency_calls++;
    return i1pro3_multimeas_check_wav_consistency(p,hr,t,v,n);
}
static void averaged_real(i1pro3 *p,double *v,double **rows,int n,int hr) {
    averaged++;
    i1pro3_average_wavmmeas(p,v,rows,n,hr);
}
#define i1pro3_multimeas_check_wav_consistency(...) checked(__VA_ARGS__)
#define i1pro3_average_wavmmeas(...) averaged_real(__VA_ARGS__)
'''
_MAIN = r'''
int main(int argc,char **argv) {
    if(argc==3) {
        scenario=argv[1];
        i1pro3 p={0}; i1pro3imp m={0}; a1log log={0};
        p.m=&m; p.log=&log; m.nwav[0]=36; m.scan_toll_ratio=1.;
        double vals[6][36], *rows[6];
        for(int i=0;i<6;i++) { rows[i]=vals[i]; for(int j=0;j<36;j++) {
            double v=10000.+j;
            if(is("negative")) v=-v;
            if(is("zero")) v=0.;
            if(is("subnormal")) v=DBL_MIN/8.;
            if(is("inconsistent") && i==2) v*=10.;
            if(is("nan_cell") && i==2 && j==10) v=NAN;
            if(is("nan_row") && i==2) v=NAN;
            if(is("inf_cell") && i==2 && j==10) v=INFINITY;
            if(is("negative_inf") && i==2 && j==10) v=-INFINITY;
            if(is("row_sum_overflow")) v=DBL_MAX/4.;
            if(is("band_sum_overflow")) v=j<2 ? (j==0 ? 1e307 : -1e307) : 0.;
            if(is("phase_difference") && i<3) v*=1.5;
            if(is("unequal_nan") && i==2 && j==10) v=NAN;
            if(is("phase_overflow")) v=j==0 ? (i==0 ? .75*DBL_MAX : -.75*DBL_MAX) : 0.;
            vals[i][j]=v;
        }}
        int even=is("empty") ? 0
            : is("unequal") || is("unequal_nan") || is("phase_overflow") ? 1 : 3;
        int odd=is("phase_overflow") ? 1 : 6-even;
        int code=!strcmp(argv[2],"wav1")
            ? i1pro3_multimeas_check_wav_consistency(&p,0,.01,rows,6)
            : i1pro3_multimeas_check_wav_consistency2(&p,0,is("phase_overflow") ? 36. : .01,
                                                      rows,even,rows+even,odd);
        printf("{\"code\":%d,\"live_vectors\":%d}\n",code,vectors);
        return vectors ? 3 : 0;
    }
    if(argc!=5) return 2;
    scenario=argv[1];
    int hr=atoi(argv[2]), use_zebra=atoi(argv[3]), emissive=atoi(argv[4]);
    i1pro3 instrument={0}; i1pro3imp impl={0}; a1log log={0};
    instrument.m=&impl; instrument.log=&log; impl.nraw=128;
    impl.nwav[0]=36; impl.nwav[1]=109; impl.highres=hr;
    impl.mmode=emissive ? i1p3_emiss_scan : i1p3_trans_scan;
    impl.scan_toll_ratio=1.; impl.min_int_time=.001;
    impl.sens_sat=100000; impl.sens_target=1;
    i1pro3_state *state=&impl.ms[impl.mmode];
    state->trans=!emissive; state->emiss=emissive;
    state->scan=1; state->inttime=.01; state->targoscale=1.;
    double reference[128], factors[109], output_values[3][109], duration=-9876;
    double *outputs[]={output_values[0],output_values[1],output_values[2]};
    for(int j=0;j<128;j++) reference[j]=10000.;
    for(int j=0;j<109;j++)
        factors[j]=(is("calibration_overflow") || is("later_calibration_overflow")
            || (is("first_calibration_overflow") && j==0)
            || (is("last_calibration_overflow") && j==impl.nwav[hr]-1))
            ? DBL_MAX : (is("nan_factor") && j==0 ? NAN : 1.);
    for(int i=0;i<3;i++) for(int j=0;j<109;j++) output_values[i][j]=-9876.;
    state->raw_white=reference; state->cal_factor[hr]=factors;
    zebra_enabled=use_zebra;
    int code=i1pro3_scan_emis_meas(&instrument,&duration,outputs,3);
    int nonfinite=0;
    for(int i=0;i<3;i++) for(int j=0;j<impl.nwav[hr];j++) nonfinite+=!isfinite(outputs[i][j]);
    printf("{\"code\":%d,\"locate_code\":%d,\"locate_calls\":%d,\"nonfinite_proxy\":%d,"
           "\"live_matrices\":%d,\"live_vectors\":%d,\"measures\":%d,\"raw_frees\":%d,"
           "\"zebra_frees\":%d,\"converted\":%d,\"averaged\":%d,\"checks\":%d,"
           "\"nonfinite_output\":%d,\"duration_untouched\":%d,\"live_heap\":%d,\"values\":[",
           code,locate_code,locate_calls,nonfinite_proxy,matrices,vectors,measures,raw_frees,
           zebra_frees,converted,averaged,consistency_calls,nonfinite,duration==-9876,heap_blocks);
    for(int i=0;i<3;i++) for(int j=0;j<impl.nwav[hr];j++)
        printf("%s\"%a\"",i||j ? "," : "",outputs[i][j]);
    puts("]}");
    if(matrices || vectors || heap_blocks) return 3;
    return 0;
}
'''


def _probe_source(source: str, numlib: str) -> str:
    """The scan-normalization harness with real consistency, averaging and multiplication.

    Its inert stand-ins for the two helpers, the averaging and the multiplication
    are replaced by the extracted functions, its conversion stand-in by one that
    writes the scenario's wavelength rows, and every heap allocation is tracked.
    """
    text = scan_normalization._probe_source(source, numlib)
    text = text[:text.index("int main(int argc,char **argv)")] + _MAIN
    text = text.replace(_function(text, "static void convert("), _CONVERT)
    text = text.replace("static const char *scenario;", "static const char *scenario;\n"
                        "static int zebra_enabled, consistency_calls;")
    text = text.replace('if(positions && strncmp(scenario,"no_zebra_",9))',
                        "if(positions && zebra_enabled)")
    for inert in ("#define i1pro3_multimeas_check_wav_consistency(...) 0\n",
                  "#define i1pro3_average_wavmmeas(...) average(__VA_ARGS__)\n",
                  "#define vect_mul(...) ((void)0)\n"):
        if text.count(inert) != 1:
            raise ValueError(f"Scan harness stand-in changed: {inert.strip()}")
        text = text.replace(inert, "")
    if text.count("#define a1logd") != 1:
        raise ValueError("Scan harness debug stand-in changed")
    text = text.replace("#define a1logd", _HEAP + "#define a1logd", 1)
    support = []
    for name in ("vect_avg", "vect_mul"):
        match = re.search(r"(?:double|void) " + name + r"\([^)]*\)\s*\{", numlib)
        if match is None:
            raise ValueError(f"Missing upstream vector function {name}")
        support.append(_function(numlib, match.group()))
    support.append(re.search(r"^#define PATCH_CONS_THR\b[^\n]*", source, re.M).group() + "\n")
    for signature in (*HELPERS, "void i1pro3_average_wavmmeas(\n"):
        support.append(_function(source, signature))
    support.append(_WRAPPERS)
    at = text.index(SCAN)
    return text[:at] + "\n".join(support) + text[at:]


def _validate(results: dict, helpers: dict) -> None:
    old, new = results["baseline"], results["patched"]
    for helper, expected in HELPER_EXPECTED.items():
        for case, (before, after) in expected.items():
            for label, row, code in (("original", helpers[helper]["baseline"][case], before),
                                     ("patched", helpers[helper]["patched"][case], after)):
                if row["code"] != code or row["live_vectors"] != 0:
                    raise AssertionError(
                        f"{label} {helper} result for {case}: code {row['code']}, "
                        f"live vectors {row['live_vectors']}")
    for hr, zebra, emissive in GRID:
        suffix = f"/hr{hr}/zebra{zebra}/emissive{emissive}"
        for case in SCAN_CASES:
            key = case + suffix
            for row in (old[key], new[key]):
                if (row["locate_code"] != 0 or row["locate_calls"] != 1
                        or row["nonfinite_proxy"] != 0 or row["converted"] != 1
                        or row["measures"] != 3 or row["raw_frees"] != 3
                        or row["zebra_frees"] != zebra or row["live_matrices"]
                        or row["live_vectors"] or row["live_heap"]):
                    raise AssertionError(f"scan bookkeeping for {key}: {row}")
            if case in SCAN_CONTROLS:
                if old[key] != new[key]:
                    raise AssertionError(f"finite control changed for {key}")
                if new[key]["code"] != (READINCONS if case == "inconsistent" else 0):
                    raise AssertionError(f"control result for {key}: code {new[key]['code']}")
                continue
            for label, row, (code, averaged, checks, (constant, per_nwav)) in (
                    ("original", old[key], SCAN_EXPECTED[case][0]),
                    ("patched", new[key], SCAN_EXPECTED[case][1])):
                observed = (row["code"], row["averaged"], row["checks"], row["nonfinite_output"])
                if observed != (code, averaged, checks, constant + per_nwav * NWAV[hr]):
                    raise AssertionError(
                        f"{label} result for {key}: code {observed[0]}, averaged "
                        f"{observed[1]}, checks {observed[2]}, nonfinite {observed[3]}")


@production_proof
def prove(
    archive: Path,
    output: Path,
    *,
    source: Path | None = None,
    compiler: str = "cc",
    sanitize: bool = False,
) -> dict:
    """Compare the frozen predecessor and current production with inert acquisition."""
    production_root = source
    production_numsup = production_text(production_root, "numlib/numsup.c")
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        numlib = zipped.read(PREFIX + "numlib/numsup.c").decode()
        if digest(numlib.encode()) != NUMLIB_SHA256:
            raise ValueError("Pinned numlib identity changed")
        before = before_source("i1pro3_wavelength_finite", SOURCE_PATH, archive)
        after = production_text(source, SOURCE_PATH)
        output.mkdir(parents=True, exist_ok=False)
        headers = output / "headers"
        for entry in zipped.infolist():
            if entry.filename.startswith(PREFIX) and entry.filename.endswith(".h"):
                relative = Path(entry.filename.removeprefix(PREFIX))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unexpected upstream header path")
                target = headers / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zipped.read(entry))
        (headers / "sa_config.h").write_bytes(zipped.read(PREFIX + "h/aconfig.h"))
        (output / "Argyll-License2.txt").write_bytes(zipped.read(PREFIX + "License2.txt"))
    for label, text in (("baseline", before), ("patched", after)):
        (output / f"i1pro3_imp.{label}.c").write_text(text, encoding="utf-8", newline="\n")
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"composed/{SOURCE_PATH}", tofile=f"wavelength-finite/{SOURCE_PATH}",
    ))
    (output / "i1pro3-wavelength-finite.patch").write_text(diff, encoding="utf-8", newline="\n")
    report = {
        "status": "incomplete", "archive_sha256": ARCHIVE_SHA256, "upstream_path": SOURCE_PATH,
        "before_sha256": digest(before.encode()), "patched_sha256": digest(after.encode()),
        "function_sha256": {signature.strip(): {
            "before": digest(_function(before, signature).encode()),
            "after": digest(_function(after, signature).encode())} for signature in FUNCTIONS},
        "numlib_sha256": NUMLIB_SHA256, "patch_sha256": digest(diff.encode()),
        "proof_source_sha256": digest(Path(__file__).read_bytes()),
        "platform": platform.platform(), "hardware_tested": False,
        "instrument_runtime_linked": False, "sanitize": sanitize, "commands": [],
        "results": {}, "helper_results": {"wav1": {}, "wav2": {}},
        "native_refusal": "I1PRO3_RD_READINCONS (0x39)",
        "scope": "extracted scan caller with substituted acquisition and wavelength conversion; "
                 "real recognition, consistency helpers, averaging, calibration multiplication "
                 "and cleanup; outer instrument API, flash branch, reflective callers and XYZ "
                 "conversion not executed",
        "limitations": [
            "Synthetic wavelength rows, not physically attainable spectra.",
            "Dark correction, linearization, normalization and wavelength conversion are inert.",
            "Output buffers of a refused scan may hold partial intermediate values; they are "
            "not successful measurements and are not replaced with fabricated zeros.",
            "No hardware, transport or calibration cache is involved.",
        ],
    }
    windows = platform.system() == "Windows"
    report["production_numsup_sha256"] = digest(production_numsup.encode())
    baseline_headers = headers
    current_headers = output / "production-support"
    report["production_support_sha256"] = production_support(
        production_root, baseline_headers, current_headers)
    try:
        for label, text in (("baseline", before), ("patched", after)):
            headers = current_headers if label == "patched" else baseline_headers
            include = [arg for path in [headers, *[headers / d for d in
                       ("h", "numlib", "rspl", "cgats", "xicc", "spectro")]]
                       for arg in ("-I", str(path))]
            unit = output / f"{label}.c"
            unit.write_text(
                _probe_source(text, production_numsup if label == "patched" else numlib),
                encoding="utf-8", newline="\n")
            executable = output / (label + ".exe" if windows else label)
            argv = [cc, "-std=gnu99", "-O1", "-g", "-DSALONEINSTLIB",
                    *(["-DNT", "-DEN_LIBUSB0"] if windows else ["-DUNIX"]),
                    *sanitizers.flags(sanitize),
                    *include, str(unit), "-o", str(executable), "-lm"]
            run = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            (output / f"{label}-compile.log").write_text(
                run.stdout + run.stderr, encoding="utf-8", newline="\n")
            report["commands"].append({"argv": argv, "returncode": run.returncode})
            run.check_returncode()
            report["results"][label] = {}
            runs = [(f"{case}/hr{hr}/zebra{zebra}/emissive{emissive}",
                     [case, str(hr), str(zebra), str(emissive)])
                    for case in SCAN_CASES for hr, zebra, emissive in GRID]
            runs += [(f"{helper}/{case}", [case, helper])
                     for helper, expected in HELPER_EXPECTED.items() for case in expected]
            for key, arguments in runs:
                run = subprocess.run([str(executable), *arguments], capture_output=True,
                                     text=True, timeout=10)
                (output / f"{label}-{key.replace('/', '-')}.log").write_text(
                    run.stdout + run.stderr, encoding="utf-8", newline="\n")
                sanitizers.refuse(run.stderr, f"{label}/{key}")
                run.check_returncode()
                if run.stderr.strip():
                    raise RuntimeError(f"Unexpected inert-probe diagnostics for {label}/{key}")
                row = json.loads(run.stdout)
                if key.startswith("wav"):
                    helper, case = key.split("/")
                    report["helper_results"][helper].setdefault(label, {})[case] = row
                else:
                    report["results"][label][key] = row
        _validate(report["results"], report["helper_results"])
        report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    return report
