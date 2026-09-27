"""M15 traceability audit on the SYNTHETIC dataset (public-safe values).

For representative answers: agent finding value -> tool result field (source path) -> tool's recorded SQL ->
independent re-computation from the raw Parquet with a fresh DuckDB query -> independent Python engine.
    set PCI_DATASET=synthetic && .venv\\Scripts\\python.exe scripts\\trace_metrics.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from pci_data.schema import DATASET, PROCESSED_DIR  # noqa: E402

if DATASET != "synthetic":
    sys.exit("run with PCI_DATASET=synthetic (traces are committed; synthetic values only)")

import duckdb  # noqa: E402

from pci_agents import Orchestrator, get_provider  # noqa: E402
from pci_agents import schemas as S  # noqa: E402
from pci_analytics import metrics  # noqa: E402
from pci_analytics.api import CommercialAnalytics  # noqa: E402
from pci_analytics.engine import DataEngine  # noqa: E402
from pci_app.tools import ToolRegistry  # noqa: E402

P = PROCESSED_DIR.as_posix()
raw = duckdb.connect()
reg = ToolRegistry(CommercialAnalytics())
orch = Orchestrator(reg, get_provider("NONE"))
eng = DataEngine()
A = dt.date(2024, 5, 1)


def raw_value(where: str, start: str, end: str) -> float:
    """Independent SUM straight from the Parquet files (no views, no macros)."""
    return raw.execute(f"SELECT sum(f.value_cr) FROM read_parquet('{P}/fact_pack_month.parquet') f "
                       f"JOIN read_parquet('{P}/pack.parquet') p USING (pfc) WHERE {where} "
                       f"AND f.period BETWEEN DATE '{start}' AND DATE '{end}'").fetchone()[0]


traces = []
# 1. Agent: market performance by therapy area (MAT May 2024) -> first finding's value
out = orch.handle({"text": "market performance by therapy area MAT for 2024-05"})
f = out["findings"][0]
v = next(x for x in f["values"] if x["metric"] == "value_cur")
call = next(c for c in out["tool_calls"] if c["call"] == v["source"]["call"])
res = reg.invoke(call["tool"], call["input"])["result"]
row = S.get_path(res, v["source"]["path"][:2])
key = row["entity_key"]
indep = raw_value(f"p.supergroup = '{key}'", "2023-06-01", "2024-05-01")
py = next(r for r in metrics.entity_period(eng, "supergroup", A, "MAT") if r["entity_key"] == key)["value_cur"]
traces.append({"question": "market performance by therapy area MAT for 2024-05", "agent_statement": f["statement"],
               "agent_value": v["value"], "tool": call["tool"], "tool_input": call["input"], "source_path": v["source"]["path"],
               "entity": key, "tool_sql": res["evidence"]["sql"], "independent_parquet_sum": indep, "python_engine": py,
               "agree": abs(indep - v["value"]) <= 1e-9 * abs(indep) and abs(py - v["value"]) <= 1e-9 * abs(py),
               "qa_passed": out["qa"]["passed"]})

# 2. Agent: growth of the total market (month) -> growth = cur/prior*100-100 recomputed independently
out = orch.handle({"text": "market performance total month for 2024-05"})
f = out["findings"][0]
g = next(x for x in f["values"] if x["metric"] == "value_growth_pct")
cur = raw_value("TRUE", "2024-05-01", "2024-05-01")
pri = raw_value("TRUE", "2023-05-01", "2023-05-01")
traces.append({"question": "market performance total month for 2024-05", "agent_statement": f["statement"],
               "agent_value": g["value"], "tool": "get_market_performance", "source_path": g["source"]["path"],
               "independent_growth_pct": cur / pri * 100 - 100,
               "agree": abs((cur / pri * 100 - 100) - g["value"]) <= 1e-9, "qa_passed": out["qa"]["passed"]})

# 3. Scenario: price +10% on the top product -> value_1 = value_0 x 1.1 independently from Parquet
top = reg.invoke("get_brand_performance", {"anchor": "2024-05-01", "basis": "MAT", "top_n": 1})["result"]["rows"][0]
out = orch.handle({"text": f"what if price +10% for product \"{top['entity_key']}\""})
f = next(x for x in out["findings"] if "CALCULATED" in x["statement"])
scen = next(x for x in f["values"] if x["name"] == "scen")["value"]
base = raw_value(f"p.prod_code = {int(top['entity_key'])}", "2023-06-01", "2024-05-01")
traces.append({"question": f"what if price +10% for product {top['entity_key']}", "agent_statement": f["statement"],
               "agent_value": scen, "tool": "run_scenario", "independent_baseline_parquet": base,
               "independent_scenario": base * 1.10, "agree": abs(base * 1.10 - scen) <= 1e-9 * abs(scen),
               "qa_passed": out["qa"]["passed"]})

out_file = ROOT / "evaluation" / "reports" / "METRIC_TRACES_SYNTHETIC.json"
out_file.write_text(json.dumps({"dataset": "synthetic", "run_at": dt.datetime.now().isoformat(timespec="seconds"),
                                "traces": traces}, indent=2, default=str), encoding="utf-8")
print(json.dumps([{k: t[k] for k in ("question", "agent_value", "agree", "qa_passed")} for t in traces], indent=2))
sys.exit(0 if all(t["agree"] and t["qa_passed"] for t in traces) else 1)
