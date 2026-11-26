"""Persistent local config for OpenCode Hub.

Stored in the user's config folder (never hardcoded, survives restarts):
  Windows : %APPDATA%/OpenCodeHub/config.json
  macOS   : ~/Library/Application Support/OpenCodeHub/config.json
  Linux   : ~/.config/OpenCodeHub/config.json
Override with OCHUB_HOME (tests / portable mode).
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "OpenCodeHub"

DEFAULTS = {
    # provider id -> {api_key, enabled, name?, base_url?, models?}
    "providers": {},
    "router": {
        "auto_switch": True,
        "cooldown_seconds": 60,
        "max_attempts": 6,
        "preferred": [],          # ordered "provider/model" refs the user pinned
    },
    "prefs": {
        "port": 8765,             # hub web/API port
        "opencode_port": 4096,    # opencode serve port
        "opencode_path": "",      # custom binary; empty = auto-managed
        "workspace": "",          # project dir OpenCode operates on
        "theme": "dark",
    },
    "opencode": {"installed_path": "", "version": ""},
}


def config_home() -> Path:
    env = os.environ.get("OCHUB_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP_NAME


def default_workspace() -> Path:
    return Path.home() / "OpenCodeHub"


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else config_home()
        self.path = self.home / "config.json"
        self.data = json.loads(json.dumps(DEFAULTS))
        self.data["prefs"]["workspace"] = str(default_workspace())
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data = _deep_merge(self.data, json.loads(self.path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                pass  # corrupt file must never crash startup
        if not self.data["prefs"].get("workspace"):
            self.data["prefs"]["workspace"] = str(default_workspace())

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.home), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # accessors
    @property
    def providers(self) -> dict: return self.data["providers"]
    @property
    def router(self) -> dict: return self.data["router"]
    @property
    def prefs(self) -> dict: return self.data["prefs"]

    def workspace(self) -> Path:
        p = Path(self.prefs.get("workspace") or default_workspace())
        p.mkdir(parents=True, exist_ok=True)
        return p

    def secrets(self) -> list[str]:
        vals = [p.get("api_key", "") for p in self.providers.values()]
        return [v for v in vals if v and len(v) >= 6]

    def masked(self) -> dict:
        def mask(v): return ("••••" + v[-4:]) if v and len(v) > 4 else ("••••" if v else "")
        view = json.loads(json.dumps(self.data))
        for p in view.get("providers", {}).values():
            if "api_key" in p:
                p["api_key"] = mask(p["api_key"])
        return view
