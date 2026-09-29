"""Publication guard (scripts/publication_guard.py) — fail-closed checks for commits and pushes.

Every scenario runs in a throw-away git repository built from fictional content. The guard must block data files,
private locations, secrets, drive paths, configured private identifiers (never hard-coded), large or binary files
and foreign history roots, and it must never print what it matched.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "publication_guard.py"
ZERO = "0" * 40


def run(cwd, *args, env=None, stdin=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=env, input=stdin)


def git(repo, *args):
    r = run(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null",
            "-c", "commit.gpgsign=false", *args)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def guard(repo, *args, stdin=None, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PCI_SOURCE_PATH", "PCI_IMS_DATA_DIR")}
    env.update(extra_env or {})
    env["PCI_GUARD_REPO"] = str(repo)
    return run(repo, sys.executable, str(GUARD), *args, env=env, stdin=stdin)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("# fictional project\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "root")
    root = git(r, "rev-parse", "HEAD")
    (r / ".githooks").mkdir()
    (r / ".githooks" / "allowed-roots").write_text(f"# test\n{root}\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "roots")
    return r


def commit(repo, files: dict, msg="change"):
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        (p.write_bytes if isinstance(content, bytes) else lambda c: p.write_text(c, encoding="utf-8"))(content)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def test_clean_public_change_passes(repo):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, {"docs/guide.md": "Set PCI_IMS_DATA_DIR=<PRIVATE_EXTERNAL_DIRECTORY> locally.\n"})
    r = guard(repo, "range", base, tip)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stderr


PROHIBITED = [
    ("data/processed/pack.parquet", b"PAR1\x00\x01", "prohibited file type"),
    ("exports/brands.csv", "a,b\n1,2\n", "prohibited file type"),
    ("exports/rows.tsv", "a\tb\n", "prohibited file type"),
    ("exports/rows.jsonl", '{"a": 1}\n', "prohibited file type"),
    ("notebooks/explore.ipynb", '{"cells": []}', "prohibited file type"),
    ("docs/report.pdf", b"%PDF-1.4\x00", "prohibited file type"),
    ("docs/shot.png", b"\x89PNG\x00", "prohibited file type"),
    ("backup/data.zip", b"PK\x03\x04\x00", "prohibited file type"),
    ("data/ims/notes.md", "private notes\n", "private/generated location"),
    ("evaluation/reports/private_results.json", "{}", "private/generated location"),
    (".cache/powerbi/names.txt", "x\n", "private/generated location"),
    ("dashboards/x.SemanticModel/.pbi/cache.abf", b"\x00abf", "Power BI Desktop cache"),
    (".env", "GEMINI_API_KEY=\n", "environment/credential file"),
    ("config/gemini_api_key.txt", "placeholder\n", "credential-like file name"),
    ("big.md", "x" * (600 * 1024), "large file"),
    ("blob.dat", b"\x00\x01\x02", "binary content"),
]


@pytest.mark.parametrize("name,content,reason", PROHIBITED, ids=[p[0] for p in PROHIBITED])
def test_prohibited_files_are_blocked(repo, name, content, reason):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, {name: content})
    r = guard(repo, "range", base, tip)
    assert r.returncode == 1 and name in r.stderr and reason in r.stderr, r.stderr


SENSITIVE = [
    ("key = " + "AIza" + "Sy" + "A" * 33 + "\n", "secret-like token"),
    ("token: '" + "ghp_" + "b" * 30 + "'\n", "secret-like token"),
    ("-----BEGIN " + "PRIVATE KEY-----\n", "secret-like token"),
    ("data lives in " + "Q:" + "\\somewhere\\x\n", "absolute drive path"),
    ("see C:" + "\\Users\\someone\\Desktop\\x\n", "user-profile"),
]


@pytest.mark.parametrize("content,reason", SENSITIVE, ids=[f"sensitive{i}" for i in range(len(SENSITIVE))])
def test_sensitive_content_is_blocked_without_printing_it(repo, content, reason):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, {"docs/notes.md": content})
    r = guard(repo, "range", base, tip)
    assert r.returncode == 1 and reason in r.stderr, r.stderr
    secret_part = content.strip().split()[-1]
    assert secret_part not in r.stderr and secret_part not in r.stdout


def test_configured_private_identifiers_blocked_and_not_printed(repo, tmp_path):
    private = tmp_path / "SOMEWHERE_PRIVATE_4821"
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, {"docs/a.md": "workbook quarterly_audit_zz9 export\n",
                        "docs/b.md": f"data folder {private.as_posix()}\n"})
    r = guard(repo, "range", base, tip, extra_env={"PCI_SOURCE_PATH": str(tmp_path / "quarterly_audit_zz9.xlsx"),
                                                   "PCI_IMS_DATA_DIR": str(private), "PCI_GUARD_DEEP": "0"})
    assert r.returncode == 1
    assert "docs/a.md: names the configured private source workbook" in r.stderr
    assert "docs/b.md: contains the configured private IMS data directory" in r.stderr
    assert "quarterly_audit_zz9" not in r.stderr and "SOMEWHERE_PRIVATE_4821" not in r.stderr


def test_configured_source_stem_matches_case_and_whitespace_variants(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, {"docs/c.md": "loaded from NORTHWIND   Ledger\tQ3 last week\n"})
    r = guard(repo, "range", base, tip, extra_env={"PCI_SOURCE_PATH": str(tmp_path / "Northwind Ledger Q3.xlsx"),
                                                   "PCI_GUARD_DEEP": "0"})
    assert r.returncode == 1 and "docs/c.md: names the configured private source workbook" in r.stderr, r.stderr
    assert "northwind" not in r.stderr.lower()


def test_local_identifier_list_blocks_and_is_not_printed(repo):
    """Owner identifiers live in <git-dir>/info (never tracked, never pushed), so they are active in every hook run."""
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "pci-private-identifiers").write_text("# fictional\nZephyr Tally 88\n", encoding="utf-8")
    base = git(repo, "rev-parse", "HEAD")
    tip = commit(repo, {"docs/d.md": "export from zephyr  tally 88\n"})
    r = guard(repo, "range", base, tip)
    assert r.returncode == 1 and "docs/d.md: names a configured private identifier" in r.stderr, r.stderr
    assert "zephyr" not in r.stderr.lower() and "tally" not in r.stderr.lower()
    assert git(repo, "status", "--porcelain", "--ignored") == ""      # the list is not a working-tree file
    (repo / ".git" / "info" / "pci-private-identifiers").write_text("abc\n", encoding="utf-8")
    r = guard(repo, "range", base, tip)
    assert r.returncode == 2 and "could not run" in r.stderr         # too-short identifier: fail closed


def test_file_added_then_deleted_in_pushed_history_is_blocked(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"leak.parquet": b"PAR1\x00"})
    git(repo, "rm", "-q", "leak.parquet")
    git(repo, "commit", "-q", "-m", "remove")
    tip = git(repo, "rev-parse", "HEAD")
    r = guard(repo, "range", base, tip)
    assert r.returncode == 1 and "leak.parquet" in r.stderr, r.stderr


def test_foreign_history_root_is_blocked_on_push(repo):
    git(repo, "checkout", "-q", "--orphan", "private")
    commit(repo, {"README.md": "# other history\n"}, msg="private root")
    sha = git(repo, "rev-parse", "HEAD")
    r = guard(repo, "pre-push", "origin", "url", stdin=f"refs/heads/private {sha} refs/heads/main {ZERO}\n")
    assert r.returncode == 1 and "root commit(s) outside" in r.stderr, r.stderr


def test_push_of_allowed_history_passes_and_deletions_are_ignored(repo):
    sha = git(repo, "rev-parse", "HEAD")
    r = guard(repo, "pre-push", "origin", "url", stdin=f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
    assert r.returncode == 0, r.stderr
    r = guard(repo, "pre-push", "origin", "url", stdin=f"(delete) {ZERO} refs/heads/old {sha}\n")
    assert r.returncode == 0, r.stderr


def test_missing_roots_file_fails_closed(repo):
    (repo / ".githooks" / "allowed-roots").unlink()
    sha = git(repo, "rev-parse", "HEAD")
    r = guard(repo, "pre-push", "origin", "url", stdin=f"refs/heads/main {sha} refs/heads/main {ZERO}\n")
    assert r.returncode == 2 and "could not run" in r.stderr


def test_staged_mode_blocks_forced_add(repo):
    (repo / "data").mkdir()
    (repo / "data" / "pack.parquet").write_bytes(b"PAR1\x00")
    git(repo, "add", "-f", "data/pack.parquet")
    r = guard(repo, "staged")
    assert r.returncode == 1 and "data/pack.parquet" in r.stderr


def test_this_repository_head_passes_the_guard():
    r = subprocess.run([sys.executable, str(GUARD), "tree", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PCI_GUARD_REPO": str(ROOT), "PCI_GUARD_DEEP": "0"})
    assert r.returncode == 0, r.stderr


def test_hooks_and_roots_file_are_present():
    for name in ("pre-push", "pre-commit"):
        text = (ROOT / ".githooks" / name).read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh") and "publication_guard.py" in text
    roots = (ROOT / ".githooks" / "allowed-roots").read_text(encoding="utf-8")
    assert any(len(ln.strip()) == 40 for ln in roots.splitlines() if not ln.startswith("#"))
