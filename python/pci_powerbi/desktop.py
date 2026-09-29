"""Drive the local Power BI Desktop instance for M12 (open, refresh, query). Local only.

Power BI Desktop hosts an Analysis Services engine on a random localhost port for each open file.
We reach it with the ADOMD client shipped inside Power BI Desktop (as_bridge.ps1), so no new Python
dependency and no network access beyond localhost. Nothing here modifies Power BI settings.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = Path(__file__).with_name("as_bridge.ps1")
from pci_data.schema import output_dir  # noqa: E402
# scratch (query text / results): .cache/powerbi (git-ignored) for synthetic; <PCI_IMS_DATA_DIR>/cache/powerbi for IMS
WORK = output_dir("cache", ROOT / ".cache") / "powerbi"
PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]


class DesktopError(RuntimeError):
    pass


def _ps(script: str, timeout=600) -> str:
    r = subprocess.run(PS + ["-Command", script], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise DesktopError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def find_desktop(title_part: str) -> int:
    """PID of the Power BI Desktop window whose title contains `title_part` (never any other window)."""
    out = _ps("Get-Process PBIDesktop -ErrorAction SilentlyContinue | "
              "Select-Object Id, MainWindowTitle | ConvertTo-Json -Compress")
    procs = json.loads(out) if out else []
    procs = procs if isinstance(procs, list) else [procs]
    hits = [p["Id"] for p in procs if title_part.lower() in (p.get("MainWindowTitle") or "").lower()]
    if len(hits) != 1:
        raise DesktopError(f"expected exactly one Power BI Desktop window titled '*{title_part}*', found {len(hits)}")
    return hits[0]


def open_pbip(pbip: Path, title_part: str, wait_s: int = 240) -> int:
    alias = Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "PBIDesktopStore.exe"
    exe = alias if alias.exists() else Path(r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe")
    subprocess.Popen([str(exe), str(pbip)])
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(5)
        try:
            pid = find_desktop(title_part)
            databases(pid)            # engine up and model loaded
            return pid
        except (DesktopError, subprocess.TimeoutExpired):
            continue
    raise DesktopError("Power BI Desktop did not open the project in time")


def _bridge(pid: int, mode: str, text: str, timeout=3600) -> Path | None:
    WORK.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:10]
    fin, fout = WORK / f"q_{tag}.txt", WORK / f"r_{tag}.tsv"
    fin.write_text(text, encoding="utf-8")
    try:
        cmd = (f"& '{BRIDGE}' -DesktopPid {pid} -Mode {mode} -In '{fin}' -TimeoutSec {int(timeout)}"
               + (f" -Out '{fout}'" if mode == "query" else ""))
        _ps(cmd, timeout=timeout + 60)
    finally:
        fin.unlink(missing_ok=True)
    return fout if mode == "query" else None


def _parse(v: str, t: str):
    if v == "\\N":
        return None
    if t in ("Double", "Decimal", "Single"):
        return float(v)
    if t in ("Int64", "Int32", "Int16", "UInt64", "UInt32", "UInt16", "Byte"):
        return int(v)
    if t == "Boolean":
        return v == "true"
    if t == "DateTime":
        return dt.date.fromisoformat(v)
    return v


def query(pid: int, dax: str, timeout=600) -> list[dict]:
    f = _bridge(pid, "query", dax, timeout)
    try:
        lines = f.read_text(encoding="utf-8").splitlines()     # bridge writes CRLF
    finally:
        f.unlink(missing_ok=True)
    names, types = lines[0].split("\t"), lines[1].split("\t")
    names = [n[n.index("[") + 1:-1] if n.endswith("]") and "[" in n else n for n in names]
    return [{n: _parse(v, t) for n, v, t in zip(names, ln.split("\t"), types)} for ln in lines[2:] if ln]


def timed_query(pid: int, dax: str, timeout=600) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    rows = query(pid, dax, timeout)
    return rows, time.perf_counter() - t0


def databases(pid: int) -> list[str]:
    return [r["CATALOG_NAME"] for r in query(pid, "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS", 120)]


def refresh(pid: int) -> float:
    db = databases(pid)[0]
    t0 = time.perf_counter()
    _bridge(pid, "exec", json.dumps({"refresh": {"type": "full", "objects": [{"database": db}]}}))
    return time.perf_counter() - t0


def model_size(pid: int) -> dict:
    rows = query(pid, "SELECT [DIMENSION_NAME], [TABLE_ID], [ROWS_COUNT] FROM $SYSTEM.DISCOVER_STORAGE_TABLES", 300)
    seg = query(pid, "SELECT [DIMENSION_NAME], [USED_SIZE] FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS", 300)
    size = {}
    for r in seg:
        size[r["DIMENSION_NAME"]] = size.get(r["DIMENSION_NAME"], 0) + (r["USED_SIZE"] or 0)
    counts = {}
    for r in rows:
        if r["TABLE_ID"].startswith(("H$", "R$", "U$")):
            continue
        counts[r["DIMENSION_NAME"]] = max(counts.get(r["DIMENSION_NAME"], 0), r["ROWS_COUNT"] or 0)
    return {"rows": counts, "segment_bytes": size}
