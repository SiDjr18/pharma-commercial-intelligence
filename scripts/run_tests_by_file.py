"""Run the whole pytest suite one test FILE per process, sequentially (memory-safe on a laptop).

    .venv\\Scripts\\python.exe scripts\\run_tests_by_file.py            # all tests/test_*.py
    .venv\\Scripts\\python.exe scripts\\run_tests_by_file.py tests/test_app.py tests/test_api.py

Nothing is skipped or modified: every collected test runs with its original assertions. Why per file: one long
single-process run once stalled under laptop memory pressure while each file passes in seconds to minutes
(PROJECT_STATUS.md). Per file it records wall time, exit code, JUnit counts and the 5 slowest tests.
Outputs: .cache/regression/ (git-ignored) in synthetic mode; <PCI_IMS_DATA_DIR>/cache/regression in IMS mode
(private-suite logs never enter the repository). Run the private suite with PCI_DATASET=ims + PCI_IMS_DATA_DIR.
"""
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from pci_data.schema import output_dir  # noqa: E402
OUT = output_dir("cache", ROOT / ".cache") / "regression"


def run_file(f: str) -> dict:
    name = Path(f).stem
    xml = OUT / f"{name}.xml"
    t0 = time.time()
    with open(OUT / f"{name}.log", "w", encoding="utf-8") as fh:
        rc = subprocess.run([sys.executable, "-m", "pytest", f, "-p", "no:cacheprovider", "--durations=10",
                             f"--junitxml={xml}"], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT).returncode
    r = ET.parse(xml).getroot()
    ts = r if r.tag == "testsuite" else r.find("testsuite")
    slow = sorted(((float(tc.get("time")), f"{tc.get('classname')}::{tc.get('name')}") for tc in ts.iter("testcase")),
                  reverse=True)[:5]
    return {"file": f, "exit": rc, "seconds": round(time.time() - t0, 1), "tests": int(ts.get("tests")),
            "failures": int(ts.get("failures")), "errors": int(ts.get("errors")), "skipped": int(ts.get("skipped")),
            "slowest": [[round(s, 1), n] for s, n in slow]}


def main(files):
    OUT.mkdir(parents=True, exist_ok=True)
    results, t0 = [], time.time()
    for f in files:
        res = run_file(f)
        results.append(res)
        print(json.dumps({k: res[k] for k in ("file", "exit", "seconds", "tests", "failures", "errors", "skipped")}),
              flush=True)
    tot = {k: sum(r[k] for r in results) for k in ("tests", "failures", "errors", "skipped")}
    (OUT / "summary.json").write_text(json.dumps({"results": results, "total": tot,
                                                  "elapsed_s": round(time.time() - t0, 1)}, indent=2), encoding="utf-8")
    print("TOTAL", json.dumps(tot), "elapsed_s", round(time.time() - t0, 1), flush=True)
    return 0 if tot["failures"] == tot["errors"] == 0 and all(r["exit"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))))
