"""Free LLM provider presets + management.

These are providers with a genuine $0 tier that OpenCode supports natively.
The router discovers the *actual* available model IDs from OpenCode's live
catalog and intersects them with these presets, so we never hardcode a model
that the user's OpenCode build doesn't have.
"""
from __future__ import annotations

# model_filter:
#   "free_suffix" -> keep models whose id ends with ":free" (OpenRouter)
#   "curated"     -> keep `models` list if present in catalog, else fallback
FREE_PROVIDERS = [
    {
        "id": "openrouter", "name": "OpenRouter", "env": "OPENROUTER_API_KEY",
        "key_url": "https://openrouter.ai/keys",
        "free_hint": ":free models · ~20 RPM · 50–1000 requests/day",
        "model_filter": "free_suffix",
        "models": [],
    },
    {
        "id": "groq", "name": "Groq", "env": "GROQ_API_KEY",
        "key_url": "https://console.groq.com/keys",
        "free_hint": "free tier · ~30 RPM · 1000 requests/day · very fast",
        "model_filter": "curated",
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
                   "meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.3-70b-versatile"],
    },
    {
        "id": "google", "name": "Google Gemini", "env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/apikey",
        "free_hint": "free tier · ~15 RPM · 1500 requests/day · 1M context",
        "model_filter": "curated",
        "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-lite-latest",
                   "gemini-2.0-flash", "gemini-3.6-flash"],
    },
    {
        "id": "mistral", "name": "Mistral (Codestral)", "env": "MISTRAL_API_KEY",
        "key_url": "https://console.mistral.ai/api-keys",
        "free_hint": "Experiment plan · phone-verified · code-focused",
        "model_filter": "curated",
        "models": ["codestral-latest", "devstral-2512", "ministral-8b-latest", "mistral-small-latest"],
    },
    {
        "id": "cerebras", "name": "Cerebras", "env": "CEREBRAS_API_KEY",
        "key_url": "https://cloud.cerebras.ai",
        "free_hint": "free tier · fast inference",
        "model_filter": "curated",
        "models": ["gpt-oss-120b", "qwen-3.8-27b", "llama-3.3-70b"],
    },
    {
        "id": "sambanova", "name": "SambaNova", "env": "SAMBANOVA_API_KEY",
        "key_url": "https://cloud.sambanova.ai",
        "free_hint": "free tier · zero data retention",
        "model_filter": "curated",
        "models": ["Meta-Llama-3.3-70B-Instruct", "gpt-oss-120b", "gemma-4-31B-it"],
    },
]

PRESET_BY_ID = {p["id"]: p for p in FREE_PROVIDERS}


def preset(provider_id: str) -> dict | None:
    return PRESET_BY_ID.get(provider_id)


def is_builtin(provider_id: str) -> bool:
    return provider_id in PRESET_BY_ID


def select_free_models(provider_id: str, catalog_models: list[str]) -> list[str]:
    """Pick free-tier model IDs for a provider from the live catalog."""
    p = PRESET_BY_ID.get(provider_id)
    available = set(catalog_models)
    if p and p["model_filter"] == "free_suffix":
        free = [m for m in catalog_models if m.endswith(":free") or m == "openrouter/free"]
        # put the openrouter/free router + a couple of strong ones first
        order = ["openrouter/free"]
        free.sort(key=lambda m: (0 if m in order else 1, m))
        return free
    if p and p["model_filter"] == "curated":
        curated = [m for m in p["models"] if m in available]
        if curated:
            return curated
        # fallback: any non-embedding/whisper/guard chat model
        skip = ("whisper", "embed", "guard", "orpheus", "tts")
        return [m for m in catalog_models if not any(s in m.lower() for s in skip)][:6]
    # custom provider: user-supplied models, else everything
    return list(catalog_models)
