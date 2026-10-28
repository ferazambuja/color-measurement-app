#!/usr/bin/env python3
"""Test real PR inst/vtable and spotread over the existing synthetic PR actor.

Copyright 2026 Fernando Voltolini de Azambuja.
SPDX-License-Identifier: GPL-2.0-or-later
This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2
or later; see License2.txt.
Requires macOS and libraries from the authenticated Argyll preparation build.
Never links OS instrument transport or executes the ordinary instrument tool.
"""
import argparse
import hashlib
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
    with (output / 'commands.log').open('w') as log:
        def run(command, expected=(0,), **kwargs):
            command = list(map(str, command))
            log.write(json.dumps(command) + '\n')
            log.flush()
            if 'input' not in kwargs:
                kwargs['stdin'] = subprocess.DEVNULL
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=60, **kwargs)
            log.write(result.stdout)
            log.flush()
            if result.returncode not in expected or 'ERROR: AddressSanitizer' in result.stdout or 'runtime error:' in result.stdout:
                raise RuntimeError(result.stdout[-12000:])
            return result.stdout

        compiler = os.environ.get('CC', 'cc')
        flags = ['-g', '-O1', '-Wall', '-fsanitize=address,undefined',
                 '-fno-omit-frame-pointer', '-fno-sanitize-recover=undefined',
                 '-DUNIX', '-D_THREAD_SAFE', '-DENABLE_SERIAL', '-DENABLE_FAST_SERIAL', '-DENABLE_USB']
        flags += ['-I' + str(source / name) for name in
                  ('h', 'numlib', 'icc', 'cgats', 'rspl', 'xicc', 'gamut', 'spectro', 'plot', 'render', 'ccast')]
        constructors = set(re.findall(r'p = \(inst \*\)(new_\w+)\(', (source / 'spectro/inst.c').read_text()))
        objects = []
        sources = ['inst', 'insttypes', 'pr655', 'pr655_imp', 'disptechs', 'rspec', 'xrga']
        # A combined source's inst.c references the CR driver's pure discovery
        # parsers. Link their actual driver/engine owners without OS transport.
        if 'new_cr250' in constructors:
            sources.extend(('cr250', 'cr250_imp'))
            constructors.remove('new_cr250')
        for name in sources:
            target = output / (name + '.o')
            run([compiler, *flags, '-c', source / 'spectro' / (name + '.c'), '-o', target])
            objects.append(target)
        # instlib.ksh produces this exact header from h/aconfig.h. Compile the
        # contributed source and actual constructors in that header context;
        # this does not claim a linked standalone instrument-library package.
        (output / 'sa_config.h').write_bytes((source / 'h/aconfig.h').read_bytes())
        for name in ('inst', 'insttypes', 'pr655', 'pr655_imp'):
            run([compiler, *flags, '-DSALONEINSTLIB', '-I' + str(output), '-c',
                 source / 'spectro' / (name + '.c'), '-o', output / (name + '_standalone.o')])
        constructors.remove('new_pr655')
        stubs = output / 'unreachable.c'
        stubs.write_text('#include <stdlib.h>\n' + ''.join(
            f'void {name}(void) {{ abort(); }}\n' for name in sorted(constructors | {'setup_spyd2'})))
        target = output / 'unreachable.o'
        run([compiler, *flags, '-c', stubs, '-o', target])
        objects.append(target)
        libraries = ['spectro/libinstapp.a', 'xicc/libxcolorants.a', 'xicc/libxicc.a',
                     'gamut/libgamut.a', 'rspl/librspl.a', 'cgats/libcgats.a', 'icc/libicc.a',
                     'plot/libplot.a', 'plot/libvrml.a', 'ccast/libccast.a', 'ccast/axTLS/libaxtls.a',
                     'yajl/libyajl.a', 'tiff/libtiff.a', 'jpeg/libjpeg.a', 'png/libpng.a',
                     'zlib/libz.a', 'numlib/libui.a', 'spectro/libconv.a', 'numlib/libnum.a']
        link = [build / name for name in libraries]
        hashes = {name: hashlib.sha256((build / name).read_bytes()).hexdigest() for name in libraries}
        for framework in ('Carbon', 'Cocoa', 'IOKit', 'CoreFoundation', 'AppKit', 'AudioToolbox'):
            link += ['-framework', framework]
        link += ['-lm', '-lpthread']
        for mode in ('unit', 'spotread'):
            target = output / (mode + '_fake.o')
            defines = ['-DPR655_SPOTREAD_TEST'] if mode == 'spotread' else []
            run([compiler, *flags, *defines, '-c', source / 'spectro/pr655_driver_test.c', '-o', target])
            extra = []
            if mode == 'spotread':
                tool = output / 'spotread.o'
                run([compiler, *flags, '-c', source / 'spectro/spotread.c', '-o', tool])
                extra.append(tool)
            binary = output / (mode + '_synthetic')
            run([compiler, '-fsanitize=address,undefined', '-o', binary, target, *extra, *objects, *link])
            symbols = run(['nm', binary])
            (output / (mode + '-symbols.txt')).write_text(symbols)
            forbidden = ('_usb_get_paths', '_hid_get_paths', '_serial_get_paths', '_icoms_close_port',
                         '_icoms_set_ser_port', '_icoms_set_ser_port_ex', '_usb_open_port', '_hid_open_port',
                         '_icoms_ser_read', '_icoms_ser_write')
            if any(re.search(r'\b' + re.escape(name) + r'$', symbols, re.M) for name in forbidden):
                raise RuntimeError('OS instrument transport linked')
            if mode == 'unit':
                result = run([binary])
                (output / 'unit.log').write_text(result)
                print(result.strip())
            else:
                for model in (655, 670):
                    result = run([binary, f'-YI:Photo Research PR-{model}:synthetic-only', '-e', '-O'])
                    (output / f'spotread-{model}.log').write_text(result)
                    if 'Result is XYZ:' not in result or 'Synthetic PR transport: opens=1 closes=1 deletes=1' not in result:
                        raise RuntimeError('Actual spotread did not publish/close expected synthetic reading')
                    if result.count('M5\n') != 1 or 'M2\n' in result:
                        raise RuntimeError('Actual spotread did not use one M5 exposure')
                    print(f'spotread PR-{model}: passed')
                env = dict(os.environ, PR655_TEST_CASE='scale')
                result = run([binary, '-YI:Photo Research PR-655:synthetic-only', '-e', '-O'], env=env, input='q')
                (output / 'spotread-scale.log').write_text(result)
                if 'nearest factor 1000' not in result or 'Result is XYZ:' in result:
                    raise RuntimeError('Actual spotread failed unit-refusal path')
                print('spotread unit refusal: passed')
        if hashes != {name: hashlib.sha256((build / name).read_bytes()).hexdigest() for name in libraries}:
            raise RuntimeError('Baseline libraries changed during proof')
        (output / 'library-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('This isolated frontend harness currently targets macOS')
    if args.output:
        verify(args.build.resolve(), args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='pr655-driver-test-') as directory:
            verify(args.build.resolve(), Path(directory))


if __name__ == '__main__':
    main()
