# PUBLICATION READINESS (M14) — prepared, NOT published

No remote is configured and nothing has been pushed. This document lists what would be published and what must happen first. The checks are automated in `tests/test_publication_m14.py`.

## Checks (2026-09-27)
| Check | Result | Evidence |
|---|---|---|
| Proprietary data files tracked (xlsx/parquet/csv/pbix/abf/db/images…) | **none** | `test_no_data_or_binary_files_tracked` |
| Data files ever added in history | **none** | `test_no_data_file_ever_added_to_history` |
| Secrets (API keys, tokens, private keys) in tracked files or any commit | **none** | `test_no_secrets_in_tracked_files_or_history` |
| Large files (> 512 KB) | **none** (largest: `app/web/app.js`, ~140 KB) | `test_no_large_files_tracked` |
| `.env` tracked | **no**; `.env.example` has empty key placeholders only | `test_env_template_and_ignores` |
| User-profile or temp paths of this machine | **none** | `test_no_user_profile_paths_and_only_documented_drive_paths` |
| Local drive paths | none in documentation or configuration: placeholders (`<PRIVATE_WORKBOOK_PATH>`, `<PROJECT_ROOT>`, `<PRIVATE_DATA_ROOT>`) are used; only fictitious test strings and the Power BI Desktop install fallback name a drive (allow-list in the test) | same test |
| Screenshots | none tracked (`screenshots/*` ignored; QA captures live in git-ignored `.cache/` and a scratch folder outside the repository) | `.gitignore` |
| Real value aggregates in committed docs | none in the **current** tree (currency mentions are unit definitions and hand-made synthetic examples) | manual scan |
| **Real value aggregates in history** | **PRESENT:** commit `5319019` (M1–M3) contains real value shares and price percentiles in an earlier `DATA_DICTIONARY.md` | known since M11 |

## Portability (resolved without changing private behaviour)
- **Dataset:** `PCI_DATASET=synthetic` (M13). The public workflow needs no private file.
- **Private source location:** set `PCI_SOURCE_PATH` to the workbook location (placeholder default `<PRIVATE_WORKBOOK_PATH>`). The profiling scripts now derive paths from the project root and the schema.
- **Power BI:**
  - `python -m pci_powerbi.build --data-root <folder> --out <folder>` writes any machine's data folder into the Power Query parameters.
  - With `PCI_DATASET=synthetic` and no options, it builds `dashboards/_synthetic/` (git-ignored).
  - The committed `dashboards/` project keeps the documented private default path. It can never be overwritten by a synthetic or foreign-root build (tested).
- **Tests on any machine:** `python scripts/run_tests_by_file.py` runs every file in its own process. The private-data suites skip where the private layer is absent; the synthetic suites need only `python -m pci_synthetic.generate`.

## FILES TO PUBLISH
Everything tracked (`git ls-files`, ~310 files), which covers:
- source code (`python/`, `sql/`, `app/web/`, `scripts/`, `run_app.py`), tests (`tests/`) and docs (`*.md`, `docs/`);
- the generated Power BI project text (`dashboards/PCI_Commercial_Intelligence.*`);
- `data/synthetic/SYNTHETIC_FINGERPRINT.json` and `data/profile/DATA_QUALITY_BASELINE.md`, which is value-free;
- the value-free evaluation and reconciliation summaries.

## FILES TO EXCLUDE (all git-ignored today)
- the licensed workbook (outside the repo);
- `data/processed/` (private Parquet and manifest);
- `data/profile/*.json|csv`;
- `data/synthetic/*.parquet` (regenerated);
- `data/processed/powerbi/`, `evaluation/reports/*.json|csv`, `.cache/`, `logs/`, `screenshots/`;
- `dashboards/_synthetic/`, `*.pbix`, `*.pbit`, `*.abf`, `**/.pbi/`;
- `.env`, `.venv/`.

## Required before any push (decisions for the owner)
1. **History:** do **not** push the existing history, because commit `5319019` would publish real aggregates. The non-destructive route publishes a fresh, history-free snapshot of the current tree from an orphan branch. Local history is untouched and no history is rewritten.
   ```
   git checkout --orphan public-release
   git add -A && git commit -m "Public release: pharma commercial intelligence (synthetic data)"
   # review, then add a remote and push ONLY this branch (after explicit approval)
   git checkout main
   ```
   The alternative is to rewrite history with `git filter-repo`. That is destructive and requires explicit approval; it is not recommended.
2. **Licence:** MIT (`LICENSE`), chosen by the owner.
3. **Public dataset:** the public workflow uses the synthetic dataset. Confirm the private-data documentation (schema, counts, methodology statistics) may be public; it contains no values.
