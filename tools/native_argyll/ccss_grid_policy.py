"""Application-only positive-wavelength policy over the repaired CCSS reader.

Argyll owns count, type, finite-value and ordered-grid safety. The application
also requires positive wavelengths, preserving its explicit-correction policy.
This overlay changes no other reader behavior.
"""

REPAIR_ID = "app-policy:ccss-grid"
PATH = "xicc/ccss.c"
PORTABLE = (
    '\t/* A multi-band grid must have positive spacing. */\n'
    '\tif (!(sp.spec_wl_long > sp.spec_wl_short)) {\n'
    '\t\tsprintf(p->e.m,"Input file spectral grid is not ordered");\n'
    '\t\treturn 1;\n\t}\n'
)
APPLICATION = (
    '\t/* CM: a multi-band grid must have positive spacing. */\n'
    '\tif (!(sp.spec_wl_short > 0.0 && sp.spec_wl_long >= sp.spec_wl_short\n'
    '\t   && sp.spec_wl_long > sp.spec_wl_short)) {\n'
    '\t\tsprintf(p->e.m,"Input file spectral grid is not positive and ordered");\n'
    '\t\treturn 1;\n\t}\n'
)


def patch(text: str) -> str:
    if text.count(PORTABLE) != 1 or APPLICATION in text:
        raise ValueError("Pinned portable CCSS grid policy changed")
    return text.replace(PORTABLE, APPLICATION)
