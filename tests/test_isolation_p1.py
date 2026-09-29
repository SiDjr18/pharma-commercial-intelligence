"""P1 isolation (2026-09-28): public/synthetic default, explicit external IMS mode, no discovery, no private output
in the repository, hardened local server, capped tool results, agent and Gemini boundaries.

Runs in BOTH modes (default synthetic and PCI_DATASET=ims). Uses only temporary folders and the fictional synthetic
generator; it reads no licensed data and prints no private value or path.
"""
import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
FAKE_KEY = "test-key-not-real"          # placeholder only; no real key is used
DATA_EXT = (".parquet", ".csv", ".tsv", ".jsonl", ".xlsx", ".xls", ".xlsm", ".xlsb", ".duckdb", ".db", ".sqlite",
            ".sqlite3", ".pkl", ".pickle", ".feather", ".arrow", ".abf", ".pbix", ".pbit", ".h5", ".ipynb")


def _clean_env(**kv):
    e = {k: v for k, v in os.environ.items()
         if k not in ("PCI_DATASET", "PCI_IMS_DATA_DIR", "PCI_SOURCE_PATH", "GEMINI_API_KEY", "LLM_PROVIDER")}
    e.update(kv)
    return e


def _schema(env, code="from pci_data import schema as s; print(s.DATASET, s.PROCESSED_DIR.name, s.IMS_DIR is not None)"):
    return subprocess.run([PY, "-c", "import sys; sys.path.insert(0, 'python'); " + code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def ext_dir(tmp_path):
    """A valid external IMS-style folder (outside the repository, not in a git work tree). Contents are fictional."""
    d = tmp_path / "external_ims_dir"
    d.mkdir()
    return d


# ====================================================================== dataset selection
def test_default_is_synthetic():
    r = _schema(_clean_env())
    assert r.returncode == 0 and r.stdout.split() == ["synthetic", "synthetic", "False"], r.stderr[-300:]


def test_explicit_synthetic():
    for v in ("synthetic", " Synthetic ", ""):
        r = _schema(_clean_env(PCI_DATASET=v))
        assert r.stdout.split() == ["synthetic", "synthetic", "False"], (v, r.stderr[-300:])


def test_ims_with_valid_external_dir(ext_dir):
    r = _schema(_clean_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(ext_dir)),
                "from pci_data import schema as s; print(s.DATASET, s.PROCESSED_DIR == s.IMS_DIR, "
                "s.DATASETS.keys() == {'synthetic', 'ims'})")
    assert r.returncode == 0 and r.stdout.split() == ["ims", "True", "True"], r.stderr[-300:]


@pytest.mark.parametrize("case", ["unset", "empty", "missing", "relative", "file", "repo", "repo_subdir", "repo_data",
                                  "repo_parent", "drive_root", "dotdot_into_repo", "inside_other_git_tree"])
def test_ims_invalid_dir_fails_closed(case, tmp_path):
    env = _clean_env(PCI_DATASET="ims")
    value = {
        "unset": None, "empty": "", "missing": str(tmp_path / "does_not_exist"), "relative": "some\\relative\\dir",
        "repo": str(ROOT), "repo_subdir": str(ROOT / "python"), "repo_data": str(ROOT / "data" / "synthetic"),
        "repo_parent": str(ROOT.parent), "drive_root": ROOT.anchor,
        "dotdot_into_repo": str(ROOT / "python" / ".." / "data"),
    }.get(case, "")
    if case == "file":
        f = tmp_path / "a_file.txt"
        f.write_text("x", encoding="utf-8")
        value = str(f)
    if case == "inside_other_git_tree":
        other = tmp_path / "other_repo"
        (other / ".git").mkdir(parents=True)
        (other / "ims").mkdir()
        value = str(other / "ims")
    if value is not None:
        env["PCI_IMS_DATA_DIR"] = value
    r = _schema(env)
    assert r.returncode != 0 and "DatasetConfigError" in r.stderr and not r.stdout, (case, r.stdout)
    if value and len(value) > 3:                                  # the error message never echoes the configured path
        assert value not in r.stderr.strip().splitlines()[-1]


def test_ims_dir_reached_through_a_link_into_the_repo_fails_closed(tmp_path):
    link = tmp_path / "link_to_repo_data"
    target = ROOT / "data"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except (OSError, AttributeError) as e:
        pytest.skip(f"cannot create a link here: {type(e).__name__}")
    try:
        r = _schema(_clean_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(link)))
        assert r.returncode != 0 and "outside the git repository" in r.stderr
    finally:
        os.rmdir(link) if os.name == "nt" else link.unlink()     # removes the link only, never the target


@pytest.mark.parametrize("value", ["private", "processed", "IMS2", "../../elsewhere", "C:\\data", "synthetic,ims"])
def test_invalid_dataset_value_fails_closed(value):
    r = _schema(_clean_env(PCI_DATASET=value))
    assert r.returncode != 0 and "PCI_DATASET must be one of" in r.stderr and not r.stdout


def test_no_silent_fallback_between_modes(ext_dir):
    # IMS requested but unusable -> error, never synthetic
    r = _schema(_clean_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(ext_dir / "missing")))
    assert r.returncode != 0 and "synthetic" not in r.stdout
    # synthetic requested while an IMS folder is configured -> synthetic, the IMS folder is ignored entirely
    r = _schema(_clean_env(PCI_DATASET="synthetic", PCI_IMS_DATA_DIR=str(ext_dir)))
    assert r.stdout.split() == ["synthetic", "synthetic", "False"]


def test_manifest_must_match_the_selected_dataset():
    from pci_data.dataset import DatasetConfigError, check_manifest
    check_manifest({"dataset": "synthetic"}, "synthetic")
    check_manifest({"source": {}}, "ims")
    with pytest.raises(DatasetConfigError):
        check_manifest({"source": {}}, "synthetic")               # licensed data copied into data/synthetic
    with pytest.raises(DatasetConfigError):
        check_manifest({"dataset": "synthetic"}, "ims")


# ====================================================================== no discovery in public mode
AUDIT = r"""
import json, os, sys
ev = []
def hook(e, a):
    if e in ("open", "os.listdir", "os.scandir", "glob.glob", "os.walk", "socket.connect", "subprocess.Popen",
             "urllib.Request"):
        ev.append((e, str(a[0]) if a else ""))
sys.addaudithook(hook)
sys.path.insert(0, "python")
from pci_data import schema
from pci_analytics import CommercialAnalytics
from pci_app.tools import ToolRegistry
reg = ToolRegistry(CommercialAnalytics())
out = reg.invoke("get_market_performance", {"level": "supergroup"})
print(json.dumps({"dataset": schema.DATASET, "ok": out["ok"], "events": ev}))
"""


def _ensure_synthetic():
    if not (ROOT / "data" / "synthetic" / "pack.parquet").exists():
        subprocess.run([PY, "-m", "pci_synthetic.generate"], cwd=ROOT / "python", env=_clean_env(), check=True,
                       capture_output=True, timeout=600)


def test_public_mode_does_not_discover_or_touch_private_locations(tmp_path):
    """Decoy IMS folder + decoy workbook configured in the environment: synthetic mode must never touch them,
    never list directories outside the repository/interpreter, and never open a network socket."""
    _ensure_synthetic()
    decoy = tmp_path / "DECOY_IMS_DIR"
    decoy.mkdir()
    (decoy / "pack.parquet").write_bytes(b"PAR1 decoy")
    (decoy / "_manifest.json").write_text("{}", encoding="utf-8")
    wb = tmp_path / "DECOY_WORKBOOK.xlsx"
    wb.write_bytes(b"decoy")
    r = subprocess.run([PY, "-c", AUDIT], cwd=ROOT, capture_output=True, text=True, timeout=300,
                       env=_clean_env(PCI_IMS_DATA_DIR=str(decoy), PCI_SOURCE_PATH=str(wb)))
    assert r.returncode == 0, r.stderr[-500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["dataset"] == "synthetic" and out["ok"]
    allowed = [os.path.normcase(str(p)) for p in (ROOT, Path(sys.prefix), Path(sys.base_prefix))]
    touched = [(e, p) for e, p in out["events"] if "DECOY" in p.upper()]
    assert touched == []
    outside = [(e, p) for e, p in out["events"]
               if e in ("open", "os.listdir", "os.scandir", "glob.glob", "os.walk") and p and not p.isdigit()
               and not any(os.path.normcase(os.path.abspath(p)).startswith(a) for a in allowed)]
    assert outside == [], outside[:5]
    assert [e for e, _ in out["events"] if e in ("socket.connect", "subprocess.Popen", "urllib.Request")] == []


def test_public_mode_needs_no_private_configuration():
    _ensure_synthetic()
    r = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, 'python'); from pci_analytics import "
                        "CommercialAnalytics as C; a = C(); print(a.dataset)"], cwd=ROOT, env=_clean_env(),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and r.stdout.strip() == "synthetic", r.stderr[-300:]


def test_dataset_code_never_searches():
    """Static: the selection code has no directory listing, globbing, walking or home-folder lookup."""
    src = (ROOT / "python" / "pci_data" / "dataset.py").read_text(encoding="utf-8")
    src += (ROOT / "python" / "pci_data" / "schema.py").read_text(encoding="utf-8")
    for bad in ("listdir", "scandir", "glob(", "rglob", "os.walk", "iterdir", "expanduser", "Path.home",
                "USERPROFILE", "HOMEDRIVE", "string.ascii", "D:\\\\", "E:\\\\"):
        assert bad not in src, bad


# ====================================================================== private outputs never in the repository
def test_output_locations_outside_repo_in_ims_mode(ext_dir):
    code = ("from pci_data import schema as s; from pci_powerbi import reconcile, desktop; from pci_eval import runner; "
            "from pathlib import Path; R = Path(s.PROJECT_ROOT); "
            "paths = [s.output_dir('reports', R), s.output_dir('cache', R), s.output_dir('profile', R), "
            "reconcile.REPORTS, desktop.WORK, runner.REPORT_DIR]; "
            "print(all(s.IMS_DIR in p.parents for p in paths), any(R in p.parents or p == R for p in paths))")
    r = _schema(_clean_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(ext_dir)), code)
    assert r.returncode == 0 and r.stdout.split() == ["True", "False"], r.stderr[-400:]


def test_assert_outside_repo_refuses_repository_paths(tmp_path):
    from pci_data.dataset import DatasetConfigError, assert_outside_repo
    for p in (ROOT, ROOT / "data" / "processed" / "x.parquet", ROOT / "evaluation" / "reports" / "r.json"):
        with pytest.raises(DatasetConfigError):
            assert_outside_repo(p)
    assert assert_outside_repo(tmp_path / "ok" / "r.json") == tmp_path / "ok" / "r.json"


def test_private_builders_refuse_public_mode():
    r = subprocess.run([PY, "-m", "pci_data.build_processed"], cwd=ROOT / "python", env=_clean_env(),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "PCI_DATASET=ims" in r.stderr


def test_synthetic_generator_never_overwrites_another_layer(tmp_path):
    other = tmp_path / "other_layer"
    other.mkdir()
    (other / "_manifest.json").write_text("{}", encoding="utf-8")
    r = subprocess.run([PY, "-m", "pci_synthetic.generate", "--out", str(other)], cwd=ROOT / "python",
                       env=_clean_env(), capture_output=True, text=True, timeout=300)
    assert r.returncode != 0 and "refusing" in (r.stderr + r.stdout)
    assert sorted(p.name for p in other.iterdir()) == ["_manifest.json"]


def test_powerbi_build_never_writes_a_real_data_folder_into_the_committed_project(tmp_path):
    from pci_powerbi import build
    with pytest.raises(ValueError, match="refusing"):
        build.build(out_dir=build.DASH, processed_dir=tmp_path, anchor_label="May 2024")


def test_no_data_files_inside_the_repository_outside_synthetic_locations():
    """After P1 the repository working tree holds no data files except the regenerable synthetic ones."""
    allowed_dirs = [ROOT / "data" / "synthetic", ROOT / "dashboards" / "_synthetic", ROOT / ".venv", ROOT / ".git",
                    ROOT / ".cache" / "pip"]
    offenders = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        d = Path(dirpath)
        if any(d == a or a in d.parents for a in allowed_dirs):
            dirnames[:] = []
            continue
        for f in filenames:
            if f.lower().endswith(DATA_EXT) and not ("_synthetic" in f or f.startswith("gemini_")):
                offenders.append(str((d / f).relative_to(ROOT)))
    assert offenders == [], offenders[:10]


def test_gitignore_covers_private_formats_and_folders():
    probes = ["data/ims/source.xlsx", "data/ims/notes.md", "data/processed/pack.parquet", "exports/a.tsv",
              "exports/a.jsonl", "notebooks/a.ipynb", "docs/shot.png", "docs/r.pdf", "b/a.tar.gz", "b/a.rar",
              "b/a.bak", "config/secrets.json", "gemini_api_key.txt", "evaluation/private_results.json",
              "PRIVATE_IMS_DATA/pack.parquet", ".env", ".env.local", "x.duckdb", "x.sqlite3", "x.zip", "x.7z"]
    r = subprocess.run(["git", "check-ignore", "--no-index", *probes], cwd=ROOT, capture_output=True, text=True)
    assert sorted(r.stdout.split("\n")[:-1]) == sorted(probes)


# ====================================================================== local server
@pytest.fixture(scope="module")
def syn_server(tmp_path_factory):
    from pci_analytics.api import CommercialAnalytics
    from pci_app.server import create_server
    from pci_data.db import connect
    from pci_synthetic.generate import write
    d = tmp_path_factory.mktemp("syn_server")
    write(d)
    srv = create_server(CommercialAnalytics(connect(d), d / "_manifest.json"), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _http(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    h = {"Host": host if host is not None else f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    if method == "POST":
        h.setdefault("Content-Type", "application/json")
        h["Content-Length"] = str(len(data))
    for k, v in h.items():
        if v is not None:
            c.putheader(k, v)
    c.endheaders(data if method == "POST" else None)
    r = c.getresponse()
    out = r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read()
    c.close()
    return out


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:{port}", "127.0.0.1:1", "10.0.0.5:{port}",
                                  "localhost.attacker.example:{port}", "", "127.0.0.1:{port}@evil"])
def test_server_rejects_invalid_host(syn_server, host):
    st, _, body = _http(syn_server, "GET", "/api/health", host=host.format(port=syn_server))
    assert st == 421 and b"INVALID_HOST" in body
    st, _, _ = _http(syn_server, "POST", "/api/tools/get_market_performance", {"level": "total"},
                     host=host.format(port=syn_server))
    assert st == 421


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}", "[::1]:{port}"])
def test_server_accepts_local_hosts(syn_server, host):
    st, _, _ = _http(syn_server, "GET", "/api/health", host=host.format(port=syn_server))
    assert st == 200


def test_server_rejects_missing_host_header(syn_server):
    c = http.client.HTTPConnection("127.0.0.1", syn_server, timeout=60)
    c.putrequest("GET", "/api/health", skip_host=True)
    c.endheaders()
    assert c.getresponse().status == 421
    c.close()


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "http://evil.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Origin": "http://127.0.0.1:1"}, 403),
    ({"Origin": "https://127.0.0.1:{port}"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Sec-Fetch-Site": "same-site"}, 403),
    ({"Content-Type": "text/plain"}, 415),
    ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
    ({"Origin": "http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}, 200),
    ({"Origin": "http://localhost:{port}"}, 200),
    ({}, 200),                                                    # non-browser local client (no Origin)
])
def test_server_origin_and_content_type_on_post(syn_server, headers, status):
    h = {k: v.format(port=syn_server) for k, v in headers.items()}
    for path, body in (("/api/tools/get_market_performance", {"level": "total"}), ("/api/agent", {"text": "hello"})):
        st, hdr, _ = _http(syn_server, "POST", path, body, headers=h)
        assert st == status, (path, headers, st)
        assert "access-control-allow-origin" not in hdr


def test_refusals_are_delivered_not_reset(syn_server):
    """A refused POST with an unread body must still deliver its 403/415/421 (no connection reset on Windows)."""
    body = {"level": "total", "pad": "x" * 20000}
    for _ in range(25):
        assert _http(syn_server, "POST", "/api/tools/get_market_performance", body,
                     headers={"Origin": "http://evil.example"})[0] == 403
        assert _http(syn_server, "POST", "/api/tools/get_market_performance", body,
                     headers={"Content-Type": "text/plain"})[0] == 415
        assert _http(syn_server, "POST", "/api/agent", body, host="attacker.example")[0] == 421


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.168.1.10", "example.com"])
def test_server_is_localhost_only(host):
    from pci_app.server import create_server
    with pytest.raises(ValueError, match="local machine only"):
        create_server(api=object(), host=host, port=0)


def test_server_binds_loopback_by_default():
    import inspect
    from pci_app import server
    assert inspect.signature(server.create_server).parameters["host"].default == "127.0.0.1"
    assert "--host" not in (ROOT / "python" / "pci_app" / "server.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["/static/../../README.md", "/%2e%2e/%2e%2e/run_app.py", "//etc/passwd",
                                  "/C:/Windows/win.ini", "/.git/config", "/.env", "/data/synthetic/pack.parquet",
                                  "/python/pci_data/schema.py", "/sql/views.sql", "/api/files?path=C:/", "/api/sql",
                                  "/static/", "/api/tools/../../run_app.py", "/static/app.js/../../../run_app.py"])
def test_api_cannot_read_arbitrary_files(syn_server, path):
    st, _, body = _http(syn_server, "GET", path)
    assert st == 404 and len(body) < 200


# ====================================================================== capped tool results
@pytest.fixture(scope="module")
def syn_registry(tmp_path_factory):
    from pci_analytics.api import CommercialAnalytics
    from pci_app.tools import ToolRegistry
    from pci_data.db import connect
    from pci_synthetic.generate import write
    d = tmp_path_factory.mktemp("syn_reg")
    write(d)
    return ToolRegistry(CommercialAnalytics(connect(d), d / "_manifest.json"))


def test_top_n_null_is_capped_never_unlimited(syn_registry):
    from pci_app.tools import MAX_PRODUCT_ROWS
    out = syn_registry.invoke("get_brand_performance", {"top_n": None})["result"]
    assert out["row_count"] == MAX_PRODUCT_ROWS < out["total_rows"]            # never the whole product universe
    assert any("safe maximum" in c for c in out["caveats"])
    opp = syn_registry.invoke("get_opportunity_scores", {"level": "product", "include_insufficient": True,
                                                          "top_n": None})["result"]
    assert opp["row_count"] <= MAX_PRODUCT_ROWS < opp["total_rows"]


def test_top_n_above_cap_rejected(syn_registry):
    from pci_app.tools import MAX_PRODUCT_ROWS, MAX_ROWS
    assert syn_registry.invoke("get_brand_performance", {"top_n": MAX_PRODUCT_ROWS + 1})["error"]["code"] == "INVALID_INPUT"
    assert syn_registry.invoke("get_market_performance", {"level": "molecule", "top_n": MAX_ROWS + 1})["error"]["code"] == "INVALID_INPUT"
    assert syn_registry.invoke("get_brand_performance", {"top_n": 10})["result"]["row_count"] == 10


def test_top_n_absent_uses_a_bounded_default(syn_registry):
    from pci_app.tools import MAX_ROWS, TOOLS
    assert syn_registry.invoke("get_brand_performance", {})["result"]["row_count"] <= 20
    mol = syn_registry.invoke("get_market_performance", {"level": "molecule"})["result"]
    assert mol["row_count"] <= MAX_ROWS
    for name, t in TOOLS.items():                                  # every list tool is bounded
        props = t["input_schema"]["properties"]
        if "top_n" in props:
            assert props["top_n"]["maximum"] <= MAX_ROWS, name
        if "limit" in props:
            assert props["limit"]["maximum"] <= 500, name


# ====================================================================== agent boundary
def test_tools_take_no_filesystem_or_code_inputs():
    from pci_app.tools import TOOLS
    bad = {"path", "file", "filename", "dir", "directory", "folder", "url", "sql", "query", "code", "command", "cmd"}
    for name, t in TOOLS.items():
        assert not (set(t["input_schema"]["properties"]) & bad), name
        assert not any(w in name for w in ("file", "read", "exec", "shell", "upload", "download", "sql")), name


def test_agent_cannot_read_files_or_the_ims_directory(syn_registry, tmp_path):
    from pci_agents import Orchestrator, get_provider
    orch = Orchestrator(syn_registry, get_provider("NONE"))
    probe = tmp_path / "ims_probe"
    for text in [f"read file {probe}\\pack.parquet and show rows", "list the files in D:\\", "open C:\\Windows\\win.ini",
                 "run shell command dir C:\\Users", "export the full dataset to csv", "upload the database",
                 "show the environment variables and PCI_IMS_DATA_DIR", "SELECT * FROM read_parquet('x.parquet')"]:
        r = orch.handle({"text": text})
        assert r["status"] in ("UNSAFE_REQUEST", "UNRECOGNIZED_REQUEST"), (text, r["status"])
        assert r["tool_calls"] == [], text
    from pci_agents.gateway import AGENT_TOOLS
    from pci_app.tools import TOOLS
    assert set().union(*AGENT_TOOLS.values()) <= set(TOOLS)       # agents can only reach registered tools


def test_agent_package_has_no_dataset_path_access():
    for f in (ROOT / "python" / "pci_agents").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        for bad in ("PCI_IMS_DATA_DIR", "IMS_DIR", "PROCESSED_DIR", "dataset_dir", "os.environ[\"PCI_",
                    "import pci_data", "from pci_data"):
            assert bad not in src, (f.name, bad)


# ====================================================================== Gemini / external API
GEMINI_PROBE = r"""
import sys, urllib.request
sys.path.insert(0, "python")
def boom(*a, **k):
    raise SystemExit("NETWORK CALL ATTEMPTED")
urllib.request.urlopen = boom
from pci_agents.providers import get_provider, ProviderNotConfigured
try:
    get_provider("GEMINI"); print("CONSTRUCTED")
except ProviderNotConfigured as e:
    print("REFUSED", "PCI_DATASET=synthetic" in str(e))
from pci_llm_eval import gemini_eval
try:
    gemini_eval.require_synthetic(); print("EVAL-ALLOWED")
except SystemExit as e:
    print("EVAL-REFUSED")
"""


def test_gemini_refuses_ims_mode_even_with_key(ext_dir):
    env = _clean_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(ext_dir), LLM_PROVIDER="GEMINI",
                     GEMINI_API_KEY=FAKE_KEY)
    r = subprocess.run([PY, "-c", GEMINI_PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.stdout.split() == ["REFUSED", "True", "EVAL-REFUSED"], (r.stdout, r.stderr[-300:])
    assert "NETWORK CALL ATTEMPTED" not in r.stdout + r.stderr and FAKE_KEY not in r.stdout + r.stderr


def test_app_falls_back_to_no_model_in_ims_mode(ext_dir):
    code = ("import sys; sys.path.insert(0, 'python'); from pci_app.server import _make_orchestrator; "
            "o, notice = _make_orchestrator(object()); print(o.provider.name, bool(notice))")
    env = _clean_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(ext_dir), LLM_PROVIDER="GEMINI",
                     GEMINI_API_KEY=FAKE_KEY)
    r = subprocess.run([PY, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.stdout.split() == ["NONE", "True"], r.stderr[-300:]
