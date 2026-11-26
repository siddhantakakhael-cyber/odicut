"""Hub HTTP server — serves the dark web UI + a JSON API.

The pywebview native window (or a browser) loads this. One Hub instance owns
the config, the background OpenCode server, and the auto-switch router.
"""
from __future__ import annotations
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import providers as P
from .config import Config
from .logbus import bus
from .opencode import OpenCodeServer, ensure_binary, OpenCodeError
from .router import Router


def web_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        cand = base / "ochub" / "web"
        if cand.exists():
            return cand
    return Path(__file__).resolve().parent / "web"


class Hub:
    def __init__(self, config: Config):
        self.config = config
        self.server: OpenCodeServer | None = None
        self.router: Router | None = None
        self.session_id: str | None = None
        self.binary: str | None = None
        self.start_error: str = ""
        self._lock = threading.RLock()
        bus.register_secrets(config.secrets())

    # ---- managed OpenCode config (OPENCODE_CONFIG) ----
    @property
    def managed_config(self) -> Path:
        return self.config.home / "opencode.json"

    def default_model(self) -> str:
        """model/small_model choice computed from config alone (no catalog needed)."""
        enabled = {pid: p for pid, p in self.config.providers.items()
                   if p.get("enabled", True) and p.get("api_key")}
        for ref in self.config.router.get("preferred", []):
            if "/" in ref and ref.split("/", 1)[0] in enabled:
                return ref
        for preset in P.FREE_PROVIDERS:
            pid = preset["id"]
            if pid in enabled:
                if preset.get("models"):
                    return f"{pid}/{preset['models'][0]}"
                if preset["model_filter"] == "free_suffix":
                    return f"{pid}/openrouter/free"
        for pid, p in enabled.items():
            if p.get("models"):
                return f"{pid}/{p['models'][0]}"
        return ""

    def write_managed_config(self) -> None:
        """Custom providers + free model defaults. Custom providers only register
        at server boot, so a change to them requires a restart."""
        cfg: dict = {}
        providers = {}
        for pid, p in self.config.providers.items():
            if P.is_builtin(pid) or not p.get("base_url"):
                continue
            providers[pid] = {
                "name": p.get("name", pid),
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": p["base_url"], "apiKey": p.get("api_key", "")},
                "models": {m: {"name": m} for m in p.get("models", [])},
            }
        if providers:
            cfg["provider"] = providers
        dm = self.default_model()
        if dm:
            cfg["model"] = dm
            cfg["small_model"] = dm
        self.config.home.mkdir(parents=True, exist_ok=True)
        self.managed_config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        bus.info(f"OpenCode config ready ({len(providers)} custom providers, model={dm or 'auto'})")

    # ---- opencode lifecycle ----
    def start_opencode(self) -> None:
        with self._lock:
            if self.server and self.server.is_running():
                return
            try:
                self.write_managed_config()
                self.binary = ensure_binary(self.config.prefs.get("opencode_path", ""))
                self.server = OpenCodeServer(self.binary, int(self.config.prefs.get("opencode_port", 4096)),
                                             str(self.config.workspace()), config_file=str(self.managed_config))
                self.server.start()
                self.router = Router(self.server, self.config)
                self.apply_providers()
                self.router.build_candidates()
                self.start_error = ""
            except OpenCodeError as e:
                self.start_error = str(e)
                bus.error(str(e))
                raise

    def restart_opencode(self) -> None:
        with self._lock:
            if self.server:
                self.server.stop()
            self.server = None
            self.session_id = None
            self.start_opencode()

    def stop_opencode(self) -> None:
        if self.server:
            self.server.stop()
            bus.info("OpenCode server stopped")

    def apply_providers(self) -> None:
        if not self.server:
            return
        for pid, p in self.config.providers.items():
            key = p.get("api_key", "")
            if not key:
                continue
            try:
                self.server.set_auth(pid, key)
                bus.info(f"Provider '{pid}' key set (masked)")
            except Exception as e:
                bus.warn(f"Provider '{pid}' auth set fail: {str(e)[:120]}")
        bus.register_secrets(self.config.secrets())

    def sync_default_model(self) -> None:
        """Best-effort live update of model/small_model (persisted in managed config too)."""
        if self.server and self.router and self.router.candidates:
            c = self.router.candidates[0]
            try:
                self.server.update_config({"model": c.key, "small_model": c.key})
            except Exception:
                pass

    def new_session(self) -> str:
        if not self.server:
            raise OpenCodeError("OpenCode running nahi hai")
        self.session_id = self.server.create_session("OpenCode Hub", str(self.config.workspace()))
        bus.info(f"Naya session: {self.session_id}")
        return self.session_id

    def chat(self, text: str) -> dict:
        if not self.server or not self.server.is_running():
            self.start_opencode()
        if not self.session_id:
            self.new_session()
        result = self.router.prompt(self.session_id, text)
        return {"text": result.text, "provider": result.provider, "model": result.model,
                "attempts": result.attempts, "switches": result.switches,
                "error": result.error, "tools": result.tools, "ok": result.ok}

    # ---- state for the UI ----
    def state(self) -> dict:
        running = bool(self.server and self.server.is_running())
        cands = [{"key": c.key, "provider": c.provider, "model": c.model,
                  "cooldown": max(0, round(c.cooldown_until - __import__('time').time())),
                  "fails": c.fails} for c in (self.router.candidates if self.router else [])]
        provs = []
        for pid, p in self.config.providers.items():
            preset = P.preset(pid)
            provs.append({"id": pid, "name": p.get("name") or (preset or {}).get("name", pid),
                          "enabled": p.get("enabled", True),
                          "has_key": bool(p.get("api_key")),
                          "masked": self.config.masked()["providers"].get(pid, {}).get("api_key", ""),
                          "builtin": P.is_builtin(pid),
                          "base_url": p.get("base_url", ""), "models": p.get("models", [])})
        return {"running": running, "start_error": self.start_error,
                "binary": self.binary or "", "session": self.session_id,
                "providers": provs, "candidates": cands,
                "router": self.config.router, "prefs": self.config.prefs,
                "last_used": self.router.last_used if self.router else "",
                "workspace": str(self.config.workspace())}


# ---------------------------------------------------------------- HTTP handler
def make_handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence default stderr spam
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            ln = int(self.headers.get("Content-Length", 0))
            if not ln:
                return {}
            try:
                return json.loads(self.rfile.read(ln))
            except json.JSONDecodeError:
                return {}

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            if p in ("/", "/index.html"):
                return self._send(200, (web_dir() / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/style.css":
                return self._send(200, (web_dir() / "style.css").read_bytes(), "text/css")
            if p == "/app.js":
                return self._send(200, (web_dir() / "app.js").read_bytes(), "application/javascript")
            if p == "/api/state":
                return self._send(200, hub.state())
            if p == "/api/presets":
                return self._send(200, P.FREE_PROVIDERS)
            if p == "/api/logs":
                return self._send(200, {"text": bus.text()})
            if p == "/api/health":
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            p = u.path
            body = self._body()
            try:
                if p == "/api/chat":
                    text = (body.get("text") or "").strip()
                    if not text:
                        return self._send(400, {"error": "empty"})
                    return self._send(200, hub.chat(text))
                if p == "/api/session/new":
                    return self._send(200, {"session": hub.new_session()})
                if p == "/api/providers":
                    pid = body.get("id", "").strip()
                    if not pid:
                        return self._send(400, {"error": "id required"})
                    entry = hub.config.providers.get(pid, {})
                    for k in ("api_key", "enabled", "name", "base_url", "models"):
                        if k in body:
                            entry[k] = body[k]
                    entry.setdefault("enabled", True)
                    hub.config.providers[pid] = entry
                    hub.config.save()
                    bus.register_secrets(hub.config.secrets())
                    if hub.server and hub.server.is_running():
                        if not P.is_builtin(pid):
                            hub.restart_opencode()  # custom providers register at boot
                        else:
                            hub.apply_providers()
                            hub.router.build_candidates()
                            hub.sync_default_model()
                            hub.write_managed_config()
                    return self._send(200, {"ok": True, "state": hub.state()})
                if p == "/api/providers/test":
                    pid = body.get("id", "")
                    entry = hub.config.providers.get(pid, {})
                    if not entry.get("api_key"):
                        return self._send(200, {"ok": False, "msg": "Key add karo pehle"})
                    if not hub.server or not hub.server.is_running():
                        hub.start_opencode()
                    elif not P.is_builtin(pid):
                        hub.restart_opencode()  # ensure custom provider is registered
                    hub.server.set_auth(pid, entry["api_key"])
                    models = entry.get("models") or (P.preset(pid) or {}).get("models", [])
                    if not models:
                        cat = {x["id"]: list((x.get("models") or {}).keys()) for x in hub.server.catalog()}
                        models = P.select_free_models(pid, cat.get(pid, []))
                    if not models:
                        return self._send(200, {"ok": False, "msg": "Is provider ka koi model nahi mila"})
                    sid = hub.server.create_session("key-test", str(hub.config.workspace()))
                    resp = hub.server.prompt(sid, pid, models[0], "Reply with exactly: OK")
                    from .router import extract_response, classify_error
                    ans, err, _ = extract_response(resp)
                    if not classify_error(err) and ans:
                        return self._send(200, {"ok": True, "msg": f"{pid}/{models[0]} works ✓"})
                    return self._send(200, {"ok": False, "msg": str(err)[:160]})
                if p == "/api/settings":
                    for k in ("router", "prefs"):
                        if k in body and isinstance(body[k], dict):
                            hub.config.data[k].update(body[k])
                    hub.config.save()
                    if hub.router:
                        hub.router.build_candidates()
                    return self._send(200, {"ok": True, "state": hub.state()})
                if p == "/api/opencode/start":
                    hub.start_opencode()
                    return self._send(200, {"ok": True, "state": hub.state()})
                if p == "/api/opencode/stop":
                    hub.stop_opencode()
                    return self._send(200, {"ok": True})
                if p == "/api/logs/clear":
                    bus.clear()
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})
            except OpenCodeError as e:
                return self._send(200, {"ok": False, "error": str(e)})
            except Exception as e:
                bus.error(f"{type(e).__name__}: {e}")
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_DELETE(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/providers/"):
                pid = u.path.split("/")[-1]
                was_custom = not P.is_builtin(pid)
                hub.config.providers.pop(pid, None)
                hub.config.save()
                bus.register_secrets(hub.config.secrets())
                if hub.server and hub.server.is_running():
                    if was_custom:
                        hub.restart_opencode()
                    elif hub.router:
                        hub.router.build_candidates()
                        hub.write_managed_config()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})

    return Handler


def serve(hub: Hub, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub))
    return httpd
