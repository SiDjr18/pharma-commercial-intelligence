"""Run the M10 evaluation locally:  cd python && ..\\.venv\\Scripts\\python.exe -m pci_eval"""
from pci_analytics import CommercialAnalytics
from pci_analytics.engine import default_engine
from pci_app.tools import ToolRegistry

from .runner import _pct, run_all, write_reports

api = CommercialAnalytics()
engine = default_engine()
api._opp_engine = engine
report = run_all(ToolRegistry(api), engine=engine)
paths = write_reports(report)
print(f"{report['passed']}/{report['total_cases']} cases passed; failed: {report['failed_case_ids'] or 'none'}")
for k, v in report["metrics"].items():
    print(f"  {k:<36} {_pct(v)}")
print("reports:", ", ".join(p.name for p in paths.values()), "-> the active dataset's reports folder")
