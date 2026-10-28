"""Source-bound SpyderX2/2024 black-cache regression proof.

Extracts the four named functions from pinned Argyll source. The transport and
calibration-file calls are in-memory stand-ins; no device or Argyll tool runs.
Argyll function bodies remain Graeme W. Gill's GPL-2.0-or-later work; the
generated proof retains Argyll's License2.txt beside its copied definitions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from native_argyll import sanitizers
from native_argyll.proof_inputs import before_source, production_proof, production_text

from color_measurement.native_receipt import native_worker_name

ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"
PREFIX = "Argyll_V3.5.0/"
SOURCE_PATH = "spectro/spydX2.c"
HEADER_PATH = "spectro/spydX2.h"
CALF_PATH = "spectro/rspec.c"
EXPECTED_ORIGINAL = "0605716f89cdd2986a342245c1e60e901314306cde6dcf63cd0dfb4d37113668"
EXPECTED_HEADER = "8a6732a325c6a16c7b0a6644cfc83225bbdd3da84c9f51b38a0f5844f55289e6"
EXPECTED_CALF = "05adc19bb0df58a7e0d216e819958e301882677124661ee6a91d4048820825da"
FUNCTIONS = (
    "spydX2_GetReading",
    "spydX2_BlackCal",
    "spydX2_save_calibration",
    "spydX2_restore_calibration",
)
DEFINITION_LINES = {
    "spydX2_GetReading": 579,
    "spydX2_BlackCal": 715,
    "spydX2_save_calibration": 1703,
    "spydX2_restore_calibration": 1748,
}


def extract_span(source: str, name: str) -> tuple[int, int, str]:
    # A forward declaration is not a definition. The signature must reach its
    # opening brace without crossing a semicolon, and must occur exactly once.
    prefix = r"(?m)^static (?:inst_code|int)\s+" + re.escape(name) + r"\([^;{}]*\)\s*\{"
    matches = list(re.finditer(prefix, source))
    if len(matches) != 1:
        raise ValueError(f"expected one definition of {name}; found {len(matches)}")
    match = matches[0]
    body = source[match.start() :]
    end = re.search(r"(?m)^\}", body)
    if end is None:
        raise ValueError(f"unterminated source function {name}")
    copied = body[: end.end()]
    if copied.count(name + "(") != 1 or copied.count("{") != copied.count("}"):
        raise ValueError(f"incomplete source function {name}")
    return match.start(), match.start() + len(copied), copied


def check_extraction(source: str, *, pinned_lines: bool = True) -> dict[str, dict[str, int | str]]:
    spans = {}
    for name in FUNCTIONS:
        result_type = "inst_code" if name in ("spydX2_GetReading", "spydX2_BlackCal") else "int"
        prototype = f"static {result_type} {name}(int x);\n"
        definition = f"static {result_type} {name}(int x) {{\nreturn x;\n}}"
        start, end, copied = extract_span(prototype + definition, name)
        if (start, end, copied) != (len(prototype), len(prototype + definition), definition):
            raise ValueError(f"prototype-before-definition control failed for {name}")
        try:
            extract_span(prototype, name)
        except ValueError:
            pass
        else:
            raise ValueError(f"prototype-only control accepted for {name}")
        start, end, copied = extract_span(source, name)
        start_line = source.count("\n", 0, start) + 1
        end_line = source.count("\n", 0, end) + 1
        if pinned_lines and start_line != DEFINITION_LINES[name]:
            raise ValueError(f"wrong source definition line for {name}: {start_line}")
        spans[name] = {
            "start_byte": len(source[:start].encode()),
            "end_byte": len(source[:end].encode()),
            "start_line": start_line,
            "end_line": end_line,
            "copied_sha256": hashlib.sha256(copied.encode()).hexdigest(),
        }
    return spans


C_TEMPLATE = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define ARGYLL_VERSION 350
#define SPYDX2_OK 0
#define SPYDX2_INT_CAL_SAVE 1
#define SPYDX2_INT_CAL_RESTORE 2
#define inst_ok 0
#define a1logd(...) ((void)0)
typedef int inst_code;
typedef struct { int unused; } a1log;
typedef struct {
    int s5[6];
    double mat[3][6], gain[3], off[3];
} SpX2calinfo;
typedef struct {
    a1log *log;
    char serno[9];
    int ix, bcal_done, bcal[6], lo_secs;
    time_t bdate;
    SpX2calinfo cinfo[1];
} spydX2;

typedef struct {
    int ef, rd, nbytes, lo_secs;
    unsigned int chsum;
    size_t pos;
    unsigned char scratch[256];
} calf;
static unsigned char cache[512];
static size_t cache_len;
static int raw_reply[6];
static int checks, failures;
#define CHECK(value) do { checks++; if (!(value)) { failures++; \
    fprintf(stderr, "failed line %d: %s\n", __LINE__, #value); } } while (0)

static int spydX2_measSettup(spydX2 *p) { (void)p; return inst_ok; }
static int spydX2_Measure(spydX2 *p, int raw[6]) {
    (void)p; memcpy(raw, raw_reply, sizeof raw_reply); return inst_ok;
}
static int calf_open(calf *x, a1log *log, char *name, int wr) {
    (void)log; (void)name; memset(x, 0, sizeof *x);
    if (wr) cache_len = 0;
    else if (!cache_len) return 1;
    return 0;
}
static int calf_done(calf *x) { (void)x; return 0; }
static void calf_rewind(calf *x) {
    x->ef = 0; x->chsum = 0; x->nbytes = 0; x->pos = 0;
}
static void digest(calf *x, const unsigned char *bytes, size_t n) {
    size_t i;
    for (i = 0; i < n; i++)
        x->chsum = ((x->chsum << 13) | (x->chsum >> 19)) + bytes[i];
    x->nbytes += (int)n;
}
static void write_bytes(calf *x, const void *source, size_t n) {
    if (x->ef) return;
    if (cache_len + n > sizeof cache) { x->ef = 1; return; }
    memcpy(cache + cache_len, source, n);
    digest(x, cache + cache_len, n);
    cache_len += n;
}
static void read_bytes(calf *x, void *target, size_t n) {
    if (x->ef) return;
    if (x->pos + n > cache_len) { x->ef = 1; return; }
    memcpy(target, cache + x->pos, n);
    digest(x, cache + x->pos, n);
    x->pos += n;
}
static void calf_wints(calf *x, int *values, int n) {
    write_bytes(x, values, (size_t)n * sizeof(int));
}
static void calf_wtime_ts(calf *x, time_t *values, int n) {
    write_bytes(x, values, (size_t)n * sizeof(time_t));
}
static void calf_wstrz(calf *x, char *value) {
    int n = (int)strlen(value) + 1;
    calf_wints(x, &n, 1);
    write_bytes(x, value, (size_t)n);
}
static void calf_rints2(calf *x, int *values, int n) {
    read_bytes(x, values, (size_t)n * sizeof(int));
}
static void calf_rints(calf *x, int *values, int n) {
    read_bytes(x, x->rd ? (void *)values : (void *)x->scratch,
               (size_t)n * sizeof(int));
}
static void calf_rtime_ts(calf *x, time_t *values, int n) {
    read_bytes(x, x->rd ? (void *)values : (void *)x->scratch,
               (size_t)n * sizeof(time_t));
}
static void calf_rstrz2(calf *x, char **value) {
    int n = 0;
    calf_rints2(x, &n, 1);
    if (x->ef || n < 1 || n > 128) { x->ef = 1; return; }
    free(*value);
    *value = malloc((size_t)n);
    if (!*value) { x->ef = 1; return; }
    read_bytes(x, *value, (size_t)n);
    if (!x->ef && (*value)[n - 1] != '\0') x->ef = 1;
}

/* EXTRACTED */

static void setup(spydX2 *p) {
    memset(p, 0, sizeof *p);
    strcpy(p->serno, "12345678");
    p->cinfo[0].mat[0][4] = 1.0;
    p->cinfo[0].mat[1][5] = 1.0;
    p->cinfo[0].mat[2][0] = 1.0;
    p->cinfo[0].gain[0] = p->cinfo[0].gain[1] = p->cinfo[0].gain[2] = 1.0;
}
static void old_cache(const spydX2 *p) {
    calf x;
    int version = ARGYLL_VERSION, size = (int)sizeof *p;
    int done = p->bcal_done;
    time_t date = p->bdate;
    CHECK(calf_open(&x, NULL, "fake", 1) == 0);
    calf_wints(&x, &version, 1);
    calf_wints(&x, &size, 1);
    calf_wstrz(&x, (char *)p->serno);
    calf_wints(&x, &done, 1);
    calf_wtime_ts(&x, &date, 1);
    calf_wints(&x, (int *)p->bcal, 3);
    calf_wints(&x, (int *)&x.chsum, 1);
    CHECK(x.ef == 0 && calf_done(&x) == 0);
}
int main(void) {
    spydX2 first, reopened, legacy;
    double before[3], after[3];
    int i, restore_status, legacy_status;
    setup(&first);
    for (i = 0; i < 6; i++) raw_reply[i] = i + 1;
    CHECK(spydX2_BlackCal(&first) == inst_ok);
    first.bcal_done = 1; first.bdate = 123;
    for (i = 0; i < 6; i++) CHECK(first.bcal[i] == i + 1);
    for (i = 0; i < 6; i++) raw_reply[i] = 20;
    CHECK(spydX2_GetReading(&first, before) == inst_ok);
    CHECK(before[0] == 15.0 && before[1] == 14.0 && before[2] == 19.0);
    CHECK(spydX2_save_calibration(&first) == SPYDX2_OK);
    setup(&reopened);
    restore_status = spydX2_restore_calibration(&reopened);
    CHECK(restore_status == SPYDX2_OK);
    CHECK(reopened.bcal_done == 1);
    CHECK(reopened.bdate == 123);
    CHECK(spydX2_GetReading(&reopened, after) == inst_ok);
#ifdef EXPECT_FIXED
    for (i = 0; i < 6; i++) CHECK(reopened.bcal[i] == i + 1);
    for (i = 0; i < 3; i++) CHECK(after[i] == before[i]);
#else
    for (i = 0; i < 3; i++) CHECK(reopened.bcal[i] == i + 1);
    for (i = 3; i < 6; i++) CHECK(reopened.bcal[i] == 0);
    CHECK(after[0] == 20.0 && after[1] == 20.0 && after[2] == 19.0);
#endif
    old_cache(&first);
    setup(&legacy);
    legacy_status = spydX2_restore_calibration(&legacy);
#ifdef EXPECT_FIXED
    CHECK(legacy_status == SPYDX2_INT_CAL_RESTORE);
    CHECK(legacy.bcal_done == 0 && legacy.bdate == 0);
    for (i = 0; i < 6; i++) CHECK(legacy.bcal[i] == 0);
#else
    CHECK(legacy_status == SPYDX2_OK);
    CHECK(legacy.bcal_done == 1 && legacy.bdate == 123);
#endif
    printf("before=[%.0f,%.0f,%.0f] after=[%.0f,%.0f,%.0f] "
           "restore=%d legacy=%d checks=%d failures=%d\n",
           before[0], before[1], before[2], after[0], after[1], after[2],
           restore_status, legacy_status, checks, failures);
    return failures != 0;
}
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_recorded(command: list[str], output: Path, label: str) -> subprocess.CompletedProcess[str]:
    record = output / f"{label}.json"
    stdout = output / f"{label}.stdout"
    stderr = output / f"{label}.stderr"
    timeout_seconds = 30
    record.write_text(
        json.dumps(
            {"argv": command, "status": "started", "timeout_seconds": timeout_seconds},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        # TimeoutExpired may hold bytes even when text=True. Preserve the bytes
        # and invocation before refusing the proof; a killed mutant is not caught.
        partial_out = error.stdout or b""
        partial_err = error.stderr or b""
        stdout.write_bytes(partial_out if isinstance(partial_out, bytes) else partial_out.encode())
        stderr.write_bytes(partial_err if isinstance(partial_err, bytes) else partial_err.encode())
        record.write_text(
            json.dumps(
                {
                    "argv": command,
                    "status": "timed_out",
                    "timeout_seconds": timeout_seconds,
                    "exit": None,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        raise RuntimeError(f"{label} timed out; retained partial output") from error
    except OSError as error:
        stdout.write_text("")
        stderr.write_text(str(error) + "\n")
        record.write_text(
            json.dumps(
                {
                    "argv": command,
                    "status": "spawn_failed",
                    "timeout_seconds": timeout_seconds,
                    "exit": None,
                    "error": str(error),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        raise RuntimeError(f"{label} failed to start; retained error") from error
    stdout.write_text(result.stdout)
    stderr.write_text(result.stderr)
    record.write_text(
        json.dumps(
            {
                "argv": command,
                "status": "completed",
                "timeout_seconds": timeout_seconds,
                "exit": result.returncode,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    return result


@production_proof
def prove(
    archive: Path, output: Path, *, source: Path | None = None, compiler: str = "cc"
) -> dict:
    """Compile actual cache/read functions with in-memory transport/cache stand-ins.

    The failing definitions are frozen; the repaired definitions come from the
    application's current production source. The model follows rspec.c's two-pass restore;
    it does not exercise the filesystem, real ABI, device or physical accuracy.
    """
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Archive SHA-256 does not match pinned ArgyllCMS 3.5.0")
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        old = before_source("spyder_black_cache", SOURCE_PATH, archive)
        header = zipped.read(PREFIX + HEADER_PATH)
        calf = zipped.read(PREFIX + CALF_PATH)
        licence = zipped.read(PREFIX + "License2.txt")
    if hashlib.sha256(old.encode()).hexdigest() != EXPECTED_ORIGINAL:
        raise ValueError("Pinned Spyder source hash changed")
    if (
        hashlib.sha256(header).hexdigest() != EXPECTED_HEADER
        or hashlib.sha256(calf).hexdigest() != EXPECTED_CALF
    ):
        raise ValueError("Pinned header or calf implementation changed")
    if "int bcal[6];" not in header.decode():
        raise ValueError("Six-channel calibration declaration missing")
    if "#define ENABLE_NONVCAL" not in old or "#undef ENABLE_BLACK_CAL" not in old:
        raise ValueError("Calibration policy changed")
    fixed = production_text(source, SOURCE_PATH)
    compiled_bytes = (source / SOURCE_PATH).read_bytes()
    original_spans = check_extraction(old)
    candidate_spans = check_extraction(fixed, pinned_lines=False)
    extraction = {
        "original": original_spans,
        "candidate": candidate_spans,
        "coordinate_sources": {
            "original": {"encoding": "utf-8", "normalization": "none",
                         "sha256": hashlib.sha256(old.encode()).hexdigest()},
            "candidate": {"encoding": "utf-8", "normalization": "universal newlines to LF",
                          "sha256": hashlib.sha256(fixed.encode()).hexdigest()},
        },
        "controls": "prototype_before_definition_selected; prototype_only_refused",
    }
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f"C compiler unavailable: {compiler}")
    output.mkdir(parents=True, exist_ok=False)
    (output / "Argyll-License2.txt").write_bytes(licence)
    (output / "source-extraction.json").write_text(
        json.dumps(extraction, sort_keys=True, indent=2) + "\n"
    )
    compiler = cc
    ub_source = output / "ubsan_canary.c"
    ub_binary = output / native_worker_name("ubsan_canary")
    ub_source.write_text(
        "int main(void) { volatile int sanitizer_canary = 2147483647; "
        "sanitizer_canary += 1; return 0; }\n"
    )
    address_source = output / "asan_canary.c"
    address_binary = output / native_worker_name("asan_canary")
    address_source.write_text(
        "#include <stdlib.h>\n"
        "int main(void) { volatile char *p = (volatile char *)malloc(1); "
        "if (!p) return 2; p[1] = 1; free((void *)p); return 0; }\n"
    )
    base_command = [compiler, "-std=c99", "-O0", "-g", "-Wall", "-Wextra",
                    *sanitizers.flags(True)]
    built = run_recorded(
        [*base_command, str(ub_source), "-o", str(ub_binary)], output, "ubsan_canary.compile"
    )
    if built.returncode:
        raise ValueError("UBSan canary compile failed")
    ub_canary = run_recorded([str(ub_binary)], output, "ubsan_canary.run")
    if (
        ub_canary.returncode == 0
        or "runtime error: signed integer overflow" not in ub_canary.stderr
    ):
        raise ValueError("UBSan canary did not demonstrate enforced UB detection")
    built = run_recorded(
        [*base_command, str(address_source), "-o", str(address_binary)],
        output,
        "asan_canary.compile",
    )
    if built.returncode:
        raise ValueError("ASan canary compile failed")
    address_canary = run_recorded([str(address_binary)], output, "asan_canary.run")
    if address_canary.returncode == 0 or "ERROR: AddressSanitizer" not in address_canary.stderr:
        raise ValueError("ASan canary did not demonstrate enforced bounds detection")
    outputs = {}

    def variants():
        # The actual production behavior must pass before deriving its negative controls.
        yield "original", old, False, True
        yield "fixed", fixed, True, True
        for label, anchor, replacement in (
            ("save_regression", "calf_wints(&x, p->bcal, 6);", "calf_wints(&x, p->bcal, 3);"),
            ("restore_regression", "calf_rints(&x, p->bcal, 6);", "calf_rints(&x, p->bcal, 3);"),
        ):
            if fixed.count(anchor) != 1:
                raise ValueError(f"Missing or ambiguous cache mutation target: {anchor}")
            yield label, fixed.replace(anchor, replacement, 1), True, False

    for label, source, expect_fixed, expect_pass in variants():
        functions = "\n\n".join(extract_span(source, name)[2] for name in FUNCTIONS)
        c_file = output / f"{label}.c"
        binary = output / native_worker_name(label)
        c_file.write_text(C_TEMPLATE.replace("/* EXTRACTED */", functions))
        command = [*base_command]
        if expect_fixed:
            command.append("-DEXPECT_FIXED")
        command.extend([str(c_file), "-o", str(binary)])
        built = run_recorded(command, output, f"{label}.compile")
        if built.returncode:
            raise ValueError(f"{label} compile failed; see retained stderr")
        run = run_recorded([str(binary)], output, f"{label}.run")
        outputs[label] = {
            "exit": run.returncode,
            "stdout": run.stdout.strip(),
            "stderr": run.stderr.strip(),
            "source_sha256": sha(c_file),
            "binary_sha256": sha(binary),
        }
        sanitizers.refuse(run.stderr, label)
        if expect_pass and run.returncode:
            raise ValueError(f"{label} proof failed; see retained stderr")
        if not expect_pass and (
            run.returncode != 1
            or "failed line" not in run.stderr
            or not re.search(r"failures=[1-9][0-9]*", run.stdout)
        ):
            raise ValueError(f"{label} regression was not caught by an assertion")
        expected_xyz = (
            "after=[15,14,19]"
            if label == "fixed"
            else "after=[20,20,19]"
            if label == "original"
            else None
        )
        if expected_xyz and (
            "before=[15,14,19]" not in run.stdout or expected_xyz not in run.stdout
        ):
            raise ValueError(f"{label} XYZ values changed")
    report = {
        "status": "passed",
        "source": EXPECTED_ORIGINAL,
        "header": EXPECTED_HEADER,
        "calf_source": EXPECTED_CALF,
        "candidate": hashlib.sha256(fixed.encode()).hexdigest(),
        "compiler": compiler,
        "compiled_source_sha256": hashlib.sha256(compiled_bytes).hexdigest(),
        "hardware_tested": False,
        "filesystem_tested": False,
        "source_extraction_sha256": sha(output / "source-extraction.json"),
        "ubsan_canary": {
            "exit": ub_canary.returncode,
            "source_sha256": sha(ub_source),
            "binary_sha256": sha(ub_binary),
        },
        "asan_canary": {
            "exit": address_canary.returncode,
            "source_sha256": sha(address_source),
            "binary_sha256": sha(address_binary),
        },
        "runs": outputs,
    }
    (output / "report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    manifest = {path.name: sha(path) for path in sorted(output.iterdir()) if path.is_file()}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    report = prove(args.archive, args.output, source=args.source)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "report_sha256": sha(args.output / "report.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
