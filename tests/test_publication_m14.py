"""M14 — publication safety and portability (git-tracked content, history, configuration).

Permanent guard: fails if data files, secrets, oversized files, .env, user-profile paths or undocumented machine
paths are ever tracked; checks the portable Power BI / source-path configuration.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


TRACKED = [f for f in git("ls-files").splitlines() if f]
SECRET = re.compile(r"AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|xox[bp]-[A-Za-z0-9-]{10,}"
                    r"|ghp_[A-Za-z0-9]{20,}|(api[_-]?key|secret|password)\s*[:=]\s*['\"][A-Za-z0-9/+_-]{12,}['\"]", re.I)
DATA_EXT = re.compile(r"\.(parquet|csv|xlsx?|xlsm|xlsb|pbix|pbit|abf|duckdb|db|sqlite3?|pkl|pickle|feather|arrow|zip|7z|"
                      r"png|jpe?g|gif|bmp|pdf)$", re.I)
# files allowed to name a local drive path, and why. Documentation and configuration use placeholders
# (<PRIVATE_WORKBOOK_PATH>, <PROJECT_ROOT>, <PRIVATE_DATA_ROOT>); no real machine path is published.
DRIVE_PATH_ALLOWED = {
    "python/pci_powerbi/desktop.py", "python/pci_powerbi/as_bridge.ps1",   # Power BI Desktop install fallback
    "python/pci_agents/evaluation.py", "tests/test_agents.py", "tests/test_app.py",      # fictitious attack/scrub strings
    "tests/test_publication_m14.py",
    "tests/test_isolation_p1.py", "tests/test_publication_guard.py",   # P1: fictitious attack / fail-closed inputs
}


def test_no_data_or_binary_files_tracked():
    assert [f for f in TRACKED if DATA_EXT.search(f)] == []
    for d in ("data/processed/", "data/raw/", "data/profile/", "data/synthetic/", "evaluation/reports/", ".cache/", "logs/"):
        allowed = {"data/profile/DATA_QUALITY_BASELINE.md", "data/synthetic/SYNTHETIC_FINGERPRINT.json",
                   "evaluation/reports/AGENT_EVAL_REPORT.md", "evaluation/reports/POWER_BI_RECONCILIATION.md",
                   "evaluation/reports/POWER_BI_RECONCILIATION_SYNTHETIC.md",
                   "evaluation/reports/METRIC_TRACES_SYNTHETIC.json"}
        extra = [f for f in TRACKED if f.startswith(d) and f not in allowed and not f.endswith(".gitkeep")]
        assert extra == [], (d, extra)


def test_no_large_files_tracked():
    big = [f for f in TRACKED if (ROOT / f).exists() and (ROOT / f).stat().st_size > 512 * 1024]
    assert big == []


def test_no_secrets_in_tracked_files_or_history():
    hits = []
    for f in TRACKED:
        p = ROOT / f
        if p.exists() and p.stat().st_size < 2_000_000:
            try:
                if SECRET.search(p.read_text(encoding="utf-8")):
                    hits.append(f)
            except UnicodeDecodeError:
                pass
    assert hits == []
    assert not SECRET.search(git("log", "--all", "-p", "--no-color"))


def test_no_data_file_ever_added_to_history():
    added = [f for f in git("log", "--all", "--diff-filter=A", "--name-only", "--format=").splitlines() if f]
    assert [f for f in added if DATA_EXT.search(f)] == []


def test_env_template_and_ignores():
    assert ".env" not in TRACKED and ".env.example" in TRACKED
    tmpl = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in tmpl.splitlines():
        if re.match(r"\s*#?\s*GEMINI_API_KEY\s*=", line):
            assert line.split("=", 1)[1].strip() == "", "the template must not carry a key"
    ign = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for rule in (".env", "data/processed/*", "data/raw/*", "*.parquet", "*.xlsx", "*.csv", "*.pbix", "*.abf",
                 "**/.pbi/", "dashboards/_synthetic/", ".cache/", "screenshots/*", "evaluation/reports/*"):
        assert rule in ign, rule


# this machine's user profile (home folder, 8.3 short names, assistant temp folders) must never be tracked
PROFILE = re.compile(r"Users[\\/]+" + re.escape(Path.home().name) + r"\b|[A-Z]{6}~\d|AppData[\\/]+Local[\\/]+Temp", re.I)


def test_no_user_profile_paths_and_only_documented_drive_paths():
    offenders, profile = [], []
    for f in TRACKED:
        p = ROOT / f
        if not p.exists() or DATA_EXT.search(f):
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PROFILE.search(t) and f != "tests/test_publication_m14.py":
            profile.append(f)
        if re.search(r"\b[A-Za-z]:\\", t) and f not in DRIVE_PATH_ALLOWED:
            offenders.append(f)
    assert profile == [] and offenders == [], (profile, offenders)


def test_private_workbook_identity_not_published():
    """The default source location is a placeholder, and no tracked file names the real workbook. The real name is
    read from PCI_SOURCE_PATH on the owner's machine only (never written anywhere)."""
    schema = (ROOT / "python" / "pci_data" / "schema.py").read_text(encoding="utf-8")
    assert '"<PRIVATE_WORKBOOK_PATH>"' in schema
    expr = (ROOT / "dashboards" / "PCI_Commercial_Intelligence.SemanticModel" / "definition" / "expressions.tmdl")
    assert '"<PRIVATE_DATA_ROOT>"' in expr.read_text(encoding="utf-8")
    src = os.environ.get("PCI_SOURCE_PATH")
    stem = Path(src).stem.lower() if src else None
    if stem and len(stem) >= 4:
        hits = [f for f in TRACKED if not DATA_EXT.search(f) and (ROOT / f).exists()
                and stem in (ROOT / f).read_text(encoding="utf-8", errors="ignore").lower()]
        assert hits == [], hits


def test_portable_source_path_override():
    env = {**os.environ, "PCI_SOURCE_PATH": "/tmp/elsewhere/source.xlsx"}
    out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'python'); "
                                                "from pci_data.schema import SOURCE_PATH; print(SOURCE_PATH.name)"],
                         cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip()
    assert out == "source.xlsx"


def test_powerbi_build_data_root_is_portable(tmp_path):
    root = tmp_path / "anywhere" / "synthetic"
    env = {k: v for k, v in os.environ.items() if k != "PCI_DATASET"}
    env["PCI_DATASET"] = "synthetic"
    if not (ROOT / "data" / "synthetic" / "pack.parquet").exists():
        subprocess.run([sys.executable, "-m", "pci_synthetic.generate"], cwd=ROOT / "python", env=env, check=True)
    r = subprocess.run([sys.executable, "-m", "pci_powerbi.build", "--data-root", str(root), "--out", str(tmp_path / "pbip")],
                       cwd=ROOT / "python", env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    expr = next((tmp_path / "pbip").rglob("expressions.tmdl")).read_text(encoding="utf-8")
    assert str(root) in expr and "Pharma_Commercial_Intelligence\\data" not in expr
    bad = subprocess.run([sys.executable, "-m", "pci_powerbi.build", "--data-root", str(root)], cwd=ROOT / "python",
                         env=env, capture_output=True, text=True, timeout=300)
    assert bad.returncode != 0 and "--out" in bad.stderr                       # never over the committed project
