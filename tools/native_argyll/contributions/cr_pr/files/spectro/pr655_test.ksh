#!/bin/sh
# Offline synthetic PR engine check. It opens no ports and links no OS transport.
# Copyright 2026 Fernando Voltolini de Azambuja.
# SPDX-License-Identifier: GPL-2.0-or-later
# This material is licensed under the GNU GENERAL PUBLIC LICENSE Version 2 or later; see License2.txt.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
CC=${CC:-cc}
task_tmp=$(mktemp -d "${TMPDIR:-/tmp}/pr655_test.XXXXXX")
trap 'rm -rf "$task_tmp"' EXIT HUP INT TERM
defines="-DUNIX -DENABLE_SERIAL -DENABLE_FAST_SERIAL"
includes="-I../h -I../numlib -I../icc -I../cgats -I../rspl -I../xicc -I../gamut -I../spectro -I../plot -I../render -I../ccast"
sanitize="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined"
libs="-lm -lpthread"
case $(uname -s) in
	Darwin) libs="$libs -framework CoreFoundation -framework Carbon" ;;
esac
$CC -g -O1 -Wall -Wextra $sanitize $defines $includes \
	pr655_imp.c pr655_test.c ../numlib/numsup.c $libs -o "$task_tmp/pr655_test"
"$task_tmp/pr655_test"
