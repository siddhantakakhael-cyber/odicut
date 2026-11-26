"""OpenCode binary management + background `opencode serve` + HTTP client.

Binary resolution order:
  1. Settings -> opencode_path (validated)
  2. bundled binary (PyInstaller resource) copied to the config folder
  3. previously downloaded binary in the config folder
  4. `opencode` on PATH
  5. auto-download the platform package from the npm registry
"""
from __future__ import annotations
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import requests

from .config import config_home
from .logbus import bus


class OpenCodeError(RuntimeError):
    pass


def _exe_name() -> str:
    return "opencode.exe" if sys.platform == "win32" else "opencode"


def _platform_pkg() -> str:
    m = platform.machine().lower()
    arm = m in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "opencode-windows-arm64" if arm else "opencode-windows-x64"
    if sys.platform == "darwin":
        return "opencode-darwin-arm64" if arm else "opencode-darwin-x64"
    return "opencode-linux-arm64" if arm else "opencode-linux-x64"


def _works(path: str) -> bool:
    try:
        p = subprocess.run([path, "--version"], capture_output=True, timeout=30)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def managed_dir() -> Path:
    d = config_home() / "opencode"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bundled_binary() -> Path | None:
    """Binary shipped inside the PyInstaller exe (if present)."""
    candidates = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates += [base / "vendor" / _exe_name(), base / _exe_name()]
    candidates.append(Path(__file__).resolve().parent.parent / "vendor" / _exe_name())
    for c in candidates:
        if c.exists():
            return c
    return None


def find_binary(custom_path: str = "") -> str | None:
    if custom_path:
        if _works(custom_path):
            return custom_path
        raise OpenCodeError(
            f"Settings me diya OpenCode path kaam nahi karta: {custom_path}\n"
            "Settings me sahi path do ya khali chhod do (auto-download).")
    bundled = _bundled_binary()
    if bundled:
        dest = managed_dir() / _exe_name()
        if not dest.exists() or dest.stat().st_size != bundled.stat().st_size:
            shutil.copyfile(bundled, dest)
            if sys.platform != "win32":
                dest.chmod(0o755)
        if _works(str(dest)):
            return str(dest)
    local = managed_dir() / _exe_name()
    if local.exists() and _works(str(local)):
        return str(local)
    on_path = shutil.which("opencode")
    if on_path and _works(on_path):
        return on_path
    return None


def download_binary() -> str:
    pkg = _platform_pkg()
    dest = managed_dir() / _exe_name()
    bus.info(f"OpenCode auto-install: npm registry se {pkg} la raha hoon…")
    try:
        meta = requests.get(f"https://registry.npmjs.org/{pkg}", timeout=60).json()
        latest = meta.get("dist-tags", {}).get("latest")
        if not latest:
            raise OpenCodeError(f"{pkg} ka latest version nahi mila")
        tarball = meta["versions"][latest]["dist"]["tarball"]
        bus.info(f"OpenCode {latest} download ho raha hai…")
        data = requests.get(tarball, timeout=600).content
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            member = next((m for m in tf.getmembers()
                           if m.name.endswith(("bin/opencode", "bin/opencode.exe"))), None)
            if member is None:
                raise OpenCodeError("tarball me opencode binary nahi mila")
            f = tf.extractfile(member)
            dest.write_bytes(f.read())
        if sys.platform != "win32":
            dest.chmod(0o755)
        if not _works(str(dest)):
            raise OpenCodeError("download hua binary execute nahi hua")
        bus.ok(f"OpenCode {latest} install ho gaya: {dest}")
        return str(dest)
    except requests.RequestException as e:
        raise OpenCodeError(
            f"OpenCode download fail ({e}). Internet check karo, ya manually install karo:\n"
            "  npm install -g opencode-ai\n"
            "phir Settings me opencode path set karo.") from e


def ensure_binary(custom_path: str = "") -> str:
    found = find_binary(custom_path)
    if found:
        return found
    return download_binary()


class OpenCodeServer:
    """Runs `opencode serve` in the background and talks to its HTTP API."""

    def __init__(self, binary: str, port: int = 4096, workspace: str | None = None,
                 config_file: str | None = None):
        self.binary = binary
        self.port = port
        self.workspace = workspace or str(Path.home())
        self.config_file = config_file
        self.proc: subprocess.Popen | None = None
        self.base = f"http://127.0.0.1:{port}"

    # ---- lifecycle ----
    def start(self) -> None:
        if self.is_running():
            return
        Path(self.workspace).mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, "serve", "--port", str(self.port), "--hostname", "127.0.0.1",
               "--print-logs", "--log-level", "INFO"]
        bus.cmd(" ".join(cmd))
        env = dict(os.environ)
        env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
        if self.config_file:
            env["OPENCODE_CONFIG"] = str(self.config_file)
        self.proc = subprocess.Popen(
            cmd, cwd=self.workspace, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        threading.Thread(target=self._pump_logs, daemon=True).start()
        # wait for health
        for _ in range(60):
            if self.proc.poll() is not None:
                out = self.proc.stdout.read() if self.proc.stdout else ""
                raise OpenCodeError(f"opencode serve turant band ho gaya:\n{out[-800:]}")
            try:
                if self.health():
                    bus.ok(f"OpenCode server ready @ {self.base}")
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise OpenCodeError("OpenCode server 30s me ready nahi hua — logs dekho")

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def _pump_logs(self) -> None:
        """Forward opencode's stdout/stderr into the (redacting) hub log bus."""
        try:
            for line in self.proc.stdout:
                line = line.rstrip()
                if line:
                    bus.emit("INFO", "[opencode] " + line)
        except (ValueError, OSError):
            pass

    def drain_logs(self) -> str:
        """Non-blocking read of any pending server output (for the log panel)."""
        if not self.proc or not self.proc.stdout:
            return ""
        try:
            import select
            out = []
            while select.select([self.proc.stdout], [], [], 0)[0]:
                line = self.proc.stdout.readline()
                if not line:
                    break
                out.append(line.rstrip())
            return "\n".join(out)
        except Exception:
            return ""

    # ---- HTTP client ----
    def _req(self, method: str, path: str, body=None, timeout=180):
        url = self.base + path
        r = requests.request(method, url, json=body, timeout=timeout)
        return r

    def health(self) -> bool:
        r = self._req("GET", "/global/health", timeout=5)
        return r.status_code == 200

    def catalog(self) -> list[dict]:
        r = self._req("GET", "/provider", timeout=30)
        r.raise_for_status()
        return r.json().get("all", [])

    def set_auth(self, provider_id: str, key: str) -> bool:
        r = self._req("PUT", f"/auth/{provider_id}", {"type": "api", "key": key}, timeout=30)
        return r.status_code == 200

    def update_config(self, patch: dict) -> bool:
        r = self._req("PATCH", "/config", patch, timeout=30)
        return r.status_code == 200

    def create_session(self, title: str = "OpenCode Hub", directory: str | None = None) -> str:
        params = {"directory": directory or self.workspace}
        r = requests.post(self.base + "/session", json={"title": title}, params=params, timeout=30)
        r.raise_for_status()
        return r.json()["id"]

    def prompt(self, session_id: str, provider_id: str, model_id: str, text: str,
               agent: str = "build", directory: str | None = None) -> dict:
        body = {"model": {"providerID": provider_id, "modelID": model_id},
                "agent": agent, "parts": [{"type": "text", "text": text}]}
        params = {"directory": directory or self.workspace}
        r = requests.post(self.base + f"/session/{session_id}/message",
                          json=body, params=params, timeout=600)
        if r.status_code != 200:
            return {"_http_error": r.status_code, "_body": r.text[:500]}
        return r.json()

    def abort(self, session_id: str) -> None:
        try:
            requests.post(self.base + f"/session/{session_id}/abort", timeout=10)
        except requests.RequestException:
            pass
