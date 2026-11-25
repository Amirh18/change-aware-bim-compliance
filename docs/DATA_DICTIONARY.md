# Data dictionary and timing boundaries

| File | Meaning |
| --- | --- |
| `data/fixtures/stair_mep_mutation_base.ifc` | Original synthetic mutation fixture |
| `data/fixtures/stair_mep_illustrative_pass.ifc` | Illustrative fixture; its name is not a compliance label (five core failures in the baseline diagnostic) |
| `data/protocols/mutation_cases_72.json` | Prospectively specified parameter-change scenarios M001–M072, including corrected Rule 13 landing-height cases |
| `data/results/mutation_results_72_repetitions30.json` | Corrected recorded 72-case run, 30 full and 30 scoped timing samples per case |
| `data/results/mutation_results_72_repetitions30.csv` | Tabular companion to corrected JSON |
| `data/results/baseline_final_check.json` | Separate two-fixture diagnostic comparing procedural, SPARQL and SHACL outward rule statuses |

`cases[].case_id` is the stable scenario ID. `old_full_status` and `new_full_status` give detailed status maps for Rules 1–14. `full_changed_rule_ids` gives detailed changes; `full_outward_changed_rule_ids` uses outward PASS/FAIL/N/A (both NOT_APPLICABLE and INSUFFICIENT_INFORMATION map to N/A). `class_scoped_rule_ids` identifies selected rules. `gui_analyzer_extra_outward` records additional GUI outcomes. `shacl_disagreement_rule_ids` records SPARQL/SHACL detailed-status disagreements. `mean_full_seconds`, `mean_partial_seconds` and `ratio_full_over_partial` refer to per-case means and their ratio; durations are seconds.

Timing excludes IFC parsing, RDF construction, mutation setup and display. The original fixture lacks explicit units; temporary generated copies assign millimetres. The corrected results retain the M050 extra GUI outcome. Neither the records nor MEP illustrations certify regulatory compliance. No third-party IFC or private extracts are included.
