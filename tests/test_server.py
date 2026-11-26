"""HTTP API tests: real hub server on an ephemeral port + fake OpenCode."""
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from ochub.config import Config
from ochub.router import Router
from ochub.server import Hub, serve

CATALOG = [{"id": "groq", "models": {"openai/gpt-oss-120b": {}, "openai/gpt-oss-20b": {}, "qwen/qwen3.8-27b": {}}}]


class FakeOC:
    def __init__(self):
        self.script = []
        self.auth = {}

    def is_running(self): return True
    def start(self): pass
    def catalog(self): return CATALOG
    def set_auth(self, pid, key): self.auth[pid] = key; return True
    def update_config(self, patch): return True
    def create_session(self, title="t", directory=None): return "ses_fake"
    def stop(self): pass
    def prompt(self, sid, prov, model, text, agent="build"):
        if self.script:
            return self.script.pop(0)
        return {"info": {}, "parts": [{"type": "text", "text": f"echo:{text}"}]}


def ok(text): return {"info": {}, "parts": [{"type": "text", "text": text}]}
def rate(): return {"info": {"error": {"name": "APIError", "data": {"message": "429 rate limit"}}}, "parts": []}


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ochub.server as srvmod
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = Config(Path(cls.tmp.name))
        cls.hub = Hub(cls.cfg)
        cls.fake = FakeOC()
        cls.hub.server = cls.fake
        cls.hub.router = Router(cls.fake, cls.cfg)
        # inject the fake so custom-provider restarts don't touch a real binary
        cls._orig_ensure, cls._orig_oc = srvmod.ensure_binary, srvmod.OpenCodeServer
        srvmod.ensure_binary = lambda custom="": "fake-binary"
        srvmod.OpenCodeServer = lambda *a, **k: cls.fake
        cls.httpd = serve(cls.hub, 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        import ochub.server as srvmod
        srvmod.ensure_binary, srvmod.OpenCodeServer = cls._orig_ensure, cls._orig_oc
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                   method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return json.loads(raw) if "json" in ctype else raw.decode()

    def test_01_index_served(self):
        html = self.req("GET", "/")
        self.assertIn("OpenCode Hub", html)
        self.assertIn("/app.js", html)

    def test_02_assets(self):
        self.assertIn(":root", self.req("GET", "/style.css"))
        self.assertIn("sendChat", self.req("GET", "/app.js"))

    def test_03_presets(self):
        presets = self.req("GET", "/api/presets")
        ids = [p["id"] for p in presets]
        for want in ("openrouter", "groq", "google", "mistral", "cerebras", "sambanova"):
            self.assertIn(want, ids)

    def test_04_add_provider_and_state_masks_key(self):
        r = self.req("POST", "/api/providers", {"id": "groq", "api_key": "gsk_testkey_123456789", "enabled": True})
        self.assertTrue(r["ok"])
        state = self.req("GET", "/api/state")
        self.assertNotIn("gsk_testkey_123456789", json.dumps(state))
        groq = next(p for p in state["providers"] if p["id"] == "groq")
        self.assertTrue(groq["has_key"])
        self.assertTrue(groq["masked"])
        # persisted to disk
        disk = json.loads((Path(self.tmp.name) / "config.json").read_text())
        self.assertEqual(disk["providers"]["groq"]["api_key"], "gsk_testkey_123456789")
        # candidates built
        self.assertTrue(any(c["provider"] == "groq" for c in state["candidates"]))

    def test_05_chat_success(self):
        self.fake.script = [ok("hello from model")]
        r = self.req("POST", "/api/chat", {"text": "hi"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "hello from model")

    def test_06_chat_autoswitch(self):
        self.fake.script = [rate(), rate(), ok("recovered answer")]
        r = self.req("POST", "/api/chat", {"text": "hi again"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["attempts"], 3)
        self.assertEqual(len(r["switches"]), 2)

    def test_07_settings(self):
        r = self.req("POST", "/api/settings", {"router": {"cooldown_seconds": 120}})
        self.assertTrue(r["ok"])
        state = self.req("GET", "/api/state")
        self.assertEqual(state["router"]["cooldown_seconds"], 120)

    def test_08_delete_provider(self):
        self.req("POST", "/api/providers", {"id": "groq", "api_key": "gsk_testkey_123456789"})
        r = self.req("DELETE", "/api/providers/groq")
        self.assertTrue(r["ok"])
        state = self.req("GET", "/api/state")
        self.assertFalse(any(p["id"] == "groq" for p in state["providers"]))

    def test_09_custom_provider(self):
        r = self.req("POST", "/api/providers", {"id": "mylab", "name": "My Lab",
                     "base_url": "http://127.0.0.1:9/v1", "models": ["m1"], "api_key": "local-key-123456"})
        self.assertTrue(r["ok"])
        state = self.req("GET", "/api/state")
        mine = next(p for p in state["providers"] if p["id"] == "mylab")
        self.assertFalse(mine["builtin"])
        self.req("DELETE", "/api/providers/mylab")

    def test_10_logs_redacted(self):
        self.req("POST", "/api/providers", {"id": "groq", "api_key": "gsk_logleak_987654321"})
        logs = self.req("GET", "/api/logs")["text"]
        self.assertNotIn("gsk_logleak_987654321", logs)
        self.req("DELETE", "/api/providers/groq")

    def test_11_empty_chat_rejected(self):
        try:
            self.req("POST", "/api/chat", {"text": "  "})
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()
