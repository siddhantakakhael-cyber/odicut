import tempfile
import unittest
from pathlib import Path

from ochub.config import Config
from ochub.router import Router, classify_error, extract_response


def make_config(tmp, providers):
    c = Config(Path(tmp) / "home")
    c.data["providers"] = providers
    return c


class FakeServer:
    """Scripted OpenCode server: returns a queued response per prompt call."""
    def __init__(self, catalog, script):
        self._catalog = catalog
        self.script = list(script)
        self.calls = []

    def catalog(self):
        return self._catalog

    def prompt(self, sid, prov, model, text, agent="build"):
        self.calls.append(f"{prov}/{model}")
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return ok_resp("fallback answer")


def ok_resp(text):
    return {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": text}]}


def err_resp(name, msg):
    return {"info": {"role": "assistant", "error": {"name": name, "data": {"message": msg}}}, "parts": []}


CATALOG = [
    {"id": "groq", "models": {"openai/gpt-oss-120b": {}, "openai/gpt-oss-20b": {}, "qwen/qwen3.8-27b": {}}},
    {"id": "google", "models": {"gemini-2.5-flash": {}, "gemini-2.5-flash-lite": {}}},
    {"id": "openrouter", "models": {"google/gemma-4-31b-it:free": {}, "openrouter/free": {}, "paid/model": {}}},
]


class TestClassify(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(classify_error({"name": "APIError", "data": {"message": "Rate limit reached, 429"}}), "rate")
        self.assertEqual(classify_error({"name": "X", "data": {"message": "quota exceeded for tokens per day"}}), "rate")
        self.assertEqual(classify_error({"name": "X", "data": {"message": "Free usage limit reached"}}), "rate")
    def test_auth(self):
        self.assertEqual(classify_error({"name": "ProviderAuthError", "data": {"message": "invalid api key"}}), "auth")
    def test_notfound(self):
        self.assertEqual(classify_error({"name": "ProviderModelNotFoundError", "data": {"message": "Model not found"}}), "notfound")
    def test_none(self):
        self.assertEqual(classify_error(None), "")
    def test_extract(self):
        text, err, tools = extract_response(ok_resp("hello"))
        self.assertEqual(text, "hello"); self.assertIsNone(err)
        text, err, tools = extract_response(err_resp("APIError", "429 rate limit"))
        self.assertEqual(text, ""); self.assertTrue(err)
        text, err, tools = extract_response({"_http_error": 500, "_body": "boom"})
        self.assertTrue(err)


class TestRouter(unittest.TestCase):
    def test_build_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {"groq": {"api_key": "gsk_xxxxxxxxxxxx", "enabled": True},
                                    "openrouter": {"api_key": "sk-or-xxxxxxxxxxxx", "enabled": True}})
            r = Router(FakeServer(CATALOG, []), cfg)
            cands = r.build_candidates()
            keys = [c.key for c in cands]
            self.assertIn("groq/openai/gpt-oss-120b", keys)
            self.assertIn("openrouter/openrouter/free", keys)
            self.assertNotIn("openrouter/paid/model", keys)  # paid filtered out

    def test_auto_switch_on_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {"groq": {"api_key": "gsk_xxxxxxxxxxxx", "enabled": True}})
            script = [err_resp("APIError", "429 rate limit exceeded"),
                      err_resp("APIError", "quota exhausted"),
                      ok_resp("final answer")]
            srv = FakeServer(CATALOG, script)
            r = Router(srv, cfg)
            res = r.prompt("ses_x", "write code")
            self.assertTrue(res.ok, res.error)
            self.assertEqual(res.text, "final answer")
            self.assertEqual(res.attempts, 3)
            self.assertEqual(len(res.switches), 2)
            self.assertEqual(len(srv.calls), 3)

    def test_all_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {"groq": {"api_key": "gsk_xxxxxxxxxxxx", "enabled": True}})
            script = [err_resp("APIError", "429 rate limit")] * 10
            r = Router(FakeServer(CATALOG, script), cfg)
            res = r.prompt("ses_x", "hi")
            self.assertFalse(res.ok)
            self.assertIn("busy", res.error)

    def test_no_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {})
            r = Router(FakeServer(CATALOG, []), cfg)
            res = r.prompt("ses_x", "hi")
            self.assertFalse(res.ok)
            self.assertIn("provider", res.error.lower())

    def test_auto_switch_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {"groq": {"api_key": "gsk_xxxxxxxxxxxx", "enabled": True}})
            cfg.data["router"]["auto_switch"] = False
            script = [err_resp("APIError", "429 rate limit"), ok_resp("should not reach")]
            r = Router(FakeServer(CATALOG, script), cfg)
            res = r.prompt("ses_x", "hi")
            self.assertFalse(res.ok)
            self.assertEqual(res.attempts, 1)

    def test_network_error_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {"groq": {"api_key": "gsk_xxxxxxxxxxxx", "enabled": True}})
            script = [TimeoutError("connection reset"), ok_resp("recovered")]
            r = Router(FakeServer(CATALOG, script), cfg)
            res = r.prompt("ses_x", "hi")
            self.assertTrue(res.ok)
            self.assertEqual(res.attempts, 2)

    def test_cooldown_skips_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp, {"groq": {"api_key": "gsk_xxxxxxxxxxxx", "enabled": True}})
            cfg.data["router"]["cooldown_seconds"] = 300
            srv = FakeServer(CATALOG, [err_resp("APIError", "429 rate limit"), ok_resp("second model")] )
            r = Router(srv, cfg)
            res = r.prompt("ses_x", "hi")
            self.assertTrue(res.ok)
            first = res.switches[0]
            self.assertIn("cooldown", first)
            # the rate-limited candidate now has a cooldown
            cooled = [c for c in r.candidates if c.cooldown_until > 0]
            self.assertTrue(cooled)


if __name__ == "__main__":
    unittest.main()
