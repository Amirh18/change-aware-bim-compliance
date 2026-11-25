# Change-Aware BIM Compliance Checking

**Code and synthetic IFC reproducibility package** for the manuscript:

> **Change-Aware BIM Compliance Checking: A Semantic Knowledge-Graph Framework for Pre-Commit Rule Re-evaluation**
>
> Amirhossein Asgari · Hosein Taghaddos · Amirmohammad Ramezanianpour

This repository hosts the research-team-owned source code, two synthetic IFC fixtures,
the 72-case controlled mutation protocol, and the associated recorded experimental results.
It does **not** contain the externally supplied IFC model used in the manuscript's
larger-model case study. The complete 72-case synthetic mutation study was rerun
successfully in the supported environment; see `docs/REPRODUCTION_VALIDATION.md`.
This is the corrected v1.1.0 release. The original v1.0.0 snapshot remains at https://zenodo.org/records/24406900. The corrected archive is available at https://doi.org/10.5281/zenodo.24518101.

## Repository layout

```text
change-aware-bim-compliance/
├── README.md                    Project overview and reproduction instructions
├── CITATION.cff                 Machine-readable citation metadata
├── LICENSE                      MIT license for software and ontology
├── LICENSE-DATA-CC-BY-4.0.md    CC BY 4.0 license for synthetic data
├── LICENSES.md                  File-by-file license scope
├── requirements.txt             Recorded core dependency versions
├── src/
│   ├── run_mutation_study.py      Main 72-case mutation and checking experiment
│   ├── run_ifc_baseline.py        Whole-model baseline checker
│   ├── benchmark_full_pipeline.py Wall-clock baseline benchmark
│   ├── ifc_extractor.py          Procedural IFC attribute extractor
│   ├── compliance_gui.py         GUI and rule implementations
│   ├── rdf_mapper.py             IFC-to-RDF mapping
│   ├── semantic_checks.py        SPARQL status classification
│   └── stair_ontology.ttl        OWL ontology (SHACL shapes are constructed in compliance_gui.py)
├── tests/
│   ├── test_rule_regressions.py   Targeted regression checks
│   ├── test_rule13_ifc_mutations.py IFC-level Rule 13 checks
│   └── test_mep_illustrative.py  Illustrative MEP checks
├── data/
│   ├── fixtures/
│   │   ├── stair_mep_mutation_base.ifc
│   │   └── stair_mep_illustrative_pass.ifc
│   ├── protocols/
│   │   └── mutation_cases_72.json
│   └── results/
│       ├── mutation_results_72_repetitions30.json
│       ├── mutation_results_72_repetitions30.csv
│       ├── baseline_final_check.json
│       └── final_verification_72.json
└── docs/
    ├── DATA_AVAILABILITY_TEMPLATE.md
    ├── DATA_DICTIONARY.md
    ├── RELEASE_NOTES.md
    └── REPRODUCTION_VALIDATION.md
```

## Requirements

- Python 3.11.1 (the Windows 10 research environment used for the recorded results)
- `ifcopenshell==0.9.0.post1`, `rdflib==7.6.0`, `pyshacl==0.40.2`
- A local Python environment with the required IFCOpenShell/pySHACL dependencies.
- Windows includes Tkinter; optional GUI graphics features may additionally need
  `matplotlib`, `numpy`, and `networkx` if they are used interactively.

### Install (Windows, from the repository root)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Run the regression tests

```powershell
python tests/test_rule_regressions.py
```

### Run the IFC-level Rule 13 regression test

```powershell
python tests/test_rule13_ifc_mutations.py
python tests/test_mep_illustrative.py
```

This verifies that M061–M065 create, mutate, and remove the dedicated
`Pset_ComplianceLandingHandrail.HeightAboveLandingWalkingLevel` property,
and that the extracted RDF graph evaluates Rule 13 using that datum.

### Reproduce the synthetic mutation study

First run a small smoke test:

```powershell
python src/run_mutation_study.py --limit 2 --timing-repetitions 3 --out smoke.json
```

Then execute all 72 synthetic cases, with 30 timing repetitions per case:

```powershell
python src/run_mutation_study.py --limit 72 --timing-repetitions 30 --out reproduced_72.json
```

The corrected author-executed experiment of 2026-11-22 is deposited at
`data/results/mutation_results_72_repetitions30.json` and its CSV companion.
A separate baseline diagnostic of 2026-11-24 UTC is deposited at
`data/results/baseline_final_check.json`. Timing values are observations, not exact reproducibility targets.

### Optional baseline checks

```powershell
python src/run_ifc_baseline.py --models "data/fixtures/stair_mep_mutation_base.ifc" "data/fixtures/stair_mep_illustrative_pass.ifc" --out baseline_local.json
python src/benchmark_full_pipeline.py --models "data/fixtures/stair_mep_mutation_base.ifc" --repetitions 3 --out baseline_timing_local.json
```

For optional GUI visualization, install `matplotlib`, `numpy`, and `networkx` in the same environment, then run `python src/compliance_gui.py`.

## Interpretation and research limitations

- The 72 cases are controlled modifications derived from **one synthetic IFC4 fixture**.
  They do not represent 72 independently collected projects or independent code-ground-truth labels.
- The original synthetic source omits `IfcProject.UnitsInContext`; temporary mutation
  copies explicitly assign millimetres. The source fixture is not changed.
- The synthetic mutation timing samples measure **rule evaluation on prepared RDF graphs**;
  they exclude full IFC processing and initial RDF creation.
- The earlier 18-file implementation agreement suite and the large, externally supplied
  IFC file are not distributed in this minimal package and are not independently
  reproducible from its fixtures alone.
- The actual saved-IFC comparison in the manuscript was performed on a separate, nonpublic
  model and is **not** independently reproducible here. Do not describe this archive as
  providing end-to-end reproduction of that experiment.
- Rule 13 uses a dedicated `LandingHandrail` target and `hasLandingHandrailHeight` measurement, not generic railing height.
- Rules 16–18 are illustrative Python-based MEP checks outside the 14-rule semantic experiment. Visualizations are schematic, not to scale; they do not establish geometric clearance or regulatory compliance.
- The synthetic M050 GUI outcome disagreed with the other checking implementations;
  this extra outcome is retained in the results, not concealed.
- The regulatory applicability of the 14 implemented checks and the mixed physical-unit
  issue identified in the external model remain unverified.
- `stair_mep_illustrative_pass.ifc` is an **illustrative fixture** whose compliance
  status has not been independently certified; in the latest baseline diagnostic it fails core Rules 1, 2, 3, 5 and 11. Its filename is not a regulatory endorsement.

## Licensing and citation

Software and ontology: **MIT**. Original synthetic IFC fixtures, mutation definitions,
recorded results and documentation: **CC BY 4.0**. See `LICENSES.md`.

The original [v1.0.0 Zenodo record](https://zenodo.org/records/24406900) is retained for provenance; it does not contain this corrected version. Use `CITATION.cff` for source metadata. Cite v1.1.0: https://doi.org/10.5281/zenodo.24518101.

## Release status

- Corrected v1.1.0 is published on GitHub and Zenodo: https://doi.org/10.5281/zenodo.24518101.
- The corrected 72-case results were recorded on 2026-11-22 UTC. Subsequent baseline and MEP/Rule 13 regression checks were author-executed; the final 72-case outcome-only verification on 2026-11-24 UTC reproduced all non-timing case outcomes. See `data/results/final_verification_72.json`.
- The externally supplied model is excluded. Preserve existing licenses and fixture bytes.
- Verify the release file manifest, tag, archive metadata and version-specific DOI before manuscript submission.
