"""Free-model auto-switch router — the heart of OpenCode Hub.

Builds an ordered roster of free-tier models from the providers the user has
enabled, then sends each prompt through OpenCode. If a model hits a rate limit,
quota, auth error, or outage, it automatically switches to the next free model
and retries — transparently, with a log of every switch.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field

from . import providers as P
from .logbus import bus
from .opencode import OpenCodeServer

# error signatures that mean "try another model"
_RATE_RE = re.compile(
    r"rate.?limit|429|quota|resource.?exhausted|too many requests|overloaded|"
    r"capacity|tokens per (day|minute)|requests per (day|minute)|limit reached|"
    r"free usage limit|insufficient|billing|credit", re.I)
_AUTH_RE = re.compile(r"auth|401|403|invalid.{0,20}key|unauthorized|api.?key|permission", re.I)
_NOTFOUND_RE = re.compile(r"not.?found|unknown model|does not exist|providermodelnotfound", re.I)


@dataclass
class Candidate:
    provider: str
    model: str
    cooldown_until: float = 0.0
    fails: int = 0

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class PromptResult:
    text: str
    provider: str
    model: str
    attempts: int
    switches: list[str] = field(default_factory=list)
    error: str = ""
    tools: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


def classify_error(err) -> str:
    """Return 'rate' | 'auth' | 'notfound' | 'other' | '' (no error)."""
    if not err:
        return ""
    if isinstance(err, dict):
        name = str(err.get("name", ""))
        msg = str(err.get("data", {}).get("message", "")) if isinstance(err.get("data"), dict) else str(err.get("data", ""))
        blob = f"{name} {msg}"
    else:
        blob = str(err)
    if _RATE_RE.search(blob):
        return "rate"
    if _NOTFOUND_RE.search(blob):
        return "notfound"
    if _AUTH_RE.search(blob):
        return "auth"
    return "other"


def extract_response(resp: dict) -> tuple[str, object, list[str]]:
    """(assistant_text, error_or_none, tool_names) from an OpenCode prompt reply."""
    if "_http_error" in resp:
        return "", {"name": "HTTPError", "data": {"message": f"HTTP {resp['_http_error']}: {resp.get('_body','')}"}}, []
    info = resp.get("info", {})
    error = info.get("error")
    texts, tools = [], []
    for part in resp.get("parts", []):
        t = part.get("type")
        if t == "text" and part.get("text"):
            texts.append(part["text"])
        elif t == "tool":
            tools.append(part.get("tool", "tool"))
    return "\n".join(texts).strip(), error, tools


class Router:
    def __init__(self, server: OpenCodeServer, config):
        self.server = server
        self.config = config
        self.candidates: list[Candidate] = []
        self.last_used: str = ""

    # ---- roster ----
    def build_candidates(self) -> list[Candidate]:
        enabled = {pid: p for pid, p in self.config.providers.items()
                   if p.get("enabled", True) and p.get("api_key")}
        if not enabled:
            self.candidates = []
            return []
        try:
            catalog = {p["id"]: list((p.get("models") or {}).keys()) for p in self.server.catalog()}
        except Exception as e:
            bus.warn(f"Catalog fetch fail ({e}) — preset models use honge")
            catalog = {}
        cands: list[Candidate] = []
        # preferred pins first
        preferred = self.config.router.get("preferred", [])
        seen = set()
        for ref in preferred:
            if "/" in ref:
                prov, model = ref.split("/", 1)
                if prov in enabled and ref not in seen:
                    cands.append(Candidate(prov, model))
                    seen.add(ref)
        # then each enabled provider in preset order, then customs
        order = [p["id"] for p in P.FREE_PROVIDERS if p["id"] in enabled]
        order += [pid for pid in enabled if pid not in order]
        for pid in order:
            models = catalog.get(pid)
            if models is None:
                # custom provider models from config, or preset curated list
                models = enabled[pid].get("models") or (P.preset(pid) or {}).get("models", [])
            free = P.select_free_models(pid, models) if P.is_builtin(pid) else list(models)
            for m in free:
                ref = f"{pid}/{m}"
                if ref not in seen:
                    cands.append(Candidate(pid, m))
                    seen.add(ref)
        self.candidates = cands
        bus.info(f"Router: {len(cands)} free models ready ({', '.join(sorted(enabled))})")
        return cands

    def _available(self) -> list[Candidate]:
        now = time.time()
        ready = [c for c in self.candidates if c.cooldown_until <= now]
        return ready

    # ---- prompt with auto-switch ----
    def prompt(self, session_id: str, text: str, agent: str = "build",
               on_switch=None) -> PromptResult:
        if not self.candidates:
            self.build_candidates()
        if not self.candidates:
            return PromptResult("", "", "", 0,
                                error="Koi free provider enabled nahi hai. Settings me ek provider ki API key add karo.")
        auto = self.config.router.get("auto_switch", True)
        max_attempts = int(self.config.router.get("max_attempts", 6))
        cooldown = int(self.config.router.get("cooldown_seconds", 60))
        switches: list[str] = []
        attempts = 0
        tried: set[str] = set()

        # start from last_used if it's still available, else first available
        pool = self._available() or self.candidates
        start = 0
        if self.last_used:
            for i, c in enumerate(pool):
                if c.key == self.last_used:
                    start = i
                    break
        ordered = pool[start:] + pool[:start]

        for cand in ordered:
            if attempts >= max_attempts:
                break
            if cand.key in tried:
                continue
            if not auto and attempts >= 1:
                break
            tried.add(cand.key)
            attempts += 1
            bus.step(f"Try #{attempts}: {cand.key}")
            if on_switch:
                on_switch(cand.key, attempts)
            try:
                resp = self.server.prompt(session_id, cand.provider, cand.model, text, agent=agent)
            except Exception as e:
                bus.warn(f"{cand.key} network/timeout: {str(e)[:140]}")
                cand.fails += 1
                if auto:
                    switches.append(f"{cand.key} → network error, switching")
                    bus.switch(switches[-1])
                continue
            answer, error, tools = extract_response(resp)
            kind = classify_error(error)
            if not kind and answer:
                self.last_used = cand.key
                cand.fails = 0
                bus.ok(f"Reply from {cand.key} ({len(answer)} chars)")
                return PromptResult(answer, cand.provider, cand.model, attempts, switches, tools=tools)
            # error path
            errmsg = ""
            if isinstance(error, dict):
                errmsg = str(error.get("data", {}).get("message", error.get("name", "")))
            else:
                errmsg = str(error)
            if kind == "rate":
                cand.cooldown_until = time.time() + cooldown
                cand.fails += 1
                msg = f"{cand.key} rate-limited → {cooldown}s cooldown, switching"
            elif kind == "auth":
                cand.fails += 1
                msg = f"{cand.key} auth issue → switching ({errmsg[:80]})"
            elif kind == "notfound":
                cand.fails += 1
                msg = f"{cand.key} model not found → switching"
            else:
                cand.fails += 1
                msg = f"{cand.key} error → switching ({errmsg[:80]})"
            switches.append(msg)
            bus.switch(msg)
            if not auto:
                return PromptResult("", cand.provider, cand.model, attempts, switches, error=errmsg)
        return PromptResult("", "", "", attempts, switches,
                            error="Saare free models abhi busy/rate-limited hain. Thodi der baad try karo "
                                  "ya aur providers add karo.")
