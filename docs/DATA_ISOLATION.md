# Data isolation: public mode and private IMS mode

> **PRIVATE IMS DATA MUST NEVER BE COMMITTED, PUSHED, UPLOADED, SCREENSHOTTED OR SENT TO ANY EXTERNAL SERVICE.**

The project runs in exactly two modes. The mode is chosen **only** by `PCI_DATASET`; nothing is discovered.

| | Public mode (default) | Private local mode |
|---|---|---|
| Selection | `PCI_DATASET` unset, empty or `synthetic` | `PCI_DATASET=ims` **and** `PCI_IMS_DATA_DIR=<PRIVATE_EXTERNAL_DIRECTORY>` |
| Data | `data/synthetic/` — fictional, structure-only, generated locally (`python -m pci_synthetic.generate`) | licensed IMS processed layer in `PCI_IMS_DATA_DIR` |
| Who | anyone who clones the repository | the data owner, on their own machine |
| Needs private files? | no | yes (outside the repository) |
| Generated outputs | repository paths that are git-ignored (`data/synthetic/`, `dashboards/_synthetic/`, `evaluation/reports/*_synthetic*`, `.cache/`) | subfolders of `PCI_IMS_DATA_DIR` only: `powerbi/`, `reports/`, `profile/`, `cache/` |
| External language model | optional Gemini router (explicit opt-in, key from the environment) | refused |

Any other `PCI_DATASET` value (including the retired `private`) stops the application with a configuration error.
There is **no fallback**: an unusable IMS configuration never turns into synthetic data, and synthetic mode never
reads an IMS folder even when `PCI_IMS_DATA_DIR` happens to be set.

## Rules for `PCI_IMS_DATA_DIR` (checked at start-up, `python/pci_data/dataset.py`)
The directory must:
1. be an **absolute** path;
2. **exist** and be a **directory**;
3. be **outside the git repository** — not the repository, not inside it, and not reached through a symbolic link or
   junction that points into it (both the path as written and the fully resolved path are checked);
4. **not be a parent** of the repository;
5. **not be a drive or filesystem root**;
6. **not be inside any git work tree** (no `.git` in the directory or its ancestors), so git can never track it.

Error messages never echo the configured path. The folder must contain the processed layer built by
`pci_data.build_processed` (`pack`, `fact_pack_month`, `pack_snapshot`, `pack_price_month` Parquet + `_manifest.json`).
At start-up the manifest is checked against the selected mode: a synthetic manifest in the IMS folder, or a
non-synthetic manifest in `data/synthetic/`, is refused.

## What the code never does
- search drives (C:, D:, E:, …), parent folders, user profile folders, Power BI folders or other databases;
- read any environment variable other than `PCI_DATASET`, `PCI_IMS_DATA_DIR`, `PCI_SOURCE_PATH` (only for the
  IMS-only workbook build/profiling), `LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`;
- write an IMS-derived file inside the repository (`pci_data.schema.output_dir()` / `assert_outside_repo()` refuse);
- expose a file or directory through the local server, the tools or the agents (no tool accepts a path, SQL or code).

Regression tests: `tests/test_isolation_p1.py` (selection, fail-closed cases, link into the repository, no discovery
under an audit hook with decoy private locations configured, output locations, server hardening, result caps, agent
boundary, Gemini refusal) and `tests/test_publication_guard.py`.

## Private workflow (owner only)
```bat
set PCI_DATASET=ims
set PCI_IMS_DATA_DIR=<PRIVATE_EXTERNAL_DIRECTORY>
rem optional, only to rebuild the processed layer from the licensed workbook (also outside the repository):
set PCI_SOURCE_PATH=<PRIVATE_WORKBOOK_PATH>
cd python && ..\.venv\Scripts\python.exe -m pci_data.build_processed && cd ..
.venv\Scripts\python.exe run_app.py                  rem http://127.0.0.1:8765
.venv\Scripts\python.exe scripts\run_tests_by_file.py  rem full suite incl. `ims` tests; logs go to <dir>\cache
```
Keep the session variables local to one terminal. Stop the server when finished. Before committing: enable the
publication guard (`git config core.hooksPath .githooks`), never `git add -f`, review `git status`.

## Tests in the two modes
Tests that assert facts of the licensed dataset (row counts, real labels, source reconciliation, SQL/Python parity on
the real layer, the committed Power BI project drift test) carry the `ims` marker (module `pytestmark` or per test) and
are skipped in public mode by `tests/conftest.py`. Everything else — synthetic, isolation, security, UI contracts,
agent boundary, publication — runs in both modes. The full private suite therefore runs with
`PCI_DATASET=ims` + `PCI_IMS_DATA_DIR`, and the public suite with `PCI_DATASET` unset.
