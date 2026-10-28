# Standalone runtime notices

Each target build retains its installed Python license and the exact NumPy,
SciPy and PyInstaller distribution notices. `RUNTIME.json` records versions and
notice hashes. `ORIGIN.json` retains the upstream sources of the Python/OpenSSL,
mpdecimal and liblzma license texts; these references do not claim identical
runtime binaries across operating systems. `PYTHON-BUNDLED.txt` comes from the
interpreter actually frozen into that package.

PySide6, Shiboken6 and the selected Qt libraries are version 6.10.3.
`QT-ORIGIN.json` authenticates their license texts and the complete retained
third-party attribution pages for the selected Qt modules, including Chromium.
Original copyright and license terms remain intact. This application uses the
open-source Qt distribution. It does not grant a commercial Qt license.

The runtime is a normal folder of shared libraries, not a single-file executable.
Recipients may replace or rebuild the LGPL-covered Qt/PySide libraries and debug
those modifications. Keep compatible library interfaces and architecture. The
exact upstream source locations are in `QT-ORIGIN.json`; obtain the matching
Qt module archives and PySide/Shiboken source before distributing a binary.
The application source includes its build script and dependency lock.

pySerial 3.5's complete BSD 3-Clause license is retained separately in
`PYSERIAL.txt`, authenticated by `SERIAL-ORIGIN.json`, because its wheel omits
that file. Application, Argyll, Three.js and CIE notices remain separate.
A runtime notice inventory is not a substitute for the Corresponding Source
required with a binary release. Release preparation must include the app source,
its Argyll source archive and the source for redistributed copyleft runtimes.
