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
- [ ] Make the repository public only after final approval and checks.
- [ ] Tag a release `v1.0.0`, enable Zenodo GitHub archiving, confirm an actual DOI.
- [ ] Update the manuscript Data Availability Statement with the real DOI.
- [ ] Ensure names, the paper title, and the DOI are consistent in GitHub/Zenodo/manuscript.
