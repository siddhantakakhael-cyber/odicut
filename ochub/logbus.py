"""Thread-safe log bus with secret redaction.

Every line is redacted BEFORE any subscriber sees it, so full API keys never
reach the GUI log panel, stdout, or disk.
"""
from __future__ import annotations
import re
import threading
import time
from typing import Callable

_LEVELS = ("INFO", "STEP", "CMD", "WARN", "ERROR", "OK", "SWITCH")
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bkey-[A-Za-z0-9]{16,}\b"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|authorization)\s*[=:]\s*)[^\s'\"]{8,}"),
]


class LogBus:
    def __init__(self):
        self._subs: list[Callable[[str, str], None]] = []
        self._secrets: set[str] = set()
        self._lock = threading.Lock()
        self.history: list[tuple[float, str, str]] = []

    def register_secrets(self, values) -> None:
        with self._lock:
            self._secrets = {v for v in values if v and len(v) >= 6}

    def redact(self, text: str) -> str:
        with self._lock:
            secrets = list(self._secrets)
        for s in secrets:
            if s in text:
                text = text.replace(s, "***REDACTED***")
        for pat in _PATTERNS:
            text = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***REDACTED***", text)
        return text

    def subscribe(self, fn) -> None:
        self._subs.append(fn)

    def emit(self, level: str, msg: str) -> None:
        level = level if level in _LEVELS else "INFO"
        msg = self.redact(str(msg))
        with self._lock:
            self.history.append((time.time(), level, msg))
            if len(self.history) > 8000:
                self.history = self.history[-6000:]
        for fn in list(self._subs):
            try:
                fn(level, msg)
            except Exception:
                pass

    def info(self, m): self.emit("INFO", m)
    def step(self, m): self.emit("STEP", m)
    def cmd(self, m): self.emit("CMD", m)
    def warn(self, m): self.emit("WARN", m)
    def error(self, m): self.emit("ERROR", m)
    def ok(self, m): self.emit("OK", m)
    def switch(self, m): self.emit("SWITCH", m)

    def text(self) -> str:
        with self._lock:
            return "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(ts))} [{lv}] {m}"
                             for ts, lv, m in self.history)

    def clear(self) -> None:
        with self._lock:
            self.history.clear()


bus = LogBus()
