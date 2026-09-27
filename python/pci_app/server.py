"""Local web server for the M8 application (Python standard library only).

Routes (nothing else is served):
  GET  /                      -> UI (app/web/index.html)
  GET  /agent                 -> M9 agentic analytics page (app/web/agent.html)
  GET  /api/agent/status      -> provider / mode status (DETERMINISTIC DEMO MODE by default) + demo grammar (M11)
  POST /api/agent             -> governed orchestrator (agents -> ToolRegistry only)
  GET  /static/<whitelisted>  -> app.js, style.css
  GET  /api/health            -> {"ok": true, ...}
  GET  /favicon.ico           -> 204 (nothing served; avoids browser console noise)
  GET  /api/tools             -> machine-readable tool contracts
  POST /api/tools/<tool name> -> JSON params -> structured tool result / error
Binds to 127.0.0.1 only; no outbound network calls; no SQL/Python/shell/file access for clients.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .tools import TOOLS, ToolRegistry, _scrub_obj, contracts

WEB_DIR = Path(__file__).resolve().parents[2] / "app" / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/static/style.css": ("style.css", "text/css; charset=utf-8"),
          "/agent": ("agent.html", "text/html; charset=utf-8"),                 # M9 agentic analytics page
          "/static/agent.js": ("agent.js", "text/javascript; charset=utf-8")}
MAX_BODY = 64 * 1024
DRAIN_LIMIT = 1024 * 1024
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
log = logging.getLogger("pci_app")


def make_handler(registry: ToolRegistry, lock: threading.Lock, orchestrator=None, provider_notice=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PCI-Local"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet, no request bodies logged
            log.debug("%s %s", self.command, self.path.split("?")[0])

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj, default=str, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _not_found(self):
            self._json(404, {"ok": False, "error": {"code": "NOT_FOUND", "category": "Not found",
                                                    "message": "No such route."}})

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in STATIC:
                name, ctype = STATIC[path]
                return self._send(200, (WEB_DIR / name).read_bytes(), ctype)
            if path == "/favicon.ico":                                          # browsers ask for it; nothing to serve
                return self._send(204, b"", "image/x-icon")
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "pci-local", "tools": len(TOOLS)})
            if path == "/api/tools":
                return self._json(200, {"ok": True, **contracts()})
            if path == "/api/agent/status" and orchestrator is not None:
                return self._json(200, {"ok": True, **orchestrator.status(), "notice": provider_notice,
                                        "supported_requests": _supported_forms(orchestrator)})
            return self._not_found()

        def _read_json(self):
            """Returns (params, None) or (None, already-sent)."""
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                # drain a bounded amount so the client receives the 413 (Windows aborts otherwise), then close
                self.rfile.read(min(length, DRAIN_LIMIT))
                self.close_connection = True
                self._json(413, {"ok": False, "error": {"code": "INVALID_INPUT", "category": "Invalid input",
                                                        "message": "request body too large"}})
                return None, True
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw or b"{}"), None
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"ok": False, "error": {"code": "INVALID_INPUT", "category": "Invalid input",
                                                        "message": "body must be a JSON object"}})
                return None, True

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/api/agent" and orchestrator is not None:          # M9 governed agent workflow
                body, sent = self._read_json()
                if sent:
                    return None
                with lock:
                    out = orchestrator.handle(body if isinstance(body, dict) else {"text": None})
                return self._json(200, _scrub_obj({"ok": True, **out}))
            if not path.startswith("/api/tools/"):
                return self._not_found()
            name = path[len("/api/tools/"):]
            params, sent = self._read_json()
            if sent:
                return None
            t = time.perf_counter()
            with lock:  # one analytics connection; serialise requests (local single-user app)
                out = registry.invoke(name, params)
            out["elapsed_ms"] = round((time.perf_counter() - t) * 1000)
            status = 200 if out["ok"] else (404 if out["error"]["code"] == "UNKNOWN_TOOL"
                                            else 500 if out["error"]["code"] == "INTERNAL_ERROR" else 400)
            self._json(status, out)

        def do_PUT(self):
            self._not_found()

        do_DELETE = do_PATCH = do_PUT

    return Handler


def create_server(api=None, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the application binds to the local machine only")
    if api is None:
        from pci_analytics import CommercialAnalytics
        api = CommercialAnalytics()
    registry = ToolRegistry(api)
    orchestrator, notice = _make_orchestrator(registry)
    return ThreadingHTTPServer((host, port), make_handler(registry, threading.Lock(), orchestrator, notice))


def _supported_forms(orchestrator) -> list:
    """M11: the documented request grammar of DETERMINISTIC DEMO MODE, shown in the AI Analyst UI (text only)."""
    if orchestrator.provider.name != "NONE":
        return []
    from pci_agents.parser import SUPPORTED_FORMS
    return list(SUPPORTED_FORMS)


def _make_orchestrator(registry):
    """M9 agent layer over the same ToolRegistry. LLM_PROVIDER defaults to NONE (deterministic demo mode);
    an unavailable provider never blocks the app: it falls back to NONE and says so."""
    from pci_agents import Orchestrator, ProviderNotConfigured, get_provider
    try:
        return Orchestrator(registry, get_provider()), None
    except ProviderNotConfigured as e:
        log.warning("%s -> falling back to LLM_PROVIDER=NONE", e)
        return Orchestrator(registry, get_provider("NONE")), f"{e}; running DETERMINISTIC DEMO MODE instead."


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pharma Commercial Intelligence - local application")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-warm", action="store_true", help="skip pre-loading the Python engine")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = time.perf_counter()
    from pci_analytics import CommercialAnalytics
    api = CommercialAnalytics()
    if not args.no_warm:
        api._python_engine()           # opportunity/scenario engine (~0.7 s)
    srv = create_server(api, port=args.port)
    log.info("Pharma Commercial Intelligence ready in %.1fs -> http://127.0.0.1:%d  (Ctrl+C to stop)",
             time.perf_counter() - t, args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
