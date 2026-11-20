# Data dictionary and timing boundaries

| File | Meaning |
| --- | --- |
| `data/fixtures/stair_mep_mutation_base.ifc` | Original synthetic mutation fixture; formerly `test_stair_mep.ifc` |
| `data/fixtures/stair_mep_illustrative_pass.ifc` | Illustrative positive-case fixture; formerly `test_stair_mep_pass.ifc` |
| `data/protocols/mutation_cases_72.json` | Prospectively specified synthetic parameter-change scenarios M001–M072 |
| `data/results/mutation_results_72_repetitions30.json` | Recorded 72-case results, 30 timing samples per case; formerly `mutation_results_72_timing30.json` |

`cases[].case_id` refers to the stable scenario ID M001–M072. Rules 1–14 are
implemented in `src/compliance_gui.py`, using semantic checks and the ontology
in `src/stair_ontology.ttl`.

**Timing scope:** rule checking on prepared RDF graphs. Mutation setup, IFC
parsing, RDF graph construction, and rendering are excluded. Durations recorded
in the JSON are in seconds. The exact synthetic fixture bytes and their SHA-256
are unchanged despite the improved filename.

**Scientific caveats:** Results do not constitute independent regulatory
validation; the original fixture omits explicit project units (generated copies
assign millimetres); M050 contains one extra GUI outcome. No externally supplied
IFC or its detailed extracts are deposited here.
