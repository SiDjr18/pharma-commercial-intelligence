# SECURITY & DATA HANDLING

## Classification
> **PRIVATE IMS DATA MUST NEVER BE COMMITTED, PUSHED, UPLOADED, SCREENSHOTTED OR SENT TO ANY EXTERNAL SERVICE.**

- `<PRIVATE_WORKBOOK_PATH>` (the source workbook) — **PRIVATE / licensed**. Outside the repo; never copied into it.
- `<PRIVATE_EXTERNAL_DIRECTORY>` (`PCI_IMS_DATA_DIR`) — **PRIVATE derived**: the processed IMS layer and every IMS-derived
  output (Power BI exports, reports, profiles, caches, test logs). Must be outside the repository; the application
  refuses a folder inside it, a parent of it, a link into it or a folder inside any git work tree (P1, 2026-09-28).
- `data/processed/`, `data/profile/*.json|csv`, `logs/`, `.cache/` — legacy private locations; no longer written in IMS
  mode and git-ignored.
- `data/synthetic/` — fictional, structure-only (M13). Public-safe by construction and by the local leakage tests. Only the generator and `SYNTHETIC_FINGERPRINT.json` are committed; the Parquet data is regenerated, not committed.
- `PCI_DATASET` selects `synthetic` (**default**, public) or `ims` (explicit, with `PCI_IMS_DATA_DIR`). Any other value, or an invalid IMS directory, stops the application (fail closed; no fallback, no discovery). Demos, screenshots and any external/LLM use run on synthetic data only; Gemini refuses IMS mode.

## Rules
1. Never commit, upload, screenshot or paste raw IMS rows, or send them to any LLM/external API.
2. LLM calls receive only tool outputs (aggregates) and schema metadata. For the public demo, only synthetic data.
3. Secrets live in `.env` (git-ignored); commit only `.env.example` with placeholder names.
4. Before any push (M14): run a secret scan and a data-leak scan (no `.xlsx`, `.parquet`, `.duckdb`, profile JSON in the index); review `git ls-files`.
5. No GitHub remote until M14 and explicit user approval.
6. Screenshots (`screenshots/`) must be taken on synthetic data only.

## Publication (M14)
Automated checks are in `tests/test_publication_m14.py`; the report is `docs/PUBLICATION_READINESS.md`:
- no data files, secrets, large files or user-profile paths are tracked or anywhere in history;
- `.env.example` only.

**The git history contains real aggregates (commit 5319019):** publish a fresh orphan-branch snapshot, never the existing history.

## Open item
- Confirm licence terms for showing derived aggregates publicly. Until confirmed: **no real aggregates in public artefacts.**

## P1 isolation controls (2026-09-28)
- **Dataset selection** (`python/pci_data/dataset.py`): explicit paths only; nothing searches drives, parent folders,
  user folders, Power BI folders or other environment variables. Details: `docs/DATA_ISOLATION.md`.
- **Outputs**: in IMS mode, `pci_data.schema.output_dir()` routes every generated file under `PCI_IMS_DATA_DIR` and
  refuses repository paths; `build_processed` and the profiling scripts run only in IMS mode.
- **Local server** (`python/pci_app/server.py`): loopback bind only; `Host` must be this server's 127.0.0.1 /
  localhost / [::1] address (else 421, blocks DNS rebinding); POST needs `application/json` and a same-origin
  `Origin`/`Sec-Fetch-Site` when a browser sends them (else 415/403, blocks CSRF); no CORS headers.
- **Result caps** (`python/pci_app/tools.py`): `top_n` null/absent never means "all rows" — at most 2,000 rows for
  dimension lists and 500 for product-grain lists; `total_rows` still reports the full count.
- **Publication guard** (`.githooks/`, `scripts/publication_guard.py`): pre-commit and pre-push checks that fail closed
  on data files, private locations, secrets, drive/profile paths, configured private identifiers (never hard-coded
  in the repository), large/binary files and history not rooted at the public snapshot. It is a client-side hook,
  active once `core.hooksPath=.githooks` is set (`docs/PUBLICATION_GUARD.md`).
- **Tests**: `tests/test_isolation_p1.py`, `tests/test_publication_guard.py`; licensed-data tests carry the `ims` marker.
