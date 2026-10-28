# CIE data and derived wavelength/locus illustration notice

International Commission on Illumination (CIE), *Colour-matching functions of CIE 1931 standard colorimetric observer*, 2019. DOI [10.25039/CIE.DS.xvudnb9b](https://doi.org/10.25039/CIE.DS.xvudnb9b). The inspected metadata names CIE 018:2019, Table 6 as the original source.

The CSV and metadata were obtained from the official CIE data page and its published links. The CSV is retained unchanged. SHA-256: `fa663e3535a7e0763a745993a1f0a192eb0275ac46ad2d1befd7626841e713c1`; MD5: `17cca777db64b17170f06f67ce9d3ab7`. `fetch-receipt.json` retains retrieval provenance, including unsuccessful initial resource links.

The publisher's terms are [Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/). These terms remain attached to the dataset and the derived wavelength-guide, spectral-locus and full chromaticity-field data/illustrations; they do not relabel the application's source code. No CIE endorsement is claimed.

Modifications in derived illustrations: the wavelength guide selects original
5 nm rows and uses the original CMF XYZ at one common relative scale 1, with dim
CMF tails and signed channel clamp. The locus retains all 471 original rows from
360–830 nm at 1 nm. The chromaticity field uses a separate 384×384 pixel-center
raster in xy and u′v′, original-boundary clipping and an ideal relative output
Y cap of 0.25 after negative-channel clamp. Each view composes the shared Python inverse and encoder;
RGBA8 quantization and browser scaling remain approximate. Negative target
channels clamp. These cues are not physical spectral appearance, acquired
amplitude, measured coverage or per-reading normalization. The closing endpoint
segment is the line of purples, not monochromatic light. CIE 1931 2° observer,
DOI, source digest and CC BY-SA 4.0 attribution remain attached to original and
derived data/illustrations, including eventual distribution. No endorsement is
claimed and unchanged CSV bytes are retained.

The implemented recipe, source excerpts and limits are documented in
`docs/illustrations.md` in the source distribution. The wheel retains this
notice and the original data; it does not include the source documentation.

## Offline 3D renderer

Three.js 0.186.1 is distributed under the MIT licence, retained and served at
[`vendor/LICENSE`](../vendor/LICENSE). The authenticated npm origin and file
hashes are recorded in the bundled vendor/ORIGIN.json. The Three.js sources
(`three.module.js`, `three.core.js`, `OrbitControls.js`, `ColorSpaces.js`) are
build inputs kept with the renderer sources in the source distribution
(`tools/desktop_builder/renderer/vendor`) and compiled into the served
`workspace.js` bundle; the wheel ships that bundle, this notice, the licence and
the origin record, not the sources. The core renderer files are unchanged.
Only the bare `three` imports in OrbitControls.js and ColorSpaces.js were changed
to the local `./three.module.js` path. Our coordinate/picking integration and
illustrative data are separate from that third-party renderer.
