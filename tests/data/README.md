# Scientific numerical test references

`ciede2000.txt` contains the 34 pairs and four-decimal expected differences from
Gaurav Sharma, Wencheng Wu and Edul N. Dalal (2005), *The CIEDE2000
Color-Difference Formula: Implementation Notes, Supplementary Test Data, and
Mathematical Observations*, Color Research & Application 30(1), 21–30,
[doi:10.1002/col.20070](https://doi.org/10.1002/col.20070).

[Authors' page](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/) and
[published numeric file](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt).
Columns 1–3 and 4–6 are the two Lab triples; column 7 is ΔE00 with unit
parametric factors. Absolute tolerance 5×10⁻⁵ covers published rounding.
The authors' MATLAB/Excel implementations are not included or translated.
These reference values have no connection to a physical instrument capture.

All 34×7 retained numbers were compared with the published numeric file during
preparation; the byte formatting differs. Separate SHA-256 identities are:

- Published source bytes: `44aebb39107128328add54fbef5ac8ee89909e50508f448a1580adea2058a4b8`.
- Retained test fixture: `db3ff1756a6fe3da5e185897f73c83fa6be5a6d530b06f1e5979f02d6d80c85d`.

The additional `(50,20,-20)` / `(50,80,80)` regression has expected ΔE00
`39.01817399392473` at absolute tolerance 10⁻¹⁰. Its provenance is an earlier
independent 85-digit decimal evaluation of the paper's equations 2–22; that
evaluation was not rerun for this implementation. It is separate from the 34
published pairs. The unequal chromas expose the rotation-term effect when the
above-turn mean-hue branch incorrectly adds 360 instead of subtracting it.
The test mutates that one branch in an isolated namespace, confirms the
published data can miss the defect, and verifies that the additional oracle
rejects it. There is no second maintained production colour formula.
