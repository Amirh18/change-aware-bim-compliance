# Reproduction validation — corrected 72-case synthetic IFC mutation study

**Recorded run:** 2026-11-22 (UTC)
**Scope:** Controlled synthetic IFC4 fixture mutations; not independent regulatory certification or reproduction of the nonpublic large-model experiments.

## Execution and provenance

The supplied JSON records Python 3.11.1 (64-bit) on Windows 10, with 30 full and 30 scoped timing samples per case. The reproduction command is:

```cmd
python src/run_mutation_study.py --limit 72 --timing-repetitions 30 --out reproduced_72.json
```

The corrected JSON and CSV are distributed with unchanged numerical content (Git normalizes text line endings) under `data/results/mutation_results_72_repetitions30.json` and `.csv`. The source fixture SHA-256 is `737ac58bd9e1a7480c40bb160b6711981a8d3ed56b60129403b430251df4eaca`. The original v1.0.0 run remains in its archived version; it is not the authoritative result for the corrected Rule 13 implementation.

## Corrected recorded results

| Measure | Corrected run |
| --- | ---: |
| Completed / requested | 72 / 72 |
| Protocol target expectations matched | 72 / 72 |
| Execution errors | 0 |
| Class-scoped missed detailed rule changes | 0 |
| Class-scoped extra detailed rule changes | 0 |
| Class-scoped missed outward rule changes | 0 |
| GUI analyzer missed outward changes | 0 |
| GUI analyzer extra outward changes | 1 (M050, R1) |
| Cases with SPARQL–SHACL disagreement | 0 |
| Median full-check per-case mean | 137.960 ms |
| Median scoped-check per-case mean | 128.336 ms |
| Median of per-case full/scoped mean ratios | 1.078029× |
| Cases with lower scoped mean | 72 / 72 |

M050: full outward changes are R3, R5 and R10; the GUI additionally flags R1. Rule 13 cases M061–M065 use explicit landing height, including removal of that datum (M065: PASS to INSUFFICIENT_INFORMATION). No other core rule changes in those five cases. Timings cover SPARQL checks on already constructed graphs, excluding IFC parsing, extraction, graph construction and display.

## Subsequent author-executed regression and baseline checks

The author reported successful execution of `tests/test_rule_regressions.py`, `tests/test_rule13_ifc_mutations.py` and `tests/test_mep_illustrative.py` after the MEP/GUI corrections. These include the 1099/1100 mm Rule 13 boundary, M061–M065, MEP PASS/FAIL/N/A handling, no default guardrail height, and synthetic M015 transitions R1, R3, R5 and R18. The separate baseline report (2026-11-24 UTC) records agreement among procedural, SPARQL and SHACL outward statuses for 28 model-rule combinations: mutation base 10 PASS, 0 FAIL, 4 N/A; illustrative model 5 PASS, 5 FAIL, 4 N/A. Both have one stair flight and one railing; they do not exercise mixed-target aggregation. The original fixtures lack UnitsInContext; generated mutation copies explicitly assign millimetres.

The final 72-case outcome-only verification on 2026-11-24 UTC used one timing repetition per method and case. All non-timing case fields match the corrected 30-repetition run. See `data/results/final_verification_72.json`. The November 22 run remains the primary performance record. The published archive is https://doi.org/10.5281/zenodo.24518101.

## Limits

- One synthetic IFC source, not 72 independent projects or independently certified ground truth.
- The 18-file suite and nonpublic saved-IFC and larger-model experiment are not independently reproducible from this package.
- Regulatory applicability and physical-unit issues in the external model remain unverified.
- MEP Rules 16–18 are illustrative Python checks, not active core SPARQL/SHACL constraints.
- This report describes author-executed tests and analysis of supplied records, not a third-party independent execution.

## SHA-256 of archived result files (LF-normalized)

- `data/results/baseline_final_check.json`: `87ec22c6ddf99ea94a282300369feb28639f3e1493ddd29f35515326885c62c1`
- `data/results/final_verification_72.json`: `cacb849daaedd7ba8253792d17edf5694c54e976e1974a0d5f973ffa4b9bb24a`
- `data/results/mutation_results_72_repetitions30.csv`: `909d67ed5879bf1c9c3453f8e3c5fcd9f14faffb08b9c9deee9c30720572c346`
- `data/results/mutation_results_72_repetitions30.json`: `d76c4f5f45da022e3358b7ac95008c42379fa6d7e0248bd0aab76814222bfc13`
