# SECURITY & DATA HANDLING

## Classification
- `<PRIVATE_WORKBOOK_PATH>` (the source workbook) — **PRIVATE / licensed**. Outside the repo; never copied into it.
- `data/processed/`, `data/profile/*.json|csv`, `logs/`, `.cache/` — **PRIVATE derived**; git-ignored.
- `data/synthetic/` — fictional, structure-only (M13). Public-safe by construction and by the local leakage tests. Only the generator and `SYNTHETIC_FINGERPRINT.json` are committed; the Parquet data is regenerated, not committed.
- `PCI_DATASET` selects `private` (default) or `synthetic`. Demos, screenshots and any external/LLM use must run with `PCI_DATASET=synthetic`.

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
