# Reproduction validation — 72-case synthetic IFC mutation study

**Validation run:** 2026-11-20 (UTC)  
**Scope:** Controlled synthetic IFC4 fixture mutations; **not** independent regulatory certification or end-to-end reproduction of the nonpublic large-model experiments.

## Execution and provenance

The research team ran the organized repository's full study using Python 3.11.1 (64-bit) on Windows 10:

```cmd
python tests/test_rule_regressions.py
python src/run_mutation_study.py --limit 72 --timing-repetitions 30 --out reproduced_72.json
```

The generated `reproduced_72.json` and `reproduced_72.csv` were supplied for comparison with the original committed `data/results/mutation_results_72_repetitions30.json`. The complete rerun JSON had SHA-256 `a592ec88507fe50a6fca83ca27f2ba4b6542d74f912d6aac935acaffd9182033`; this new output is **not** included in the minimal release, but its summary and checksum are recorded here.

## Results

| Measure | Original run | Reproduced run |
| --- | ---: | ---: |
| Completed / requested mutation cases | 72 / 72 | 72 / 72 |
| Protocol expectations matched | 72 / 72 | 72 / 72 |
| Execution errors | 0 | 0 |
| Class-scoped missed outward rule changes | 0 | 0 |
| GUI analyzer missed outward rule changes | 0 | 0 |
| GUI analyzer extra outward rule changes | 1 (M050) | 1 (M050) |
| Cases with SHACL disagreements | 0 | 0 |
| Full rule-check time: median across case means | 145.467 ms | 144.186 ms |
| Scoped rule-check time: median across case means | 125.994 ms | 124.990 ms |
| Full/scoped ratio: median across per-case ratios | 1.156875× | 1.151955× |
| Cases with lower scoped mean time | 71 / 72 | 72 / 72 |

Each case contains **30 full-check samples and 30 scoped-check samples**. Automated comparison showed **identical rule-status maps and change-detection outcomes for all 72 cases** between the original and reproduced JSON files. Both runs identify the same additional GUI outcome in M050: GUI flags rule R1 although the full-outward rule changes are R3, R5, and R10. This discrepancy is reported rather than hidden.

The original and reproduced runs identify the same input fixture checksum: SHA-256 `737ac58bd9e1a7480c40bb160b6711981a8d3ed56b60129403b430251df4eaca`.

## Interpretation and limits

- The 72 cases are controlled edits generated from one synthetic source IFC; they are **not** 72 independent projects or expert-verified regulatory truth cases.
- These benchmark times cover SPARQL rule evaluation on **already built RDF graphs**. They exclude IFC parsing, RDF extraction/construction, and display; do not interpret them as end-to-end performance.
- Some per-case timing ratios become unusually large when few/no rules require rechecking. Report the median-of-ratios with this qualification, not as an expected universal acceleration.
- The fixture initially lacked `UnitsInContext`; temporary generated copies explicitly declare millimetres.
- The original 18-file suite, proprietary-source large-model experiments, and independently assessed legal/regulatory rule applicability are **not** reproduced by this distribution.
- This record documents an **author-executed** reproducibility run and comparison of supplied output files; it is not an independently operated third-party test.

For the original full machine-readable results, see `data/results/mutation_results_72_repetitions30.json`. For licensing and exclusions, see `LICENSES.md` and `README.md`.
