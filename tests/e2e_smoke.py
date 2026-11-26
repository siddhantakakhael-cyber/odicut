"""End-to-end smoke test with the REAL opencode binary + a mock LLM provider.

Not part of unittest discovery (needs a real binary). Run:
    python tests/e2e_smoke.py /path/to/opencode

Chain verified: Hub API -> router -> opencode serve -> custom OpenAI-compatible
provider (local mock) -> streamed reply. Also verifies 429 auto-switch.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ochub.config import Config
from ochub.server import Hub, serve

MOCK_REPLY = "SMOKE_OK: mock model ne jawab diya."


class MockLLM(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(ln) or b"{}")
        model = body.get("model", "")
        if model == "m-rate":  # simulate rate limit for auto-switch test
            data = json.dumps({"error": {"message": "Rate limit reached: 429 too many requests", "type": "rate_limit"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        # streamed OpenAI-compatible completion
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        base = {"id": "chatcmpl-smoke", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model}
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": MOCK_REPLY}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13}},
        ]
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def req(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.loads(resp.read())


def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else "opencode"
    tmp = tempfile.mkdtemp(prefix="ochub-e2e-")
    os.environ["OCHUB_HOME"] = str(Path(tmp) / "home")

    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockLLM)
    mock_port = mock.server_address[1]
    threading.Thread(target=mock.serve_forever, daemon=True).start()

    cfg = Config(Path(tmp) / "home")
    cfg.prefs["opencode_path"] = binary
    cfg.prefs["workspace"] = str(Path(tmp) / "ws")
    cfg.prefs["opencode_port"] = 4199
    hub = Hub(cfg)
    httpd = serve(hub, 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    failures = []
    try:
        print("1) OpenCode start…")
        hub.start_opencode()
        state = req(port, "GET", "/api/state")
        assert state["running"], "opencode not running"
        print("   running ✓  binary:", state["binary"])

        print("2) custom provider add (mock LLM, m-rate pehle -> 429, m-ok -> reply)…")
        r = req(port, "POST", "/api/providers", {
            "id": "mockllm", "name": "Mock LLM",
            "base_url": f"http://127.0.0.1:{mock_port}/v1",
            "models": ["m-rate", "m-ok"], "api_key": "mock-key-123456", "enabled": True})
        assert r["ok"], r
        state = req(port, "GET", "/api/state")
        keys = [c["key"] for c in state["candidates"]]
        assert "mockllm/m-rate" in keys and "mockllm/m-ok" in keys, keys
        print("   candidates:", keys)

        print("3) chat -> 429 auto-switch -> reply…")
        r = req(port, "POST", "/api/chat", {"text": "Reply with exactly: SMOKE_OK"})
        print("   ok:", r["ok"], "| model:", r.get("model"), "| attempts:", r.get("attempts"))
        print("   switches:", r.get("switches"))
        print("   text:", (r.get("text") or r.get("error", ""))[:200])
        if not r["ok"]:
            failures.append("chat failed: " + str(r.get("error")))
        elif "SMOKE_OK" not in r["text"]:
            failures.append("unexpected reply: " + r["text"][:120])
        elif r["attempts"] < 2:
            failures.append("auto-switch nahi hua (attempts < 2)")

        print("   --- opencode server output (tail) ---")
        time.sleep(0.5)
        from ochub.logbus import bus
        oc_lines = [l for l in bus.text().split("\n") if "[opencode]" in l]
        print("\n".join(oc_lines[-40:]))
        print("4) logs me key leak check…")
        logs = req(port, "GET", "/api/logs")["text"]
        if "mock-key-123456" in logs:
            failures.append("KEY LEAKED IN LOGS")
        else:
            print("   redacted ✓")
    except Exception as e:
        failures.append(f"{type(e).__name__}: {e}")
    finally:
        hub.stop_opencode()
        httpd.shutdown()
        mock.shutdown()

    if failures:
        print("\nE2E FAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nE2E PASS ✓ (real opencode + auto-switch + redaction)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
