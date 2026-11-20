# Prepublication release notes

This archive is a **renamed, reorganized release copy** of the research team's
corrected synthetic-case experiments. The synthetic IFC and JSON files retain
their original bytes. Python references to filenames and directories were
updated to match the published folder structure; the scientific rule definitions
and numeric test thresholds were not intentionally changed.

| Original development name | Public repository path |
| --- | --- |
| `stairCases - v2.py` | `src/ifc_extractor.py` |
| `ui_staircases - ChangeAdded.py` | `src/compliance_gui.py` |
| `run_72_ifc_mutations.py` | `src/run_mutation_study.py` |
| `run_local_ifc_baseline.py` | `src/run_ifc_baseline.py` |
| `test_phase5_fixes.py` | `tests/test_rule_regressions.py` |
| `test_stair_mep.ifc` | `data/fixtures/stair_mep_mutation_base.ifc` |
| `test_stair_mep_pass.ifc` | `data/fixtures/stair_mep_illustrative_pass.ifc` |
| `mutation_cases_72.json` | `data/protocols/mutation_cases_72.json` |
| `mutation_results_72_timing30.json` | `data/results/mutation_results_72_repetitions30.json` |

The prior public-release candidate had already removed several nonfunctional
hardcoded private-model fallbacks; the underlying compliance formulas are
preserved. The code is **not** a certified regulatory checking engine.

The original 18-file implementation agreement suite is not fully deposited.
The separate large IFC used for the actual saved-IFC experiment is neither
provided nor independently reproducible from this minimal release.

Run all tests with the supported Python 3.11 environment **before publishing**.
