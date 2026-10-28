"""Identity of the pinned ArgyllCMS source this project is built and checked against.

One fact, in one place, because it is about the ARCHIVE and not about any
instrument. It lived in ``i1pro3.py`` while eighteen files imported it, including
``argyll_description.py``, which is device-agnostic: the generic path depended on
a per-device module for a constant that has nothing to do with that device.

Every native check, build report and capability manifest is bound to this digest,
so a build made from a different archive is refused rather than silently compared
against tables derived from another version.
"""

from __future__ import annotations

__all__ = ["ARCHIVE_SHA256", "ARCHIVE_VERSION"]

#: SHA-256 of ``Argyll_V3.5.0_src.zip``, the archive every excerpt, line citation
#: and driver manifest in this repository is taken from.
ARCHIVE_SHA256 = "f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f"

#: The upstream release that digest names, as it spells itself.
ARCHIVE_VERSION = "3.5.0"
