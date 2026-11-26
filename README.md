# ⌬ OpenCode Hub

**OpenCode ka GUI version — free models ke saath, Windows .exe ke roop mein.**

OpenCode Hub ek native Windows desktop app hai jo [OpenCode](https://opencode.ai)
ko background mein chalata hai aur aapko ek dark-mode chat GUI deta hai. Sabse
khaas baat: jab ek **free model rate-limit** ho jaata hai, app **automatically
doosre free model pe switch** kar deta hai — bina kuch kiye. Aap jitne chahe utne
free providers (Gemini, Groq, OpenRouter, Mistral, Cerebras, SambaNova) ya koi bhi
custom OpenAI-compatible endpoint add kar sakte ho.

> 🪟 **Sirf Windows ke liye.** Koi Ubuntu / WSL / Linux setup nahi chahiye —
> OpenCode binary app ke andar bundled hai aur native Windows pe chalta hai.

---

## ✨ Features

- **Proper Windows .exe** — double-click karke chalne wala native GUI app
  (WebView2 window), CLI nahi.
- **OpenCode background mein** — `opencode serve` app khud start/stop karta hai;
  binary bundled hai (npm install ki zaroorat nahi).
- **Free-model auto-switch** — 429 / quota / rate-limit / auth error pe agle free
  model pe transparent failover, cooldown ke saath. Har switch log mein dikhta hai.
- **6 free providers ready** — OpenRouter (`:free`), Groq, Google Gemini, Mistral
  Codestral, Cerebras, SambaNova. Sirf API key paste karo.
- **Custom providers** — koi bhi OpenAI-compatible base URL + models add karo
  (local Ollama, LM Studio, apna endpoint, etc.).
- **`small_model` bhi free** — title-generation jaise background tasks bhi free
  model use karte hain, paid default nahi.
- **Privacy** — keys sirf local disk pe (`%APPDATA%/OpenCodeHub`), logs mein har
  key **redact** hoti hai. Koi telemetry nahi.

---

## 📥 Download & Run (Windows)

1. Repo ke **Actions** tab → latest **Windows EXE** workflow → neeche
   **Artifacts** se `OpenCodeHub-windows-x64.zip` download karo.
2. Zip extract karo → `OpenCodeHub.exe` double-click karo.
3. Pehli baar: **🔌 Providers** button → kisi free provider ki API key paste karo
   → **Save** → **Test** (optional).
4. Chat box mein coding task likho → **Send**. Bas.

**Requirement:** Windows 10/11 with **WebView2 runtime** (zyadatar systems pe
pehle se hota hai; na ho to [Microsoft se free install](https://developer.microsoft.com/microsoft-edge/webview2/)
kar lo — app browser fallback bhi de deta hai).

### Free API keys kahan se milengi

| Provider | Key link | Free tier |
|---|---|---|
| Google Gemini | https://aistudio.google.com/apikey | ~15 RPM, 1500 req/day |
| Groq | https://console.groq.com/keys | ~30 RPM, 1000 req/day, fast |
| OpenRouter | https://openrouter.ai/keys | `:free` models, 50–1000 req/day |
| Mistral | https://console.mistral.ai/api-keys | Experiment plan (Codestral) |
| Cerebras | https://cloud.cerebras.ai | free tier |
| SambaNova | https://cloud.sambanova.ai | free tier |

> Jitne zyada providers add karoge, auto-switch utna reliable hoga.

---

## 🔄 Auto-switch kaise kaam karta hai

1. App enabled providers ke **free models** ki ek ordered list banata hai
   (OpenCode ke live catalog se verify karke — koi stale model ID nahi).
2. Har prompt pehle model pe jaata hai. Agar reply mein **rate-limit / quota /
   auth / model-not-found** error aaye, toh woh model **cooldown** mein chala
   jaata hai aur app **agla free model** try karta hai (max 6 attempts tak).
3. Jo model reply de deta hai, wahi current model ban jaata hai. Saare switches
   **🧾 Logs** panel mein dikhte hain.

Settings (⚙) mein auto-switch on/off, cooldown seconds, max attempts, workspace
folder, aur ports change kar sakte ho.

---

## 🛠️ Build from source (Windows)

```powershell
# Python 3.11+ chahiye
pip install -r requirements.txt -r requirements-build.txt

# OpenCode Windows binary vendor/ mein lao (exe mein bundle hoga)
python scripts/fetch_opencode.py --pkg opencode-windows-x64 --dest vendor/opencode.exe

# .exe build karo
pyinstaller ochub.spec --noconfirm --clean
# Output: dist/OpenCodeHub/OpenCodeHub.exe
```

### Dev mode (koi bhi OS)

```bash
pip install -r requirements.txt
python run.py --browser     # browser mein UI kholta hai
python run.py               # native window (agar webview available ho)
python run.py --server-only # sirf server (headless)
```

OpenCode binary auto-download ho jaata hai (npm registry se) agar bundled/PATH
pe na ho. Settings mein custom `opencode` path bhi de sakte ho.

### Tests

```bash
python -m unittest discover -s tests -v        # 41 unit + API tests
python tests/e2e_smoke.py /path/to/opencode    # real-binary E2E (auto-switch)
```

---

## 📁 Config & privacy

- Config + keys: `%APPDATA%/OpenCodeHub/config.json` (Windows), permissions `0600`.
- Managed OpenCode config: `%APPDATA%/OpenCodeHub/opencode.json`.
- Logs: `%APPDATA%/OpenCodeHub/hub.log` (keys redacted).
- Workspace (OpenCode jis folder pe kaam karta hai): default `~/OpenCodeHub`,
  Settings mein change karo.

Sab kuch **local** hai. Keys kahin upload nahi hoti. Logs mein API keys
`***REDACTED***` ho jaati hain.

---

## 🧱 Architecture

```
OpenCodeHub.exe
  ├─ run.py            entry: hub server + native window (pywebview/WebView2)
  ├─ ochub/
  │   ├─ config.py     persistent config + key masking
  │   ├─ providers.py  free-provider presets + model selection
  │   ├─ opencode.py   binary manage + background `opencode serve` + HTTP client
  │   ├─ router.py     free-model auto-switch (failover + cooldown)
  │   ├─ server.py     hub HTTP API + orchestration
  │   ├─ logbus.py     redacting log bus
  │   └─ web/          dark-mode GUI (HTML/CSS/JS)
  └─ vendor/opencode.exe   bundled OpenCode binary
```

Hub ek local HTTP server (`127.0.0.1:8765`) hai jo GUI serve karta hai aur
OpenCode (`127.0.0.1:4096`) ko manage karta hai. GUI sirf hub se baat karta hai.
