# Publication guard

`scripts/publication_guard.py` (standard library only) blocks commits and pushes that would publish private data,
private paths or secrets. It **fails closed**: exit 1 when something suspicious is found, exit 2 when the guard itself
cannot run (for example the allowed-roots file is missing). Both block the commit/push. It prints **path and reason
only** — never file contents, matched values, secrets or configured private paths.

## Enable (once per clone)
```
git config core.hooksPath .githooks
```
- `.githooks/pre-commit` → `publication_guard.py staged` (checks exactly what would be committed, including files
  added with `git add -f`).
- `.githooks/pre-push` → `publication_guard.py pre-push <remote> <url>` (checks every pushed ref).
Both hooks run the first interpreter that actually works, in this order: `$PCI_GUARD_PYTHON`, the repository
`.venv`, `python3`, `python`, `py` (each is probed, so placeholder stubs such as the Windows Store alias are
skipped). With no working Python 3 they refuse the commit/push.

Manual checks: `python scripts/publication_guard.py tree HEAD`, `... range <base> <tip>`, `... staged`.

## Security model
The guard is a **client-side Git hook**. It runs on the machine that commits or pushes, and only in a clone whose
hooks configuration is enabled (`core.hooksPath=.githooks`, set once per clone). Under normal hook operation every
`git commit` and `git push` passes through it, so an ordinary push of private history or unsafe files is refused
before anything leaves the machine.

Like any client-side hook, it can technically be skipped: a clone where the setting was never enabled, or an explicit
`git commit --no-verify` / `git push --no-verify`. It is therefore a safety control that catches mistakes, not an
absolute guarantee. The other layers are:
- the licensed data lives outside the repository (`docs/DATA_ISOLATION.md`), so there is nothing private to add;
- `.gitignore` covers data formats and private folders;
- the owner reviews `git diff --cached` before committing, and only pushes with explicit approval.

Do not use `--no-verify` in this repository.

## What is checked
For a push: every blob added or changed by the commits that the remote does not have yet (so a file added and later
deleted inside the pushed range is still caught), plus the full tree of the pushed tip.

| Check | Blocks |
|---|---|
| History roots | any pushed history whose root commit is not listed in `.githooks/allowed-roots` (the public orphan snapshot). With the hook active, a push of a private development history is refused under any branch name (see *Security model*). |
| File types | data, binary, notebook, archive, image and log formats: parquet, csv, tsv, jsonl, xlsx/xls/xlsm/xlsb/ods, duckdb/db/sqlite, pkl, feather/arrow, h5, npy, zip/7z/rar/tar/gz, pbix/pbit/abf, ipynb, pdf, bak, png/jpg/gif/bmp/webp/tif, log |
| Locations | `data/processed/`, `data/raw/`, `data/profile/`, `data/synthetic/`, `data/ims/`, `data/private/`, `evaluation/reports/`, `logs/`, `.cache/`, `screenshots/`, `dashboards/_synthetic/`, `.venv/`, `demo/` (except the reviewed, tracked files listed in the script and `.gitkeep`) |
| Power BI caches | any `.pbi/` folder, `cache.abf`, `localSettings.json` |
| Credentials | `.env*` (except `.env.example`), `secrets.json`, `credentials.json`, credential-like file names |
| Size / binary | files over 512 KB; binary content |
| Secrets | Google API keys, `sk-…`, GitHub tokens, Slack tokens, AWS keys, private-key blocks, `api_key/secret/password/token = "…"` |
| Paths | absolute drive paths (drive letter, colon, backslash) outside the documented allow-list; user-profile / temp-folder paths |
| Configured private identifiers | the file stem of `PCI_SOURCE_PATH` and the path of `PCI_IMS_DATA_DIR`, when set in the environment of the person committing or pushing; plus every entry of the optional per-clone list `.git/info/pci-private-identifiers` (see below). Matching ignores case and runs of whitespace. |
| Deep check (optional) | when `PCI_IMS_DATA_DIR` is set and DuckDB is installed: multi-word real company/brand labels from the private layer found in pushed text (count only). Disable with `PCI_GUARD_DEEP=0`. |

## Private identifiers (never in the repository)
The guard contains no private names: hard-coding them would publish them. To keep the owner's identifiers (for
example the licensed source workbook's file name) checked in every commit and push, even when `PCI_SOURCE_PATH` is
not set, list them in `.git/info/pci-private-identifiers`: one identifier per line (at least 4 characters), `#` for
comments. That file is inside the git directory, so git never tracks, commits or pushes it. Each clone keeps its own
list. The guard reports only the file path and the reason, never the identifier. A list it cannot read, or an entry
that is too short, blocks the operation (exit 2).

## Normal public pushes
A push of `public-release` (or any branch rooted at the public snapshot) with ordinary source, documentation and test
changes passes. Branch deletions are not checked. Changing `.githooks/allowed-roots` is a deliberate publication
decision (for example after a future history rewrite) and must be reviewed.

## Limits
The guard complements review; it does not replace it. It cannot recognise every possible private value (for
example a single real number typed into a document), and it recognises private names only when they are configured
on the machine (see above). Keep private work in `PCI_IMS_DATA_DIR`, run the tests, and read `git diff --cached`.
