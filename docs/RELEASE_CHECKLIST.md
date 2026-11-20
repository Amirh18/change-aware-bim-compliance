# Release and archive checklist

**GitHub repository:** https://github.com/Amirh18/change-aware-bim-compliance
**Repository title:** Change-Aware BIM Compliance Checking
**Manuscript title:** Change-Aware BIM Compliance Checking: A Semantic Knowledge-Graph Framework for Pre-Commit Rule Re-evaluation
**Zenodo record title:** Change-Aware BIM Compliance Checking: Code and Synthetic IFC Reproducibility Package

- [ ] Review the renamed scripts and published filenames.
- [ ] Run `python tests/test_rule_regressions.py` in the target Windows Python environment.
- [ ] Run `python src/run_mutation_study.py --limit 2 --timing-repetitions 3 --out smoke.json`.
- [ ] Verify M001 and M002 outcomes against the recorded synthetic data.
- [ ] Approve the copyright holders and public release.
- [ ] Upload contents to the **private** GitHub repository, without nesting the ZIP filename as a folder.
- [ ] Verify the GitHub file tree against `README.md` and manifest.
- [ ] Make the repository public only after final approval and checks.
- [ ] Tag a release `v1.0.0`, enable Zenodo GitHub archiving, confirm an actual DOI.
- [ ] Update the manuscript Data Availability Statement with the real DOI.
- [ ] Ensure names, the paper title, and the DOI are consistent in GitHub/Zenodo/manuscript.
