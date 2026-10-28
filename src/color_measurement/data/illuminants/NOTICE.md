# CIE standard illuminant data

International Commission on Illumination (CIE), Vienna, Austria. Original CSV and official metadata are retained unchanged under [Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/). Source data and derived/interpolated data retain these terms separately from application code. No CIE endorsement is claimed.

| Illuminant | Dataset DOI | Original CSV SHA-256 |
| --- | --- | --- |
| D50 (2022) | [10.25039/CIE.DS.etgmuqt5](https://doi.org/10.25039/CIE.DS.etgmuqt5) | `b23049c6f7b266c1c1fbe147aa271e8930ca02d6e569c5ae1804c036faea4193` |
| D65 (2019) | [10.25039/CIE.DS.hjfjmt59](https://doi.org/10.25039/CIE.DS.hjfjmt59) | `e76f210bffff3d552ef7113025da5f325d5dfec200dd4b878b1a2f3a507032cb` |
| A (2018) | [10.25039/CIE.DS.8jsxjrsn](https://doi.org/10.25039/CIE.DS.8jsxjrsn) | `61ef23fe146b8b665c74706717ab28cec7db6c9022993490bdc71991f43cb59b` |

Official resources: https://files.cie.co.at/Publications-datasets/ followed by the retained CSV/metadata filename. D50/D65 metadata cites ISO/CIE 11664-2:2022 Table B.1; A revision-2 metadata cites equation 1 and Table A.1. Metadata contains publisher checksums, validation values and interpolation guidance. The tables cover 300–830 nm at 1 nm, with relative value 100 at 560 nm. They are nominal relative SPDs, not measured absolute radiance or irradiance.

The loader authenticates unchanged bytes, uses exact integer rows or linear table interpolation within the range, and never extrapolates. For A, linear table interpolation is an explicit approximation; its metadata recommends the related formula. Derived whites additionally use the authenticated original CIE 1931 observer over 360–830 nm and a declared inclusive 1 nm sum. These CIE-derived whites differ from the separately declared rounded CSS nominal D50/D65 chromaticities used for ideal RGB conventions. See `docs/illumination.md` in the source distribution for equations, examples and limits.
