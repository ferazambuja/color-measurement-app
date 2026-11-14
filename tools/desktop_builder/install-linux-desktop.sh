#!/bin/sh
# Register this extracted folder in the current user's application menu.
set -eu

# Command substitution strips trailing newlines, which could select another folder.
case "$0" in
    */*) CDPATH='' cd -P -- "${0%/*}/." ;;
    *) CDPATH='' cd -P -- . ;;
esac
application=$PWD
case "$application" in
    *'
'*|*"$(printf '\r')"*)
        printf '%s\n' 'Move the application to a folder without line breaks first.' >&2
        exit 1
        ;;
esac
if [ ! -x "$application/Color Measurement" ] ||
   [ ! -f "$application/_internal/color_measurement/desktop_assets/app-icon.svg" ]; then
    printf '%s\n' 'Keep this script inside the complete extracted application folder.' >&2
    exit 1
fi

# Exec has its own quoting rules inside the desktop entry's string escaping.
# Percent signs must be doubled to avoid interpreting a path as field codes.
# A fixed env executable also lets GIO validate Exec before expanding those codes.
program=$(printf '%s' "$application/Color Measurement" |
    sed 's/[\\`"$]/\\&/g; s/%/%%/g; s/\\/\\\\/g')
# env always exists, so TryExec lets menus hide the entry once this folder moves.
executable=$(printf '%s' "$application/Color Measurement" | sed 's/\\/\\\\/g')
icon=$(printf '%s' "$application/_internal/color_measurement/desktop_assets/app-icon.svg" |
    sed 's/\\/\\\\/g')
applications=${XDG_DATA_HOME:-"$HOME/.local/share"}/applications
mkdir -p "$applications"
entry=$(mktemp "$applications/.color-measurement.XXXXXX")
trap 'rm -f "$entry"' 0 HUP INT TERM
cat > "$entry" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Color Measurement
Comment=Collect and review color measurements
Exec=/usr/bin/env "$program"
TryExec=$executable
Icon=$icon
Terminal=false
Categories=Science;Graphics;
StartupWMClass=Color Measurement
EOF
chmod 644 "$entry"
mv -f "$entry" "$applications/io.github.ferazambuja.colormeasurement.desktop"
printf '%s\n' 'Color Measurement was added to your application menu.' \
    'Keep this application folder in place. Run this script again if you move it.'
