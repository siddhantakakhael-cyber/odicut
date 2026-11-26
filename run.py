"""OpenCode Hub entry point.

Starts the local hub server, launches OpenCode in the background, and opens a
native window (pywebview / WebView2). Falls back to the default browser if a
native webview isn't available.

  python run.py                 # native window (dev)
  python run.py --browser       # force browser
  python run.py --server-only   # no window (CI smoke test / headless)
  OpenCodeHub.exe               # packaged Windows app
"""
from __future__ import annotations
import argparse
import socket
import sys
import threading
import time
import webbrowser


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="OpenCodeHub")
    ap.add_argument("--port", type=int, default=0, help="hub UI port (0 = config/auto)")
    ap.add_argument("--browser", action="store_true", help="open in browser instead of native window")
    ap.add_argument("--server-only", action="store_true", help="run headless (no window)")
    args = ap.parse_args(argv)

    from ochub.config import Config
    from ochub.logbus import bus
    from ochub.server import Hub, serve

    config = Config()
    # disk log (redacted) — works even in windowed mode where stdout is None
    config.home.mkdir(parents=True, exist_ok=True)
    logpath = config.home / "hub.log"

    def _filelog(level, msg):
        try:
            with open(logpath, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}\n")
        except OSError:
            pass
    bus.subscribe(_filelog)

    hub = Hub(config)
    port = _free_port(args.port or int(config.prefs.get("port", 8765)))
    httpd = serve(hub, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    bus.ok(f"OpenCode Hub UI @ {url}")
    if sys.stdout:
        try:
            print(f"OpenCode Hub running at {url}", flush=True)
        except (AttributeError, OSError):
            pass

    def boot():
        try:
            hub.start_opencode()
        except Exception as e:
            bus.error(f"OpenCode start fail: {e}")
    threading.Thread(target=boot, daemon=True).start()

    try:
        if args.server_only:
            while True:
                time.sleep(1)
        elif args.browser:
            webbrowser.open(url)
            while True:
                time.sleep(1)
        else:
            try:
                import webview
                webview.create_window("OpenCode Hub", url, width=1180, height=820,
                                      min_size=(860, 560), background_color="#0b0d12")
                webview.start()  # blocks until the window closes
            except Exception as e:
                bus.warn(f"Native window nahi bana ({e}) — browser me khol raha hoon")
                webbrowser.open(url)
                while True:
                    time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop_opencode()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
