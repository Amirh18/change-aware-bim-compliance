# Release and archive checklist

**GitHub repository:** https://github.com/Amirh18/change-aware-bim-compliance
**Repository title:** Change-Aware BIM Compliance Checking
**Manuscript title:** Change-Aware BIM Compliance Checking: A Semantic Knowledge-Graph Framework for Pre-Commit Rule Re-evaluation
**Zenodo record title:** Change-Aware BIM Compliance Checking: Code and Synthetic IFC Reproducibility Package

- [ ] Review the renamed scripts and published filenames.
- [x] Run `python tests/test_rule_regressions.py` in the target Windows Python environment.
- [x] Run `python src/run_mutation_study.py --limit 2 --timing-repetitions 3 --out smoke.json`.
- [x] Verify M001 and M002 outcomes against the recorded synthetic data.
- [x] Reproduce all 72 synthetic mutation cases (30 repetitions per method and case); see `REPRODUCTION_VALIDATION.md`.
- [ ] Approve the copyright holders and public release.
- [x] Upload contents to the **private** GitHub repository, without nesting the ZIP filename as a folder.
- [x] Verify the GitHub file tree against `README.md` and manifest.
- [x] Make the repository public only after final approval and checks.
- [x] Enable Zenodo GitHub archiving and provide the published v1.0.0 record URL: https://zenodo.org/records/24406900.
- [ ] Independently verify the version DOI, authors, and mixed licensing on the Zenodo record.
- [x] Update the manuscript Data Availability Statement with the Zenodo record URL.
- [ ] Replace the record URL with the confirmed version DOI if required by the journal.
- [ ] Ensure names, the paper title, and the DOI are consistent in GitHub/Zenodo/manuscript.
