"""Checked shared Argyll conversion failure and all production call sites.

Upstream functions and correction fragments are derived from Graeme W. Gill's
ArgyllCMS 3.5.0, GPL-2.0-or-later. Generated extracts retain upstream notices.
The inert proof substitutes allocation, integration and filter lookup; it never
links a transport. Caller proofs execute exact changed tail fragments, not full
acquisition methods. Scientific formulas and successful conversions are unchanged.
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

from native_argyll import sanitizers
from native_argyll.legacy_nonadaptive_calibration import ARCHIVE_SHA256, PREFIX, digest
from native_argyll.proof_inputs import before_source, production_proof, production_text
from native_argyll.spyder_calibration_slots import _function

REPAIR_ID = 'recipe:conversion-allocation'


CALLS = (('spectro/dtp20.c',
  'dtp20_read_chart',
  '\tif (ipatch_convert_xrga(vals, npatch, xcalstd_nonpol, p->target_calstd, '
  'p->native_calstd,\n'
  '\t                    instClamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP20_INTERNAL_ERROR;',
  'inst_internal_error | DTP20_INTERNAL_ERROR',
  False),
 ('spectro/dtp20.c',
  'dtp20_read_chart',
  '\tif (p->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(vals, npatch, &p->custfilt, instClamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP20_INTERNAL_ERROR;',
  'inst_internal_error | DTP20_INTERNAL_ERROR',
  True),
 ('spectro/dtp20.c',
  'dtp20_read_strip',
  '\tif (ipatch_convert_xrga(vals, npatch, xcalstd_nonpol, p->target_calstd, '
  'p->native_calstd,\n'
  '\t                    instClamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP20_INTERNAL_ERROR;',
  'inst_internal_error | DTP20_INTERNAL_ERROR',
  False),
 ('spectro/dtp20.c',
  'dtp20_read_sample',
  '\tif (ipatch_convert_xrga(val, 1, xcalstd_nonpol, p->target_calstd, p->native_calstd, '
  'clamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP20_INTERNAL_ERROR;',
  'inst_internal_error | DTP20_INTERNAL_ERROR',
  False),
 ('spectro/dtp22.c',
  'dtp22_read_sample',
  '\tif (ipatch_convert_xrga(val, 1, xcalstd_nonpol, p->target_calstd, p->native_calstd, '
  'clamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP22_INTERNAL_ERROR;',
  'inst_internal_error | DTP22_INTERNAL_ERROR',
  False),
 ('spectro/dtp22.c',
  'dtp22_read_sample',
  '\tif (p->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(val, 1, &p->custfilt, clamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP22_INTERNAL_ERROR;',
  'inst_internal_error | DTP22_INTERNAL_ERROR',
  True),
 ('spectro/dtp41.c',
  'dtp41_read_strip',
  '\tif (ipatch_convert_xrga(vals, npatch, xcalstd_nonpol, p->target_calstd, '
  'p->native_calstd,\n'
  '\t                    instClamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP41_INTERNAL_ERROR;',
  'inst_internal_error | DTP41_INTERNAL_ERROR',
  False),
 ('spectro/dtp41.c',
  'dtp41_read_strip',
  '\tif (p->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(vals, npatch, &p->custfilt, instClamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP41_INTERNAL_ERROR;',
  'inst_internal_error | DTP41_INTERNAL_ERROR',
  True),
 ('spectro/dtp41.c',
  'dtp41_read_sample',
  '\tif (ipatch_convert_xrga(val, 1, xcalstd_nonpol, p->target_calstd, p->native_calstd, '
  'clamp) != 0)\n'
  '\t\treturn inst_internal_error | DTP41_INTERNAL_ERROR;',
  'inst_internal_error | DTP41_INTERNAL_ERROR',
  False),
 ('spectro/ss.c',
  'ss_read_xy',
  '\tif (ipatch_convert_xrga(vals, npatch, \n'
  '\t                   p->filt == ss_aft_PolFilter ? xcalstd_pol : xcalstd_nonpol,\n'
  '\t                   p->target_calstd, p->native_calstd, instClamp) != 0)\n'
  '\t\treturn inst_internal_error;',
  'inst_internal_error',
  False),
 ('spectro/ss.c',
  'ss_read_xy',
  '\tif (p->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(vals, npatch, &p->custfilt, instClamp) != 0)\n'
  '\t\treturn inst_internal_error;',
  'inst_internal_error',
  True),
 ('spectro/ss.c',
  'ss_read_sample',
  '\tif (ipatch_convert_xrga(val, 1, \n'
  '\t                   p->filt == ss_aft_PolFilter ? xcalstd_pol : xcalstd_nonpol,\n'
  '\t                   p->target_calstd, p->native_calstd, clamp) != 0)\n'
  '\t\treturn inst_internal_error;',
  'inst_internal_error',
  False),
 ('spectro/i1pro_imp.c',
  'i1pro_conv2XYZ',
  '\tif (ipatch_convert_xrga(vals, nvals, xcalstd_nonpol, m->target_calstd, m->native_calstd, '
  'clamp) != 0)\n'
  '\t\treturn I1PRO_INT_CIECONVFAIL;',
  'I1PRO_INT_CIECONVFAIL',
  False),
 ('spectro/i1pro_imp.c',
  'i1pro_conv2XYZ',
  '\tif (m->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(vals, nvals, &m->custfilt, clamp) != 0)\n'
  '\t\treturn I1PRO_INT_CIECONVFAIL;',
  'I1PRO_INT_CIECONVFAIL',
  True),
 ('spectro/i1pro3_imp.c',
  'i1pro3_conv2XYZ',
  '\tif (ipatch_convert_xrga(vals, nvals, xcalstd_nonpol, m->target_calstd, m->native_calstd, '
  'clamp) != 0)\n'
  '\t\treturn I1PRO3_INT_CIECONVFAIL;',
  'I1PRO3_INT_CIECONVFAIL',
  False),
 ('spectro/i1pro3_imp.c',
  'i1pro3_conv2XYZ',
  '\tif (m->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(vals, nvals, &m->custfilt, clamp) != 0)\n'
  '\t\treturn I1PRO3_INT_CIECONVFAIL;',
  'I1PRO3_INT_CIECONVFAIL',
  True),
 ('spectro/munki_imp.c',
  'munki_conv2XYZ',
  '\tif (ipatch_convert_xrga(vals, nvals, xcalstd_nonpol, m->target_calstd, m->native_calstd, '
  'clamp) != 0)\n'
  '\t\treturn MUNKI_INT_CIECONVFAIL;',
  'MUNKI_INT_CIECONVFAIL',
  False),
 ('spectro/munki_imp.c',
  'munki_conv2XYZ',
  '\tif (m->custfilt_en &&\n'
  '\t    ipatch_convert_custom_filter(vals, nvals, &m->custfilt, clamp) != 0)\n'
  '\t\treturn MUNKI_INT_CIECONVFAIL;',
  'MUNKI_INT_CIECONVFAIL',
  True))

PATHS = (
    'spectro/xrga.c',
    'spectro/inst.c',
    'spectro/xrga.h',
    'spectro/inst.h',
    'spectro/dtp20.c',
    'spectro/dtp22.c',
    'spectro/dtp41.c',
    'spectro/ss.c',
    'spectro/i1pro_imp.c',
    'spectro/i1pro3_imp.c',
    'spectro/munki_imp.c',
)


def _census(text: str, path: str) -> dict[str, int]:
    """Reject extra calls or aliases; declaration/definition files count separately."""
    expected = {'ipatch_convert_xrga': 0, 'ipatch_convert_custom_filter': 0}
    if path in ('spectro/xrga.c', 'spectro/xrga.h'):
        expected['ipatch_convert_xrga'] = 1
    elif path in ('spectro/inst.c', 'spectro/inst.h'):
        expected['ipatch_convert_custom_filter'] = 1
    else:
        for caller_path, _, _, _, filtered in CALLS:
            if caller_path == path:
                expected['ipatch_convert_custom_filter' if filtered else 'ipatch_convert_xrga'] += 1
    # Ignore comments and string/character literals, retaining token separation.
    code = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  ' ', text)
    actual = {name: len(re.findall(r'\b' + name + r'\b', code)) for name in expected}
    if actual != expected:
        raise ValueError(f'Unexpected helper-reference census in {path}: {actual} != {expected}')
    return actual


def _inventory(files) -> dict[str, dict[str, int]]:
    inventory = {}
    for path, content in files:
        counts = _census(content.decode('latin-1'), path)
        if any(counts.values()):
            inventory[path] = counts
    if set(inventory) != set(PATHS):
        raise ValueError('Incomplete helper-reference inventory')
    return inventory


def _extract(texts: dict[str, str], originals: dict[str, str]) -> tuple[str, str]:
    xrga = texts['spectro/xrga.c']
    preamble = xrga[:xrga.index('/* A conversion equation */')]
    table = xrga[xrga.index('/* A conversion equation */'):xrga.index('/* Core conversion code.')]
    functions = ''.join(_function(xrga, name) for name in
                        ('convert_xrga', 'xspec_convert_xrga', 'ipatch_convert_xrga'))
    functions += texts['spectro/inst.c'][:texts['spectro/inst.c'].index('#include')]
    functions += _function(texts['spectro/inst.c'], 'inst_double_finite')
    functions += _function(texts['spectro/inst.c'], 'inst_valid_custom_filter')
    functions += _function(texts['spectro/inst.c'], 'ipatch_convert_custom_filter')
    for path, name in (('spectro/xrga.c', 'ipatch_convert_xrga'),
                       ('spectro/inst.c', 'ipatch_convert_custom_filter')):
        original = _function(originals[path], name)
        functions += original.replace(name + '(', name + '_baseline(', 1)
    # The complete source preamble retains its upstream author/licence notice.
    return preamble, table + functions


def _callers(texts: dict[str, str], headers: dict[str, bytes]) -> str:
    code = '/* Exact checked call fragments, not complete acquisition functions. */\n'
    for file, macro in (('dtp20.h', 'DTP20_INTERNAL_ERROR'),
                        ('dtp22.h', 'DTP22_INTERNAL_ERROR'),
                        ('dtp41.h', 'DTP41_INTERNAL_ERROR'),
                        ('i1pro_imp.h', 'I1PRO_INT_CIECONVFAIL'),
                        ('i1pro3_imp.h', 'I1PRO3_INT_CIECONVFAIL'),
                        ('munki_imp.h', 'MUNKI_INT_CIECONVFAIL')):
        code += re.search(r'^#define ' + macro + r'\s+[^\n]*',
                          headers['spectro/' + file].decode(), re.M)[0] + '\n'
    for index, (path, function, fragment, error, filtered) in enumerate(CALLS):
        if not (_function(texts[path], function).count(fragment) == 1):
            raise AssertionError('Proof verdict failed')
        code += f'/* {path}: {function} */\n'
        code += f'static int caller_{index}(ipatch *vals, int enabled) {{\n'
        code += '    proof_driver state, *p = &state, *m = &state;\n'
        code += '    ipatch *val = vals; int npatch = 2, nvals = 2, clamp = instClamp;\n'
        code += '    setup_driver(&state, enabled);\n' + fragment + '\n    return inst_ok;\n}\n'
    code += 'static struct { int (*run)(ipatch *, int); int error, filtered; } caller_cases[] = {\n'
    for index, (_, _, _, error, filtered) in enumerate(CALLS):
        code += f'    {{caller_{index}, {error}, {int(filtered)}}},\n'
    return code + '};\n'


@production_proof
def prove(archive: Path, output: Path, *, compiler: str = 'cc', source: Path | None = None,
          sanitize: bool = False) -> dict:
    """Run inert allocation tests. Optional source is the final repaired build tree.

    BEFORE is an authenticated frozen predecessor; AFTER is current production.
    Record their actual hashes without reversing later repairs. The archive
    authenticates declarations, pristine helpers and
    the successful-behavior baseline. No source tree is modified.
    """
    data = archive.read_bytes()
    if digest(data) != ARCHIVE_SHA256:
        raise ValueError('Archive SHA-256 does not match pinned ArgyllCMS 3.5.0')
    cc = shutil.which(compiler)
    if cc is None:
        raise ValueError(f'C compiler unavailable: {compiler}')
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        original = {p: zipped.read(PREFIX + p).decode() for p in PATHS}
        headers = {n.removeprefix(PREFIX): zipped.read(n) for n in zipped.namelist()
                   if n.startswith(PREFIX) and n.endswith('.h')}
        licence = zipped.read(PREFIX + 'License2.txt')
        headers['sa_config.h'] = zipped.read(PREFIX + 'h/aconfig.h')
        archive_inventory = _inventory((n.removeprefix(PREFIX), zipped.read(n))
                                       for n in zipped.namelist()
                                       if n.startswith(PREFIX) and n.endswith(('.c', '.h')))
    before = {p: before_source("conversion_allocation", p, archive) for p in PATHS}
    after = {p: production_text(source, p) for p in PATHS}
    files = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix in (".c", ".h"))
    if any(p.is_symlink() for p in files):
        raise ValueError("Symlink in helper-reference source inventory")
    source_inventory = _inventory((p.relative_to(source).as_posix(), p.read_bytes()) for p in files)
    # Both repair descriptions must match the authenticated original helper bodies.
    for p in ('spectro/xrga.c', 'spectro/inst.c'):
        name = 'ipatch_convert_xrga' if p.endswith('xrga.c') else 'ipatch_convert_custom_filter'
        if _function(before[p], name) != _function(original[p], name):
            raise ValueError(f'Unexpected original helper: {p}')
    output.mkdir(parents=True, exist_ok=False)
    inc = output / 'upstream'
    for path, value in headers.items():
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe upstream header path')
        target = inc / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(after[path].encode() if path in after else value)
    (output / 'License2.txt').write_bytes(licence)
    preamble, funcs = _extract(after, original)
    (output / 'preamble.inc').write_text(preamble + '#include "ss_imp.h"\n')
    (output / 'functions.inc').write_text(funcs)
    (output / 'callers.inc').write_text(_callers(after, headers))
    harness = Path(__file__).with_name('conversion_allocation_probe.c')
    (output / 'probe.c').write_bytes(harness.read_bytes())
    diff = ''.join(''.join(difflib.unified_diff(
        re.findall(r'[^\n]*\n|[^\n]+$', before[p]),
        re.findall(r'[^\n]*\n|[^\n]+$', after[p]),
        fromfile='a/' + p, tofile='b/' + p)) for p in PATHS)
    (output / 'conversion-allocation.patch').write_text(diff)
    report = dict(status='incomplete', archive_sha256=ARCHIVE_SHA256, sanitize=sanitize,
                  source=str(source) if source else None,
                  reference_inventory=archive_inventory,
                  production_reference_inventory=source_inventory,
                  before_sha256={p: digest(before[p].encode()) for p in PATHS},
                  patched_sha256={p: digest(after[p].encode()) for p in PATHS},
                  patch_sha256=digest(diff.encode()),
                  module_sha256=digest(Path(__file__).read_bytes()),
                  harness_sha256=digest(harness.read_bytes()),
                  instrument_runtime_linked=False, hardware_tested=False,
                  scope='actual shared helpers and all 18 checked call fragments; no acquisition',
                  limitations=['Factory, integration and filter lookup are substituted.',
                               'Caller tails are exact source fragments; acquisition and outer '
                               'cleanup are source reviewed, not executed.',
                               'Unchanged on allocation failure applies to each helper, '
                               'not the read.',
                               'No colorimetric accuracy or physical operation is tested.'],
                  commands=[], results={})
    try:
        system = platform.system()
        for label, sanitizer in ([('normal', False)] + ([('sanitized', True)] if sanitize else [])):
            exe = output / (label + ('.exe' if system == 'Windows' else ''))
            cmd = [cc, '-std=gnu99', '-O1', '-g', '-DSALONEINSTLIB',
                   *(['-DNT', '-DEN_LIBUSB0'] if system == 'Windows' else ['-DUNIX', '-pthread']),
                   *sanitizers.flags(sanitizer),
                   *['-I' + str(d) for d in [inc, output, *[inc / p for p in
                     ('spectro', 'numlib', 'xicc', 'icc', 'cgats', 'h')]]],
                   str(output / 'probe.c'), '-lm', '-o', str(exe)]
            build = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            report['commands'].append(dict(command=cmd, returncode=build.returncode))
            (output / (label + '-build.log')).write_text(build.stdout + build.stderr)
            if build.returncode:
                raise RuntimeError(f'{label} proof build failed; see log')
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            (output / (label + '.log')).write_text(run.stdout + run.stderr)
            report['commands'].append(dict(command=[str(exe)], returncode=run.returncode))
            sanitizers.refuse(run.stderr, label)
            if run.returncode or run.stderr:
                raise AssertionError(f'{label} allocation proof failed; see log')
            result = json.loads(run.stdout)
            # The current filter adds 23 scratch lifetimes: eight retry controls,
            # eight mixed-batch controls and seven callers. Each adds four factory
            # checks and one owned release: 728 + 23 * 5 = 843.
            if result['failures'] or result['callers'] != 18 or result['checks'] != 843:
                raise AssertionError(f'{label} incomplete caller proof')
            report['results'][label] = result
            if sanitizer:
                for case in ('baseline-xrga-oom', 'baseline-filter-oom'):
                    run = subprocess.run([str(exe), case], capture_output=True,
                                         text=True, timeout=30)
                    (output / (case + '.log')).write_text(run.stdout + run.stderr)
                    report['commands'].append(dict(command=[str(exe), case],
                                                   returncode=run.returncode))
                    name = ('ipatch_convert_custom_filter_baseline' if 'filter' in case
                            else 'ipatch_convert_xrga_baseline')
                    body = _function(funcs, name)
                    position = funcs.index(body) + body.index('conv->convert')
                    location = funcs.count('\n', 0, position) + 1
                    if (not run.returncode or 'member access within null pointer' not in run.stderr
                            or f'functions.inc:{location}:' not in run.stderr):
                        raise AssertionError(f'Pristine failure not reproduced: {case}')
                    report['results'][case] = dict(returncode=run.returncode,
                                                    diagnostic=True)
        report['products_sha256'] = {p.name: digest(p.read_bytes()) for p in output.iterdir()
                                      if p.is_file()}
        report['status'] = 'passed'
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True))
    return report
