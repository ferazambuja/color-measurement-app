#!/usr/bin/env python3
"""Test the CR-250 inst driver and spotread with no OS instrument transport.

Copyright 2026 Fernando Voltolini de Azambuja.
SPDX-License-Identifier: GPL-2.0-or-later
This material is licenced under the GNU GENERAL PUBLIC LICENSE Version 2 or later;
see the License2.txt file for licencing details.

Requires macOS, clang, Python 3, and libraries from a completed Argyll Jam
spotread build. Never executes the ordinary spotread binary. Generated objects,
symbol lists and command logs use a temporary directory unless --output is given.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile


def verify(build, output):
    source = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True, exist_ok=True)
    log = (output / 'commands.log').open('w')

    def run(command, expected=None, **kwargs):
        command = [str(value) for value in command]
        log.write(json.dumps(command) + '\n')
        log.flush()
        # A key-driven frontend never inherits a terminal: EOF, or the keys given.
        if 'input' not in kwargs:
            kwargs['stdin'] = subprocess.DEVNULL
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=60, **kwargs)
        log.write(result.stdout)
        log.flush()
        if expected is None and result.returncode:
            raise RuntimeError(result.stdout[-12000:])
        if expected is not None and result.returncode not in expected:
            raise RuntimeError(result.stdout[-12000:])
        if 'ERROR: AddressSanitizer' in result.stdout or 'runtime error:' in result.stdout:
            raise RuntimeError(result.stdout[-12000:])
        return result.stdout

    compiler = os.environ.get('CC', 'cc')
    flags = ['-g', '-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
             '-fno-sanitize-recover=undefined', '-Wall', '-DUNIX', '-D_THREAD_SAFE',
             '-DENABLE_SERIAL', '-DENABLE_FAST_SERIAL', '-DENABLE_USB']
    flags += ['-I' + str(source / directory) for directory in
              ('h', 'numlib', 'icc', 'cgats', 'rspl', 'xicc', 'gamut', 'spectro',
               'plot', 'render', 'ccast')]
    objects = []
    for name in ('inst', 'insttypes', 'cr250', 'cr250_imp', 'cr250_fake', 'disptechs', 'rspec', 'xrga'):
        obj = output / (name + '.o')
        run([compiler, *flags, '-c', source / 'spectro' / (name + '.c'), '-o', obj])
        objects.append(obj)

    # Foreign constructors abort; their driver objects are never linked.
    constructors = set(re.findall(r'p = \(inst \*\)(new_\w+)\(',
                                  (source / 'spectro/inst.c').read_text()))
    constructors.remove('new_cr250')
    stubs = output / 'unreachable.c'
    stubs.write_text('#include <stdlib.h>\n' + ''.join(
        f'void {name}(void) {{ abort(); }}\n'
        for name in sorted(constructors | {'setup_spyd2'})))
    obj = output / 'unreachable.o'
    run([compiler, *flags, '-c', stubs, '-o', obj])
    objects.append(obj)

    # Deliberately exclude libinst, icoms, USB and HID objects.
    libraries = ['spectro/libinstapp.a', 'xicc/libxcolorants.a', 'xicc/libxicc.a',
                 'gamut/libgamut.a', 'rspl/librspl.a', 'cgats/libcgats.a', 'icc/libicc.a',
                 'plot/libplot.a', 'plot/libvrml.a', 'ccast/libccast.a',
                 'ccast/axTLS/libaxtls.a', 'yajl/libyajl.a', 'tiff/libtiff.a',
                 'jpeg/libjpeg.a', 'png/libpng.a', 'zlib/libz.a', 'numlib/libui.a',
                 'spectro/libconv.a', 'numlib/libnum.a']
    link = [build / library for library in libraries]
    for framework in ('Carbon', 'Cocoa', 'IOKit', 'CoreFoundation', 'AppKit', 'AudioToolbox'):
        link += ['-framework', framework]
    link += ['-lm', '-lpthread']

    # On MSVC and NT, numsup.h makes snprintf and vsnprintf _snprintf and
    # _vsnprintf, which leave a full buffer unterminated and return -1. The
    # 'legacy' run rebuilds the driver with that behaviour, so a text cut to
    # size that is not terminated explicitly fails here, not only on Windows.
    legacy_crt = output / 'legacy_crt.h'
    legacy_crt.write_text('''#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
static int legacy_vsnprintf(char *buf, size_t size, const char *fmt, va_list args) {
	/* Two current reply fields plus the primary diagnostic can exceed 8 KiB. */
	char full[16384];
	int n = vsnprintf(full, sizeof(full), fmt, args);
	if (n < 0 || (size_t)n >= sizeof(full)) abort();
	if ((size_t)n < size) { memcpy(buf, full, n + 1); return n; }
	memcpy(buf, full, size);
	return -1;
}
static int legacy_snprintf(char *buf, size_t size, const char *fmt, ...) {
	va_list args;
	int n;
	va_start(args, fmt);
	n = legacy_vsnprintf(buf, size, fmt, args);
	va_end(args);
	return n;
}
#undef snprintf
#undef vsnprintf
#define snprintf legacy_snprintf
#define vsnprintf legacy_vsnprintf
''')
    legacy_objects = []
    for obj in objects:
        if obj.stem in ('cr250', 'cr250_imp'):
            obj = output / (obj.stem + '_legacy.o')
            run([compiler, *flags, '-include', legacy_crt, '-c',
                 source / 'spectro' / (obj.stem[:-len('_legacy')] + '.c'), '-o', obj])
        legacy_objects.append(obj)

    for mode in ('unit', 'legacy', 'spotread'):
        obj = output / (mode + '_fake.o')
        binary = output / (mode + '_synthetic')
        define = '-DCR250_SPOTREAD_TEST' if mode == 'spotread' else '-Dmain=uimain'
        run([compiler, *flags, define, '-c', source / 'spectro/cr250_driver_test.c', '-o', obj])
        extra = []
        if mode == 'spotread':
            main_obj = output / 'spotread.o'
            run([compiler, *flags, '-c', source / 'spectro/spotread.c', '-o', main_obj])
            extra.append(main_obj)
        run([compiler, '-fsanitize=address,undefined', '-o', binary, obj,
             *extra, *(legacy_objects if mode == 'legacy' else objects), *link])
        symbols = run(['nm', binary])
        (output / (mode + '-symbols.txt')).write_text(symbols)
        forbidden = ('_usb_get_paths', '_hid_get_paths', '_serial_get_paths',
                     '_icoms_close_port', '_icoms_set_ser_port', '_icoms_set_ser_port_ex',
                     '_usb_open_port', '_hid_open_port')
        for symbol in forbidden:
            if re.search(r'\b' + re.escape(symbol) + r'$', symbols, re.M):
                raise RuntimeError('OS transport unexpectedly linked: ' + symbol)
        if mode in ('unit', 'legacy'):
            result = run([binary])
            (output / (mode + '.log')).write_text(result)
            print(('legacy CRT: ' if mode == 'legacy' else '') + result.strip())
            continue

        # The real frontend must preserve XYZ, refuse unsupported reads, and
        # use direct selection without ever calling the discovery factory.
        # spotread requests spectra when the driver offers them, so each
        # CR-250 case that completes M also reads and checks RM Spectrum.
        cr250 = '-YI:Colorimetry Research CR-250:synthetic-only'
        cr100 = '-YI:Colorimetry Research CR-100:synthetic-only'
        cr300 = '-YI:Colorimetry Research CR-300:synthetic-only'
        cases = [
            ('success', cr250, ['-e'], {}, 1, 'Result is XYZ: 12.500000 25.000000 6.250000', [0], True, ''),
            ('telephoto', cr250, ['-p'], {}, 1, 'Result is XYZ:', [0], True, ''),
            # -i makes spotread compute XYZ from the published spectrum itself:
            # Argyll's own conversion must reproduce the instrument's XYZ.
            ('spectral-xyz', cr250, ['-e', '-iC'], {}, 1, 'Result is XYZ: 12.500000 25.000000 6.2500', [0], True, ''),
            ('spectrum', cr250, ['-e', '-s'], {}, 1, 'Spectrum from 380.000 to 780.000 nm in 81 steps', [0], True, ''),
            ('cr100', cr100, ['-e'], {}, 1, 'Result is XYZ: 12.500000 25.000000 6.250000', [0], True, ''),
            ('cr300', cr300, ['-e'], {}, 1, 'Result is XYZ: 12.500000 25.000000 6.250000', [0], True, ''),
            ('cr250-match', cr250, ['-e', '-y2'], {}, 1, 'Result is XYZ:', [0], True, ' SM Match 9; SM UserCalibMode 2;'),
            ('cr100-matrix', cr100, ['-e', '-y2'], {}, 1, 'Result is XYZ:', [0], True, ' SM Matrix 7; SM UserCalibMode 1;'),
            ('cr300-match', cr300, ['-e', '-y2'], {}, 1, 'Result is XYZ:', [0], True, ' SM Match 9; SM UserCalibMode 2;'),
            ('refresh', cr250, ['-e', '-Yr'], {}, 1, 'Synthetic writes: SM SyncMode 1;', [0], True, ' SM SyncMode 1;'),
            ('refresh-verbose', cr250, ['-e', '-Yr', '-v'], {}, 1, 'Synthetic writes: SM SyncMode 1;', [0], True, ' SM SyncMode 1;'),
            ('norefresh-verbose', cr250, ['-e', '-Yn', '-v'], {'CR250_TEST_CASE': 'sync-auto'}, 1, 'Synthetic writes: SM SyncMode 0;', [0], True, ' SM SyncMode 0;'),
            ('rate', cr250, ['-e', '-YR:59.94'], {}, 1, 'SM SyncMode 2; SM SyncFreq 59.94;', [0], True, ' SM SyncMode 2; SM SyncFreq 59.94;'),
            ('averages', cr250, ['-e', '-Ya'], {}, 1, 'Synthetic writes: SM ExposureX 10;', [0], True, ' SM ExposureX 10;'),
            ('averages-refused', cr250, ['-e', '-Yaaa'], {}, 1, 'Setting no of averages to 10 failed', [0], True, ''),
            ('nonadaptive', cr250, ['-e', '-YA'], {}, 1, 'Synthetic writes: SM ExposureMode 1;', [0], True, ' SM ExposureMode 1;'),
            ('nonadaptive-cr100', cr100, ['-e', '-YA'], {}, 1, 'Result is XYZ: 12.500000 25.000000 6.250000', [0], True, ''),
            ('setup', cr250, ['-e'], {'CR250_TEST_CASE': 'setup'}, 0, 'RS UserCalibMode:Match', [0], False, ' SM UserCalibMode 0;'),
            ('warning', cr250, ['-e'], {'CR250_TEST_CASE': 'warning'}, 1, '100:M:Synchronization failed', [0], False, ''),
            # spotread offers a retry after a misread; 'q' declines it.
            ('spectrum-scale', cr250, ['-e'], {'CR250_TEST_CASE': 'scale1000'}, 1, 'nearest factor 1000', [0], False, ''),
            ('firmware', cr250, ['-e'], {'CR250_TEST_CASE': 'firmware'}, 0, 'firmware 1.26 or later', [255], False, ''),
            ('model', cr250, ['-e'], {'CR250_TEST_CASE': 'model'}, 0, 'RC Model:CR-300', [255], False, ''),
            # -Yr passes spotread's capability check before SyncMode is known; the first
            # read then refuses it, before any measurement and with nothing written.
            ('refresh-nosync', cr250, ['-e', '-Yr'], {'CR250_TEST_CASE': 'no-sync'}, 0, 'has no SyncMode setting to set to Auto', [0], False, ''),
        ]
        for label, selection, args, variables, count, expected_text, statuses, publishes_xyz, writes in cases:
            env = dict(os.environ)
            env.pop('CR250_TEST_CASE', None)
            env.update(variables)
            keys = {'input': 'q'} if label == 'spectrum-scale' else {}
            result = run([binary, selection, '-O', *args], expected=statuses, env=env, **keys)
            (output / (label + '.log')).write_text(result)
            if expected_text not in result or f'Synthetic transport: M={count} opens=1 closes=1' not in result:
                raise RuntimeError(label + ': unexpected result\n' + result)
            if ('Result is XYZ:' in result) != publishes_xyz:
                raise RuntimeError(label + ': unexpected XYZ publication')
            # Exactly the expected settings are written, and nothing where the
            # instrument is already in its contract state (the CR-100 never gets ExposureMode).
            if re.findall(r'^Synthetic writes:(.*)$', result, re.M) != [writes]:
                raise RuntimeError(label + ': unexpected writes\n' + result)
            print('spotread synthetic ' + label + ': passed')
        invalid = (['-YI'], ['-YI:'], ['-YI::synthetic-only'],
                   ['-YI:Colorimetry Research CR-100:'],
                   ['-YI:CR-250:synthetic-only'],
                   [cr250, '-c1'], [cr250, cr100])
        for args in invalid:
            result = run([binary, *args, '-e', '-O'], expected=[1])
            if 'Synthetic transport:' in result or 'Result is XYZ:' in result:
                raise RuntimeError('Invalid selection reached a transport')
        print(f'spotread synthetic invalid selection: {len(invalid)} cases passed')
    log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True, help='Root of completed Jam build')
    parser.add_argument('--output', type=Path, help='Keep generated sources, logs and test binaries here')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('This isolated frontend harness currently targets macOS')
    if args.output:
        verify(args.build.resolve(), args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='cr250-driver-test-') as temporary:
            verify(args.build.resolve(), Path(temporary))


if __name__ == '__main__':
    main()
