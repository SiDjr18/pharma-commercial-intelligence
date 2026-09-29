"""Publication guard: blocks commits/pushes that would publish private data, private paths or secrets.

Runs from the git hooks in .githooks/ (enable once per clone:  git config core.hooksPath .githooks) or by hand:

    python scripts/publication_guard.py staged              # what `git commit` would record (pre-commit hook)
    python scripts/publication_guard.py tree [REV]          # every file of REV (default HEAD)
    python scripts/publication_guard.py range BASE TIP      # every blob added/changed in BASE..TIP + TIP's tree
    python scripts/publication_guard.py pre-push REMOTE URL # reads git's pre-push refs from stdin (pre-push hook)

Fail closed: exit 1 when anything suspicious is found, exit 2 when the guard itself cannot run (both block).
It reports PATH and REASON only: it never prints file contents, matched values, secrets or configured private paths.
Standard library only; nothing is sent anywhere. See docs/PUBLICATION_GUARD.md.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ZERO = "0" * 40
MAX_BYTES = 512 * 1024                         # same limit as tests/test_publication_m14.py
ROOTS_FILE = ".githooks/allowed-roots"         # history roots that may be published (the public snapshot)

# ---- file types that never belong in the public repository (data, binaries, notebooks, archives, images)
PROHIBITED_EXT = {
    "parquet", "csv", "tsv", "jsonl", "ndjson", "xlsx", "xls", "xlsm", "xlsb", "ods", "duckdb", "wal", "db", "sqlite",
    "sqlite3", "pkl", "pickle", "feather", "arrow", "h5", "hdf5", "npy", "npz", "zip", "7z", "rar", "tar", "gz", "tgz",
    "bz2", "xz", "pbix", "pbit", "abf", "ipynb", "pdf", "bak", "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif",
    "tiff", "log",
}
# ---- locations that hold private or generated material (tracked exceptions listed below)
PROHIBITED_PREFIX = ("data/processed/", "data/raw/", "data/profile/", "data/synthetic/", "data/ims/", "data/private/",
                     "evaluation/reports/", "logs/", ".cache/", "screenshots/", "dashboards/_synthetic/", ".venv/",
                     "venv/", "demo/")
ALLOWED_IN_PREFIX = {
    "data/profile/DATA_QUALITY_BASELINE.md", "data/synthetic/SYNTHETIC_FINGERPRINT.json",
    "evaluation/reports/AGENT_EVAL_REPORT.md", "evaluation/reports/POWER_BI_RECONCILIATION.md",
    "evaluation/reports/POWER_BI_RECONCILIATION_SYNTHETIC.md", "evaluation/reports/METRIC_TRACES_SYNTHETIC.json",
}
SECRET = re.compile(
    r"AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY|xox[bp]-[A-Za-z0-9-]{10,}"
    r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    r"|(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9/+_.-]{12,}['\"]", re.I)
DRIVE_PATH = re.compile(r"\b[A-Za-z]:\\")
# files allowed to name a drive path (Power BI install fallback; fictitious attack / scrub strings) — mirrors
# DRIVE_PATH_ALLOWED in tests/test_publication_m14.py, plus the guard's own tests
DRIVE_PATH_ALLOWED = {
    "python/pci_powerbi/desktop.py", "python/pci_powerbi/as_bridge.ps1", "python/pci_agents/evaluation.py",
    "tests/test_agents.py", "tests/test_app.py", "tests/test_publication_m14.py", "tests/test_isolation_p1.py",
    "tests/test_publication_guard.py",
}
PROFILE = re.compile(r"[\\/]Users[\\/]+(?!Public\b|Default\b|<)[^\\/\s\"'<>]{2,}[\\/]|[A-Z]{6}~\d|AppData[\\/]+Local[\\/]+Temp",
                     re.I)
PROFILE_ALLOWED = {"tests/test_publication_m14.py", "tests/test_publication_guard.py", "tests/test_app.py",
                   "scripts/publication_guard.py"}
SELF_FILES = {"scripts/publication_guard.py"}
# Private source identifiers are never hard-coded here (that would publish them). They come from this machine only:
# the PCI_SOURCE_PATH file stem, PCI_IMS_DATA_DIR and an optional per-clone list inside the git directory (git never
# tracks or pushes it): one identifier per line, '#' comments. Matching ignores case and runs of whitespace.
LOCAL_IDENTIFIERS = "info/pci-private-identifiers"
MIN_MARKER = 4


class GuardError(Exception):
    pass


def _git(repo: Path, *args, data: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True)
    if r.returncode != 0:
        raise GuardError(f"git {args[0]} failed")
    return r.stdout


def _norm(text: str) -> str:
    """Case- and whitespace-insensitive form used for identifier matching."""
    return " ".join(text.lower().split())


def _local_identifiers(repo: Path) -> list[str]:
    """Owner-maintained identifiers in <git common dir>/info/pci-private-identifiers (inside .git: never tracked)."""
    git_dir = Path(_git(repo, "rev-parse", "--git-common-dir").decode().strip())
    f = (git_dir if git_dir.is_absolute() else Path(repo) / git_dir) / LOCAL_IDENTIFIERS
    if not f.is_file():
        return []
    raw = f.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "replace")
    idents = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if any(len(x) < MIN_MARKER for x in idents):          # fail closed rather than silently ignore an entry
        raise GuardError(f"{LOCAL_IDENTIFIERS}: every identifier needs at least {MIN_MARKER} characters")
    return idents


def _private_markers(repo: Path) -> list[tuple[str, str]]:
    """Configured private identifiers (from THIS machine only; never printed)."""
    out = []
    src = os.environ.get("PCI_SOURCE_PATH", "").strip().strip('"')
    if src:
        stem = PurePosixPath(src.replace("\\", "/")).stem
        if len(stem) >= MIN_MARKER:
            out.append((_norm(stem), "names the configured private source workbook (PCI_SOURCE_PATH)"))
    ims = os.environ.get("PCI_IMS_DATA_DIR", "").strip().strip('"').rstrip("\\/")
    if len(ims) >= MIN_MARKER:
        for v in {ims, ims.replace("\\", "/"), ims.replace("\\", "\\\\")}:
            out.append((_norm(v), "contains the configured private IMS data directory (PCI_IMS_DATA_DIR)"))
    for ident in _local_identifiers(repo):
        out.append((_norm(ident), f"names a configured private identifier (local list <git-dir>/{LOCAL_IDENTIFIERS})"))
    return out


def _real_labels() -> set[str]:
    """Optional deep check: multi-word real company/brand labels from the configured IMS layer (local only)."""
    ims = os.environ.get("PCI_IMS_DATA_DIR", "").strip().strip('"')
    if not ims or os.environ.get("PCI_GUARD_DEEP", "1") == "0":
        return set()
    pack = Path(ims) / "pack.parquet"
    try:
        import duckdb
    except ImportError:
        return set()
    if not pack.is_file():
        return set()
    con = duckdb.connect()
    try:
        rows = con.execute("SELECT DISTINCT company FROM read_parquet(?) UNION SELECT DISTINCT brand FROM read_parquet(?)",
                           [str(pack), str(pack)]).fetchall()
    finally:
        con.close()
    return {" ".join(r[0].split()) for r in rows if isinstance(r[0], str) and len(r[0]) >= 8 and " " in r[0].strip()}


class Guard:
    def __init__(self, repo: Path):
        self.repo = repo
        self.findings: list[tuple[str, str]] = []
        self.markers = _private_markers(repo)
        self.labels = _real_labels()
        self.max_words = max((len(x.split()) for x in self.labels), default=0)
        self._seen: set[tuple[str, str]] = set()

    def add(self, path: str, reason: str):
        if (path, reason) not in self._seen:
            self._seen.add((path, reason))
            self.findings.append((path, reason))

    # ------------------------------------------------------------------ path rules
    def check_path(self, path: str):
        p = path.replace("\\", "/")
        name = p.rsplit("/", 1)[-1]
        low = p.lower()
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if name.endswith(".gitkeep"):
            return
        if ext in PROHIBITED_EXT or low.endswith(".duckdb.wal"):
            self.add(p, f"prohibited file type (.{ext}): data, binary, notebook, archive, image or log")
        if any(low.startswith(x.lower()) for x in PROHIBITED_PREFIX) and p not in ALLOWED_IN_PREFIX:
            self.add(p, "private/generated location (data, reports, caches, logs, screenshots)")
        if "/.pbi/" in f"/{low}" or name.lower() in ("cache.abf", "localsettings.json"):
            self.add(p, "Power BI Desktop cache/local settings (may embed imported data)")
        if (name.lower().startswith(".env") and name != ".env.example") or name.lower() in ("secrets.json", "credentials.json"):
            self.add(p, "environment/credential file")
        if re.search(r"(^|/)[^/]*(secret|credential|api[_-]?key|token)[^/]*\.(json|txt|ya?ml|ini|cfg|key)$", low):
            self.add(p, "credential-like file name")
        if "__pycache__/" in low or ext in ("pyc", "pyo"):
            self.add(p, "compiled cache file")

    # ------------------------------------------------------------------ content rules
    def check_blob(self, path: str, data: bytes):
        p = path.replace("\\", "/")
        if len(data) > MAX_BYTES:
            self.add(p, f"large file ({len(data) // 1024} KB > {MAX_BYTES // 1024} KB)")
        if b"\0" in data[:8000]:
            self.add(p, "binary content")
            return
        text = data.decode("utf-8", "replace")
        if p in SELF_FILES:
            return
        if SECRET.search(text) and p not in ("tests/test_publication_m14.py", "tests/test_publication_guard.py"):
            self.add(p, "secret-like token (API key / private key / password pattern)")
        if DRIVE_PATH.search(text) and p not in DRIVE_PATH_ALLOWED:
            self.add(p, "absolute drive path (machine-specific or private location)")
        if PROFILE.search(text) and p not in PROFILE_ALLOWED:
            self.add(p, "user-profile / temp-folder path")
        low = _norm(text)
        for marker, reason in self.markers:
            if marker in low:
                self.add(p, reason)
        if self.labels:
            n = self._label_hits(text)
            if n:
                self.add(p, f"contains {n} real IMS entity label(s) (deep check against PCI_IMS_DATA_DIR)")

    def _label_hits(self, text: str) -> int:
        found = set()
        strip = ".,;:()[]{}\"'|`*<>#!?\\"
        for line in text.splitlines():
            toks = line.split()
            for i in range(len(toks)):
                for n in range(2, self.max_words + 1):
                    if i + n > len(toks):
                        break
                    k = " ".join(toks[i:i + n]).strip(strip)
                    if k in self.labels:
                        found.add(k)
        return len(found)

    # ------------------------------------------------------------------ object access
    def _blobs(self, entries: list[tuple[str, str]]):
        """entries: (path, blob sha). Reads all blobs in one git cat-file --batch call."""
        if not entries:
            return
        out = _git(self.repo, "cat-file", "--batch", data="\n".join(s for _, s in entries).encode() + b"\n")
        pos = 0
        for path, _ in entries:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].split()
            if len(header) < 3 or header[1] != b"blob":
                pos = nl + 1
                continue
            size = int(header[2])
            self.check_blob(path, out[nl + 1:nl + 1 + size])
            pos = nl + 1 + size + 1

    def tree(self, rev: str):
        entries = []
        for line in _git(self.repo, "ls-tree", "-r", "-z", rev).split(b"\0"):
            if not line:
                continue
            meta, path = line.split(b"\t", 1)
            mode, typ, sha = meta.split()
            if typ != b"blob":
                continue
            path = path.decode("utf-8", "replace")
            self.check_path(path)
            if mode == b"120000":
                self.add(path, "symbolic link (may point outside the repository)")
            entries.append((path, sha.decode()))
        self._blobs(entries)

    def commits(self, commits: list[str]):
        entries = []
        for c in commits:
            raw = _git(self.repo, "diff-tree", "-r", "-z", "--root", "--no-commit-id", "--diff-filter=ACMRT", c)
            parts = raw.split(b"\0")
            i = 0
            while i < len(parts) - 1:
                meta = parts[i].decode()
                if not meta.startswith(":"):
                    i += 1
                    continue
                fields = meta[1:].split()
                status = fields[4]
                if status[0] in "RC":                     # rename/copy: ":meta\0old\0new\0" -> check the new path
                    path = parts[i + 2].decode("utf-8", "replace")
                    i += 3
                else:
                    path = parts[i + 1].decode("utf-8", "replace")
                    i += 2
                self.check_path(path)
                if fields[1] == "120000":
                    self.add(path, "symbolic link (may point outside the repository)")
                entries.append((path, fields[3]))
        self._blobs(entries)

    def staged(self):
        raw = _git(self.repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT")
        entries = []
        for path in (x.decode("utf-8", "replace") for x in raw.split(b"\0") if x):
            self.check_path(path)
            ls = _git(self.repo, "ls-files", "-s", "-z", "--", path).split(b"\0")[0].split()
            if len(ls) >= 2:
                if ls[0] == b"120000":
                    self.add(path, "symbolic link (may point outside the repository)")
                entries.append((path, ls[1].decode()))
        self._blobs(entries)

    # ------------------------------------------------------------------ history rules
    def allowed_roots(self) -> set[str]:
        f = self.repo / ROOTS_FILE
        if not f.is_file():
            raise GuardError(f"{ROOTS_FILE} is missing: cannot tell the public history from private history")
        roots = {ln.split("#", 1)[0].strip().lower() for ln in f.read_text(encoding="utf-8").splitlines()}
        roots.discard("")
        if not roots or not all(re.fullmatch(r"[0-9a-f]{40}", r) for r in roots):
            raise GuardError(f"{ROOTS_FILE} must list full 40-character commit ids")
        return roots

    def check_roots(self, tip: str, label: str):
        roots = set(_git(self.repo, "rev-list", "--max-parents=0", tip).decode().split())
        bad = roots - self.allowed_roots()
        if bad:
            self.add(label, f"history has {len(bad)} root commit(s) outside {ROOTS_FILE} "
                            "(private or unrelated history must never be pushed)")

    def pre_push(self, remote: str, stdin_text: str):
        for line in stdin_text.splitlines():
            parts = line.split()
            if len(parts) != 4:
                continue
            local_ref, local_sha, remote_ref, remote_sha = parts
            if local_sha == ZERO:                        # branch deletion: nothing is published
                continue
            label = f"{local_ref} -> {remote_ref}"
            self.check_roots(local_sha, label)
            exclude = ["--not", f"--remotes={remote}"] + ([remote_sha] if remote_sha != ZERO and self._exists(remote_sha) else [])
            new = _git(self.repo, "rev-list", local_sha, *exclude).decode().split()
            self.commits(new)
            self.tree(local_sha)

    def _exists(self, sha: str) -> bool:
        return subprocess.run(["git", "-C", str(self.repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True).returncode == 0


def main(argv: list[str]) -> int:
    repo = Path(os.environ.get("PCI_GUARD_REPO") or _toplevel())
    if not argv:
        print(__doc__)
        return 2
    mode = argv[0]
    try:
        g = Guard(repo)                            # reads the configured identifiers; any failure blocks (exit 2)
        if mode == "staged":
            g.staged()
        elif mode == "tree":
            g.tree(argv[1] if len(argv) > 1 else "HEAD")
        elif mode == "range":
            base, tip = argv[1], argv[2]
            g.check_roots(tip, f"{base}..{tip}")
            g.commits(_git(repo, "rev-list", tip, "--not", base).decode().split())
            g.tree(tip)
        elif mode == "pre-push":
            g.pre_push(argv[1] if len(argv) > 1 else "origin", sys.stdin.read())
        else:
            print(f"publication guard: unknown mode {mode!r}", file=sys.stderr)
            return 2
    except GuardError as e:
        print(f"publication guard: BLOCKED - guard could not run: {e}", file=sys.stderr)
        return 2
    if g.findings:
        print(f"publication guard: BLOCKED - {len(g.findings)} finding(s) (path and reason only):", file=sys.stderr)
        for path, reason in g.findings:
            print(f"  {path}: {reason}", file=sys.stderr)
        print("Nothing was published. Remove or un-stage the files above (see docs/PUBLICATION_GUARD.md).",
              file=sys.stderr)
        return 1
    print(f"publication guard: OK ({mode})", file=sys.stderr)
    return 0


def _toplevel() -> str:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("publication guard: not inside a git repository")
    return r.stdout.strip()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
