#!/bin/sh
# Offline synthetic check for the CR-250 transport engine (cr250_imp.c).
#
# Copyright 2026 Fernando Voltolini de Azambuja.
# SPDX-License-Identifier: GPL-2.0-or-later
# This material is licenced under the GNU GENERAL PUBLIC LICENSE Version 2 or later :-
# see the License2.txt file for licencing details.
#
# Compiles cr250_imp.c, cr250_test.c and the numlib support file against
# the Argyll headers with the address and undefined-behaviour sanitizers,
# runs the result once from a temporary directory, and removes it. It
# links no instrument library, opens no port and enumerates nothing. It is
# not part of the Jam build.
#
#   sh spectro/cr250_test.ksh        # from the source root, or
#   sh cr250_test.ksh [-D]           # from spectro/; -D prints engine debug
#
# CC selects the compiler (default cc).

set -eu

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"

CC=${CC:-cc}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/cr250_test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

defines="-DUNIX -DENABLE_SERIAL -DENABLE_FAST_SERIAL -DENABLE_USB"
includes="-I../h -I../numlib -I../icc -I../cgats -I../rspl -I../xicc -I../gamut -I../spectro -I../plot -I../render -I../ccast"
sanitize="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined"
libs="-lm -lpthread"
case $(uname -s) in
	Darwin) libs="$libs -framework CoreFoundation -framework Carbon" ;;
esac

$CC -g -O1 -Wall $sanitize $defines $includes \
	cr250_imp.c cr250_test.c ../numlib/numsup.c $libs -o "$tmp/cr250_test"

"$tmp/cr250_test" "$@"
