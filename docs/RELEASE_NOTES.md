# v1.1.0 — Corrected Research Release

This release supersedes the original v1.0.0 research implementation for the corrected synthetic mutation experiment. The previously published archival snapshot remains unchanged (https://zenodo.org/records/24406900).

## Changes

- Corrected Rule 13 to use a dedicated `LandingHandrail` class and height above the landing walking level. Generic railing height is not a substitute. IFC extraction, RDF mapping, rule logic, mutation protocol, and regression tests were updated accordingly.
- Corrected procedural baseline aggregation to prioritize FAIL over PASS.
- Improved illustrative MEP terminology, missing-measurement handling, and GUI presentation. These extensions are not independently validated semantic or regulatory constraints.
- Replaced the recorded synthetic experimental results with the corrected 2026-11-22 JSON and CSV files.
- Added the 2026-11-24 UTC baseline diagnostic and final 72-case computational verification results.
- Preserved the recorded M050 GUI discrepancy and all research limitations.
- Preserved the original synthetic IFC fixture bytes and existing licenses.
- Updated reproducibility documentation and the SHA-256 file manifest.
- Excluded third-party IFC models, private extracts, virtual environments, and temporary files.

## Computational Verification

All targeted regression tests passed in the local research environment, including the dedicated Rule 13 IFC mutation tests and illustrative MEP regression tests.

The final 72-case outcome-only verification was completed on 2026-11-24 UTC using one timing repetition per method and case. All non-timing case outcomes matched the corrected 30-repetition experiment recorded on 2026-11-22.

The final verification results are available at `data/results/final_verification_72.json`.

The corrected 30-repetition experiment remains the primary source of the reported performance statistics. The subsequent outcome-only verification does not replace the recorded timing benchmark.

## Research Limitations

The experimental results establish computational consistency within the controlled synthetic mutation protocol. They do not establish independently validated regulatory compliance or reproduce the nonpublic external-model experiments.

See `REPRODUCTION_VALIDATION.md` for detailed verification results and experimental limitations.

## Archival Status

The original v1.0.0 Zenodo record is preserved for provenance. A corresponding version-specific Zenodo archive for v1.1.0 is pending publication.