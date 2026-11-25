# v1.1.0 — corrected release candidate

This candidate supersedes the original v1.0.0 research implementation for the corrected synthetic experiment. The previously published snapshot remains unchanged (https://zenodo.org/records/24406900).

- Correct Rule 13 to use a dedicated LandingHandrail class and height above the landing walking level; generic railing height is not a substitute. Update IFC extraction, RDF mapping, rule logic, mutation protocol and regression checks accordingly.
- Preserve FAIL-first procedural baseline aggregation.
- Correct illustrative MEP terminology, missing-measurement handling and GUI presentation; do not interpret these extensions as validated semantic constraints.
- Replace the current recorded synthetic results with the corrected 2026-11-22 JSON and CSV; add the 2026-11-24 UTC baseline diagnostic.
- Preserve the recorded M050 GUI discrepancy and all research limitations.
- Keep original synthetic fixture bytes and existing licenses; exclude third-party IFC models, private extracts, environments and caches.

The corrected recorded timing run predates the later GUI and baseline edits. Targeted regression tests passed locally; a final all-case outcome verification is recommended before release. GitHub and Zenodo publication are pending. See REPRODUCTION_VALIDATION.md.
