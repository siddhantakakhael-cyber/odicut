"use strict";
const $ = (s) => document.querySelector(s);
const chatEl = $("#chat"), emptyEl = $("#empty"), inputEl = $("#input");
let state = null, presets = [], busy = false, logTimer = null;

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  return r.json();
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 3200);
}
function esc(s) { return (s || "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

function renderMarkdownish(text) {
  // minimal: fenced code blocks + inline code
  let h = esc(text);
  h = h.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => `<pre>${code.replace(/\n$/, "")}</pre>`);
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  return h;
}

function addMsg(role, html, meta = "") {
  emptyEl.classList.add("hidden");
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.innerHTML = (meta ? `<div class="meta">${meta}</div>` : "") + html;
  chatEl.appendChild(d);
  chatEl.scrollTop = chatEl.scrollHeight;
  return d;
}

async function loadState() {
  state = await api("/api/state");
  const oc = $("#oc-status");
  oc.textContent = state.running ? "● OpenCode: running" : "● OpenCode: stopped";
  oc.className = "pill " + (state.running ? "on" : "off");
  $("#cur-model").textContent = state.last_used || (state.candidates[0] ? state.candidates[0].key : "no model");
  if (state.start_error) toast("⚠ " + state.start_error.split("\n")[0]);
}

async function sendChat(text) {
  text = (text || inputEl.value).trim();
  if (!text || busy) return;
  if (!state || !state.providers.some(p => p.has_key && p.enabled)) {
    toast("Pehle 🔌 Providers me ek free provider ki key add karo");
    openModal("providers-modal"); return;
  }
  busy = true; inputEl.value = ""; $("#send").disabled = true;
  addMsg("user", esc(text));
  const typing = addMsg("bot", `<span class="typing">⌬ OpenCode soch raha hai… (free model auto-switch on)</span>`);
  // live log polling while working
  clearInterval(logTimer);
  logTimer = setInterval(async () => {
    const lg = await api("/api/logs");
    const lines = lg.text.split("\n").filter(l => l.includes("[SWITCH]") || l.includes("[STEP]"));
    const last = lines[lines.length - 1];
    if (last) typing.querySelector(".typing").textContent = "⌬ " + last.split("] ").slice(1).join("] ");
    if (!$("#logdrawer").classList.contains("hidden")) $("#logtext").textContent = lg.text;
  }, 900);
  try {
    const res = await api("/api/chat", "POST", { text });
    clearInterval(logTimer);
    typing.remove();
    if (res.ok && res.text) {
      let meta = `<span class="model">${esc(res.provider + "/" + res.model)}</span>`;
      if (res.attempts > 1) meta += `<span class="sw">⇄ ${res.attempts} models try hue</span>`;
      if (res.tools && res.tools.length) meta += `<span>🔧 ${res.tools.length} tool calls</span>`;
      addMsg("bot", renderMarkdownish(res.text), meta);
      $("#cur-model").textContent = res.provider + "/" + res.model;
    } else {
      addMsg("err", esc(res.error || "Koi reply nahi mila. Providers check karo."),
        res.switches && res.switches.length ? `<span class="sw">⇄ ${esc(res.switches.join(" · "))}</span>` : "");
    }
  } catch (e) {
    clearInterval(logTimer); typing.remove();
    addMsg("err", "Network error: " + esc(String(e)));
  }
  busy = false; $("#send").disabled = false; inputEl.focus();
  loadState();
}

// ---------------- Providers ----------------
async function openProviders() {
  presets = await api("/api/presets");
  await loadState();
  renderProviders();
  openModal("providers-modal");
}
function renderProviders() {
  const wrap = $("#preset-list"); wrap.innerHTML = "";
  const byId = {}; state.providers.forEach(p => byId[p.id] = p);
  const all = [...presets];
  state.providers.filter(p => !p.builtin).forEach(p => all.push({ id: p.id, name: p.name, free_hint: "custom · " + (p.base_url || ""), key_url: "", custom: true }));
  for (const pr of all) {
    const cur = byId[pr.id] || {};
    const div = document.createElement("div");
    div.className = "preset";
    div.innerHTML = `
      <div class="top">
        <span class="name">${esc(pr.name || pr.id)}</span>
        <button class="toggle ${cur.enabled !== false && cur.has_key ? "on" : ""}" data-toggle="${pr.id}" title="enable/disable"></button>
      </div>
      <div class="hint">${esc(pr.free_hint || "")} ${pr.key_url ? `· <a href="${pr.key_url}" target="_blank">key lo ↗</a>` : ""}</div>
      <div class="keyrow">
        <input type="password" placeholder="${cur.has_key ? "saved: " + esc(cur.masked) : "API key paste karo"}" data-key="${pr.id}">
        <button data-save="${pr.id}">Save</button>
        <button data-test="${pr.id}">Test</button>
        ${cur.has_key ? `<button data-del="${pr.id}">🗑</button>` : ""}
      </div>
      <div class="state" data-state="${pr.id}"></div>`;
    wrap.appendChild(div);
  }
  wrap.querySelectorAll("[data-save]").forEach(b => b.onclick = () => saveKey(b.dataset.save));
  wrap.querySelectorAll("[data-test]").forEach(b => b.onclick = () => testKey(b.dataset.test));
  wrap.querySelectorAll("[data-del]").forEach(b => b.onclick = () => delProvider(b.dataset.del));
  wrap.querySelectorAll("[data-toggle]").forEach(b => b.onclick = () => toggleProvider(b.dataset.toggle));
}
async function saveKey(id) {
  const inp = document.querySelector(`[data-key="${id}"]`);
  const key = inp.value.trim();
  if (!key) { toast("Key khali hai"); return; }
  const st = document.querySelector(`[data-state="${id}"]`);
  st.textContent = "Saving…"; st.className = "state";
  await api("/api/providers", "POST", { id, api_key: key, enabled: true });
  inp.value = ""; st.textContent = "✓ saved"; st.className = "state ok";
  await loadState(); renderProviders(); toast(id + " key save ho gayi");
}
async function testKey(id) {
  const st = document.querySelector(`[data-state="${id}"]`);
  st.textContent = "Testing… (ek chhota prompt)"; st.className = "state";
  const r = await api("/api/providers/test", "POST", { id });
  st.textContent = (r.ok ? "✓ " : "✗ ") + (r.msg || r.error || "");
  st.className = "state " + (r.ok ? "ok" : "bad");
}
async function delProvider(id) {
  await api("/api/providers/" + id, "DELETE");
  await loadState(); renderProviders(); toast(id + " hata diya");
}
async function toggleProvider(id) {
  const cur = state.providers.find(p => p.id === id);
  await api("/api/providers", "POST", { id, enabled: !(cur && cur.enabled !== false) });
  await loadState(); renderProviders();
}
async function addCustom() {
  const id = $("#c-id").trim ? $("#c-id").value.trim() : $("#c-id").value.trim();
  const name = $("#c-name").value.trim(), base = $("#c-base").value.trim();
  const models = $("#c-models").value.split(",").map(s => s.trim()).filter(Boolean);
  const key = $("#c-key").value.trim();
  if (!id || !base || !models.length) { toast("id, base URL aur models zaroori hain"); return; }
  await api("/api/providers", "POST", { id, name: name || id, base_url: base, models, api_key: key, enabled: true });
  ["c-id", "c-name", "c-base", "c-models", "c-key"].forEach(i => $("#" + i).value = "");
  await loadState(); renderProviders(); toast("Custom provider add ho gaya");
}

// ---------------- Settings ----------------
async function openSettings() {
  await loadState();
  $("#s-workspace").value = state.prefs.workspace || "";
  $("#s-ocpath").value = state.prefs.opencode_path || "";
  $("#s-auto").checked = state.router.auto_switch !== false;
  $("#s-cooldown").value = state.router.cooldown_seconds || 60;
  $("#s-attempts").value = state.router.max_attempts || 6;
  $("#s-port").value = state.prefs.port || 8765;
  $("#s-ocport").value = state.prefs.opencode_port || 4096;
  openModal("settings-modal");
}
async function saveSettings() {
  await api("/api/settings", "POST", {
    prefs: { workspace: $("#s-workspace").value.trim(), opencode_path: $("#s-ocpath").value.trim(),
      port: +$("#s-port").value, opencode_port: +$("#s-ocport").value },
    router: { auto_switch: $("#s-auto").checked, cooldown_seconds: +$("#s-cooldown").value,
      max_attempts: +$("#s-attempts").value },
  });
  closeModal(); await loadState(); toast("Settings save ho gayi ✓");
}

// ---------------- Logs ----------------
async function refreshLogs() { $("#logtext").textContent = (await api("/api/logs")).text;
  $("#logtext").scrollTop = $("#logtext").scrollHeight; }
function toggleLogs() { const d = $("#logdrawer"); d.classList.toggle("hidden");
  if (!d.classList.contains("hidden")) { refreshLogs(); clearInterval(logTimer);
    logTimer = setInterval(refreshLogs, 1500); } else clearInterval(logTimer); }

// ---------------- modal helpers ----------------
function openModal(id) { $("#" + id).classList.remove("hidden"); }
function closeModal() { document.querySelectorAll(".modal").forEach(m => m.classList.add("hidden")); }

// ---------------- wiring ----------------
$("#send").onclick = () => sendChat();
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
$("#btn-new").onclick = async () => { await api("/api/session/new", "POST"); chatEl.innerHTML = "";
  emptyEl.classList.remove("hidden"); toast("Naya chat shuru"); };
$("#btn-providers").onclick = openProviders;
$("#btn-settings").onclick = openSettings;
$("#btn-logs").onclick = toggleLogs;
$("#s-save").onclick = saveSettings;
$("#c-add").onclick = addCustom;
$("#log-clear").onclick = async () => { await api("/api/logs/clear", "POST"); refreshLogs(); };
$("#log-copy").onclick = async () => { navigator.clipboard.writeText($("#logtext").textContent); toast("Log copy ho gaya"); };
document.querySelectorAll("[data-close]").forEach(b => b.onclick = closeModal);
document.querySelectorAll(".modal").forEach(m => m.addEventListener("click", e => { if (e.target === m) closeModal(); }));
document.querySelectorAll(".ex").forEach(b => b.onclick = () => sendChat(b.textContent));

loadState();
setInterval(loadState, 8000);
