/* openNoClaw v0.3 */

// ── State ─────────────────────────────────────────────────────
let token = "";
let currentProfile = null;
let ws = null;
let isStreaming = false;
let currentBubble = null;
let currentBubbleAgent = null;
let sessionCostUsd = 0;
let backendInfo = null;
let activeSection = "chat";
let attachedFiles = [];  // [{name, mime_type, data (base64) | content (text)}]
let botSettings = { name: "openNoClaw", avatar: "bot-nexus" };
let activeSessId = null;
let selectedCronId = null;
let cachedCrons = [];

const BOT_AVATARS = [
  { id: "bot-nexus",  label: "Nexus"  },
  { id: "bot-nova",   label: "Nova"   },
  { id: "bot-orbit",  label: "Orbit"  },
  { id: "bot-pixel",  label: "Pixel"  },
  { id: "bot-spark",  label: "Spark"  },
  { id: "bot-zen",    label: "Zen"    },
];

const EUR_RATE = 0.92;
const AVATARS = ["robot-default", "robot-seo", "robot-cm", "robot-dev", "robot-analyst", "robot-writer"];

// ── DOM ───────────────────────────────────────────────────────
const loginScreen       = document.getElementById("login-screen");
const profilePicker     = document.getElementById("profile-picker");
const loginPwArea       = document.getElementById("login-password-area");
const loginPwInput      = document.getElementById("login-pw-input");
const loginPwBtn        = document.getElementById("login-pw-btn");
const loginBack         = document.getElementById("login-back-btn");
const loginError        = document.getElementById("login-error");
const app               = document.getElementById("app");
const messages          = document.getElementById("messages");
const input             = document.getElementById("input");
const sendBtn           = document.getElementById("send-btn");
const clearBtn          = document.getElementById("clear-btn");
const navItems          = document.querySelectorAll(".nav-item");
const sections          = document.querySelectorAll(".section");
const profileBtn        = document.getElementById("profile-btn");
const profileNameNav    = document.getElementById("profile-name-nav");
const profileAvatar     = document.getElementById("profile-avatar");
const backendBadge      = document.getElementById("backend-badge");
const costDisplay       = document.getElementById("cost-display");
const chatProfileLabel  = document.getElementById("chat-profile-label");
const reloadSkillsBtn   = document.getElementById("reload-skills-btn");
const profilePanel      = document.getElementById("profile-panel");
const profileOverlay    = document.getElementById("profile-overlay");
const profilePanelClose = document.getElementById("profile-panel-close");
const profilePanelBody  = document.getElementById("profile-panel-body");
const skillsGrid        = document.getElementById("skills-grid");
const agentsGrid        = document.getElementById("agents-grid");
const attachBtn         = document.getElementById("attach-btn");
const attachInput       = document.getElementById("attach-input");
const attachPreview     = document.getElementById("attach-preview");

// ── Boot ───────────────────────────────────────────────────────
async function boot() {
  // Always pre-load profiles so window._profiles is available everywhere (user picker, etc.)
  const profiles = await safeFetch("/api/profiles") || [];
  window._profiles = profiles;

  const saved = sessionStorage.getItem("onc_token");
  if (saved) {
    const resp = await safeFetch(`/api/me?token=${enc(saved)}`);
    if (resp && resp.id) {
      token = saved;
      currentProfile = resp;
      enterApp();
      return;
    }
    sessionStorage.removeItem("onc_token");
  }
  showLoginScreen();
}

// ── Login ───────────────────────────────────────────────────────
async function showLoginScreen() {
  loginScreen.classList.remove("hidden");
  app.classList.add("hidden");

  const profiles = await safeFetch("/api/profiles") || [];
  window._profiles = profiles;
  profilePicker.innerHTML = "";
  loginPwArea.classList.add("hidden");

  if (profiles.length === 0) {
    token = "default:";
    currentProfile = { id: "default", name: "User", admin: true };
    enterApp();
    return;
  }

  let pendingProfile = null;

  profiles.forEach(p => {
    const btn = document.createElement("button");
    btn.className = "profile-pick-btn";
    btn.innerHTML = `
      <div class="avatar">${p.name.charAt(0).toUpperCase()}</div>
      <span>${p.name}</span>
    `;
    btn.addEventListener("click", () => {
      if (p.has_password) {
        pendingProfile = p;
        profilePicker.classList.add("hidden");
        loginPwArea.classList.remove("hidden");
        loginPwInput.focus();
        loginError.classList.add("hidden");
      } else {
        attemptLogin(p.id, "");
      }
    });
    profilePicker.appendChild(btn);
  });

  loginPwBtn.onclick = () => attemptLogin(pendingProfile.id, loginPwInput.value);
  loginPwInput.onkeydown = (e) => { if (e.key === "Enter") loginPwBtn.click(); };
  loginBack.onclick = () => {
    loginPwArea.classList.add("hidden");
    profilePicker.classList.remove("hidden");
    loginPwInput.value = "";
    pendingProfile = null;
  };
}

async function attemptLogin(profileId, password) {
  const t = `${profileId}:${password}`;
  const resp = await safeFetch(`/api/me?token=${enc(t)}`);
  if (resp && resp.id) {
    token = t;
    currentProfile = resp;
    sessionStorage.setItem("onc_token", token);
    enterApp();
  } else {
    loginError.classList.remove("hidden");
  }
}

// ── Enter app ─────────────────────────────────────────────────
async function enterApp() {
  loginScreen.classList.add("hidden");
  app.classList.remove("hidden");

  if (currentProfile.admin) document.body.classList.add("is-admin");
  else document.body.classList.remove("is-admin");

  profileNameNav.textContent = currentProfile.name;
  profileAvatar.textContent = currentProfile.name.charAt(0).toUpperCase();
  chatProfileLabel.textContent = `Chat · ${currentProfile.name}`;

  await loadBackendInfo();
  await loadBotSettings();
  connectWS(currentProfile.id);
  loadSkills();
  loadCrons();
  loadSessions();
  updateBrowserBadge();
  setInterval(loadCrons, 30_000);
  setInterval(updateBrowserBadge, 10_000);
}

// ── Browser badge ──────────────────────────────────────────────
async function updateBrowserBadge() {
  const badge = document.getElementById("browser-badge");
  const urlEl = document.getElementById("browser-badge-url");
  if (!badge) return;
  const data = await safeFetch(`/api/browser/status?token=${enc(token)}`);
  if (data && data.has_session) {
    let label = data.url || "";
    try { label = new URL(data.url).hostname; } catch (_) {}
    if (urlEl) urlEl.textContent = label;
    badge.style.display = "";
  } else {
    badge.style.display = "none";
  }
}

// ── Bot settings ───────────────────────────────────────────────
async function loadBotSettings() {
  const data = await safeFetch(`/api/bot?token=${enc(token)}`);
  if (!data) return;
  botSettings = data;
  applyBotSettings();
}

function applyBotSettings() {
  const avatarNav = document.getElementById("bot-avatar-nav");
  const nameNav   = document.getElementById("bot-name-nav");
  if (avatarNav) avatarNav.src = `/static/avatars/${botSettings.avatar}.svg`;
  if (nameNav)   nameNav.textContent = botSettings.name;
  document.title = botSettings.name;
}

// ── Backend badge ──────────────────────────────────────────────
async function loadBackendInfo() {
  backendInfo = await safeFetch(`/api/backend?token=${enc(token)}`);
  if (!backendInfo) return;

  if (backendInfo.is_api) {
    backendBadge.className = "backend-badge api";
    const shortModel = backendInfo.model
      .replace("claude-", "")
      .replace("-20251001", "")
      .replace("-20250219", "");
    backendBadge.innerHTML = `API<br>${shortModel}`;
    backendBadge.title = `Anthropic API · ${backendInfo.model}`;
  } else {
    backendBadge.className = "backend-badge cli";
    backendBadge.innerHTML = `Forfait<br>CLI`;
    backendBadge.title = "Claude Code CLI (subscription)";
  }
}

function updateCostDisplay(addedUsd) {
  if (!backendInfo?.is_api) return;
  sessionCostUsd += addedUsd;
  const eur = sessionCostUsd * EUR_RATE;
  costDisplay.classList.remove("hidden");
  costDisplay.innerHTML = `session<br><span class="cost-val">$${sessionCostUsd.toFixed(4)}</span><br><span class="cost-val">€${eur.toFixed(4)}</span>`;
}

// ── WebSocket ─────────────────────────────────────────────────
function connectWS(userId) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${userId}`);

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ type: "auth", token }));
  });

  ws.addEventListener("message", (evt) => {
    handleWS(JSON.parse(evt.data));
  });

  ws.addEventListener("close", () => {
    setTimeout(() => connectWS(userId), 3000);
  });
}

function handleWS(msg) {
  switch (msg.type) {
    case "connected":
      break;

    case "history":
      messages.innerHTML = "";
      msg.messages.forEach(m => addBubble(m.role, m.content));
      scrollBottom();
      loadSessions();
      break;

    case "user_message":
      break;

    case "agent_start":
      currentBubbleAgent = { id: msg.agent_id, name: msg.agent_name, avatar: msg.agent_avatar };
      break;

    case "stream_start":
      isStreaming = true;
      sendBtn.disabled = true;
      currentBubble = addBubble("assistant", "", true, currentBubbleAgent);
      break;

    case "chunk":
      if (currentBubble) {
        const body = currentBubble.querySelector(".body");
        if (body.querySelector(".typing-dots")) {
          body.innerHTML = "";
        }
        body.textContent += msg.content;
        scrollBottom();
      }
      break;

    case "stream_end":
      isStreaming = false;
      sendBtn.disabled = false;
      if (currentBubble) {
        currentBubble.classList.remove("streaming");
        if (msg.usage && backendInfo?.is_api) {
          const footer = document.createElement("div");
          footer.className = "msg-footer";
          const cost = msg.usage.cost_usd;
          const eur = msg.usage.cost_eur;
          footer.textContent = `${msg.usage.input_tokens}in · ${msg.usage.output_tokens}out · $${cost.toFixed(5)} / €${eur.toFixed(5)}`;
          currentBubble.appendChild(footer);
          updateCostDisplay(cost);
        }
        if (msg.agent_id) {
          const badge = document.createElement("div");
          badge.className = "agent-badge";
          badge.innerHTML = `
            <img src="/static/avatars/${msg.agent_avatar}.svg" alt="" class="agent-badge-icon" onerror="this.style.display='none'" />
            <span>${msg.agent_name}</span>
          `;
          currentBubble.querySelector(".sender").appendChild(badge);
        }
        // Parse meta-create blocks
        renderMetaActions(currentBubble, msg.content || "");
        currentBubble = null;
        currentBubbleAgent = null;
      }
      scrollBottom();
      break;

    case "error":
      isStreaming = false;
      sendBtn.disabled = false;
      currentBubble = null;
      currentBubbleAgent = null;
      if (msg.message === "Unauthorized" || msg.message === "Forbidden") {
        sessionStorage.removeItem("onc_token");
        location.reload();
      } else {
        addSysMsg(`Error: ${msg.message}`);
      }
      break;
  }
}

// ── Meta-create blocks ─────────────────────────────────────────
function renderMetaActions(bubble, content) {
  if (!currentProfile?.admin) return;
  const blockRe = /```(create-agent|create-skill|send-notification|run-action)\s*\n([\s\S]*?)```/g;
  let match;
  while ((match = blockRe.exec(content)) !== null) {
    const type = match[1];
    let data;
    try { data = JSON.parse(match[2]); } catch { continue; }

    if (type === "run-action") {
      const action = data.action || "";
      if (!action) continue;
      const bodyEl2 = bubble.querySelector(".body") || bubble;
      const status2 = document.createElement("div");
      status2.className = "browser-action-status";
      status2.textContent = `Running ${action}…`;
      bodyEl2.appendChild(status2);
      // Map action name to endpoint
      const endpoint = `/api/actions/${action}`;

      if (action.startsWith("browser-")) {
        // Browser actions: show screenshot result
        safeFetch(`${endpoint}?token=${enc(token)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        }).then(res => {
          if (res?.ok !== false) {
            status2.style.color = "var(--ok)";
            status2.textContent = `✓ ${action}` + (res?.url ? ` — ${res.url}` : "");
            if (res?.screenshot_b64) {
              const img = document.createElement("img");
              img.src = `data:image/png;base64,${res.screenshot_b64}`;
              img.className = "browser-chat-screenshot";
              img.title = "Click to open in browser tab";
              img.addEventListener("click", () => {
                const w = window.open();
                if (w) { w.document.write(`<img src="${img.src}" style="max-width:100%">`); }
              });
              bodyEl2.appendChild(img);
            }
          } else {
            status2.style.color = "var(--err)";
            status2.textContent = `✗ ${action}: ${res?.message || "failed"}`;
          }
        }).catch(err => {
          status2.style.color = "var(--err)";
          status2.textContent = `✗ ${action} error: ${err.message}`;
        });
        continue;
      }

      safeFetch(`${endpoint}?token=${enc(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      }).then(res => {
        if (res?.ok === true || (res && !("ok" in res))) {
          status2.style.color = "var(--ok)";
          status2.textContent = res?.message || `✓ ${action} done`;
        } else {
          status2.style.color = "var(--err)";
          status2.textContent = res?.message || res?.detail || `✗ ${action} failed`;
        }
      }).catch(err => {
        status2.style.color = "var(--err)";
        status2.textContent = `✗ ${action} error: ${err.message}`;
      });
      continue;
    }

    if (type === "send-notification") {
      const channel = data.channel || "telegram";
      const message = data.message || "";
      // Send immediately, no confirmation needed
      const body = bubble.querySelector(".body") || bubble;
      const status = document.createElement("div");
      status.style.cssText = "font-size:.78rem;font-weight:600;margin-top:.4rem;color:var(--text-dim)";
      status.textContent = `Sending via ${channel}…`;
      body.appendChild(status);
      safeFetch(`/api/connexions/notify?token=${enc(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel, message }),
      }).then(res => {
        if (res?.ok !== false && res !== null) {
          status.textContent = `✓ Sent via ${channel}`;
          status.style.color = "var(--ok)";
        } else {
          status.textContent = `✗ ${channel} send failed — check connexion settings`;
          status.style.color = "var(--err)";
        }
      });
      continue;
    }

    const btn = document.createElement("button");
    btn.className = "meta-create-btn";
    btn.textContent = type === "create-agent"
      ? `✦ Create agent "${data.name}"`
      : `✦ Create skill "${data.name}"`;

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Creating…";
      let res;
      if (type === "create-agent") {
        res = await safeFetch(`/api/agents?token=${enc(token)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
      } else {
        res = await safeFetch(`/api/admin/skills?token=${enc(token)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
      }
      if (res) {
        btn.textContent = "✓ Created!";
        btn.style.background = "var(--ok)";
      } else {
        btn.textContent = "✗ Failed";
        btn.style.background = "var(--err)";
        btn.disabled = false;
      }
    });

    const bodyEl = bubble.querySelector(".body") || bubble;
    bodyEl.appendChild(btn);
  }
}

// ── Sessions ───────────────────────────────────────────────────
async function loadSessions() {
  if (!currentProfile) return;
  const sessBar = document.getElementById("sessions-list");
  if (!sessBar) return;
  const data = await safeFetch(`/api/sessions/${currentProfile.id}?token=${enc(token)}`);
  if (!data) return;
  activeSessId = data.active_session;
  sessBar.innerHTML = "";
  (data.sessions || []).forEach(sess => {
    const chip = document.createElement("div");
    chip.className = "session-chip" + (sess.id === activeSessId ? " active" : "");
    chip.title = `${sess.title} (${sess.message_count} msgs)`;
    chip.innerHTML = `<span style="overflow:hidden;text-overflow:ellipsis;flex:1">${escHtml(sess.title)}</span>`;
    if (sess.id === activeSessId) {
      const del = document.createElement("button");
      del.className = "session-chip-delete";
      del.title = "Delete session";
      del.textContent = "×";
      del.addEventListener("click", e => {
        e.stopPropagation();
        deleteSession(sess.id);
      });
      chip.appendChild(del);
    }
    chip.style.display = "inline-flex";
    chip.style.alignItems = "center";
    chip.addEventListener("click", () => switchSession(sess.id));
    sessBar.appendChild(chip);
  });
}

async function newChat() {
  if (!currentProfile) return;
  const data = await safeFetch(`/api/sessions/${currentProfile.id}?token=${enc(token)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!data) return;
  activeSessId = data.session_id;
  messages.innerHTML = "";
  sessionCostUsd = 0;
  costDisplay.classList.add("hidden");
  await loadSessions();
}

async function switchSession(sessId) {
  if (!currentProfile || sessId === activeSessId) return;
  const res = await safeFetch(
    `/api/sessions/${currentProfile.id}/${sessId}/activate?token=${enc(token)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
  );
  if (!res) return;
  activeSessId = sessId;
  const hist = await safeFetch(`/api/history/${currentProfile.id}?token=${enc(token)}`);
  messages.innerHTML = "";
  (hist || []).forEach(m => addBubble(m.role, m.content));
  scrollBottom();
  sessionCostUsd = 0;
  costDisplay.classList.add("hidden");
  await loadSessions();
}

async function deleteSession(sessId) {
  if (!currentProfile) return;
  if (!confirm("Delete this conversation?")) return;
  await safeFetch(
    `/api/sessions/${currentProfile.id}/${sessId}?token=${enc(token)}`,
    { method: "DELETE" }
  );
  // Reload history and sessions
  const hist = await safeFetch(`/api/history/${currentProfile.id}?token=${enc(token)}`);
  messages.innerHTML = "";
  (hist || []).forEach(m => addBubble(m.role, m.content));
  scrollBottom();
  await loadSessions();
}

const newChatBtn = document.getElementById("new-chat-btn");
if (newChatBtn) newChatBtn.addEventListener("click", newChat);

// ── Screenshot button ──────────────────────────────────────────
const screenshotBtn = document.getElementById("screenshot-btn");
if (screenshotBtn) {
  screenshotBtn.addEventListener("click", async () => {
    const res = await safeFetch(`/api/browser/screenshot?token=${enc(token)}`);
    if (!res || !res.screenshot) {
      addSysMsg("No active browser session — open the Browser tab first");
      return;
    }
    const f = {
      name: `screenshot-${Date.now()}.png`,
      mime_type: "image/png",
      data: res.screenshot,
      dataUrl: `data:image/png;base64,${res.screenshot}`,
      isImage: true,
    };
    attachedFiles.push(f);
    renderAttachPreview();
  });
}

// ── Attach ─────────────────────────────────────────────────────
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];

function renderAttachPreview() {
  if (!attachPreview) return;
  attachPreview.innerHTML = "";
  attachedFiles.forEach((f, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    if (f.isImage && f.dataUrl) {
      const img = document.createElement("img");
      img.className = "attach-chip-img";
      img.src = f.dataUrl;
      chip.appendChild(img);
    }
    const nameEl = document.createElement("span");
    nameEl.className = "attach-chip-name";
    nameEl.title = f.name;
    nameEl.textContent = f.name;
    chip.appendChild(nameEl);
    const rm = document.createElement("button");
    rm.className = "attach-chip-remove";
    rm.textContent = "×";
    rm.addEventListener("click", () => { attachedFiles.splice(idx, 1); renderAttachPreview(); });
    chip.appendChild(rm);
    attachPreview.appendChild(chip);
  });
}

async function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    if (IMAGE_TYPES.includes(file.type)) {
      reader.onload = e => {
        const dataUrl = e.target.result;
        const base64 = dataUrl.split(",")[1];
        resolve({ name: file.name, mime_type: file.type, data: base64, dataUrl, isImage: true });
      };
      reader.readAsDataURL(file);
    } else {
      reader.onload = e => resolve({ name: file.name, mime_type: file.type || "text/plain", content: e.target.result, isImage: false });
      reader.readAsText(file);
    }
    reader.onerror = reject;
  });
}

if (attachBtn) {
  attachBtn.addEventListener("click", () => attachInput && attachInput.click());
}
if (attachInput) {
  attachInput.addEventListener("change", async () => {
    for (const file of attachInput.files) {
      try {
        const f = await readFile(file);
        attachedFiles.push(f);
      } catch (e) { console.warn("Could not read file", file.name, e); }
    }
    attachInput.value = "";
    renderAttachPreview();
  });
}

// ── Send ───────────────────────────────────────────────────────
function send() {
  const text = input.value.trim();
  const hasAttachments = attachedFiles.length > 0;
  if ((!text && !hasAttachments) || isStreaming || !ws || ws.readyState !== WebSocket.OPEN) return;

  // Display in chat: show file names alongside message
  const displayText = hasAttachments
    ? attachedFiles.map(f => `📎 ${f.name}`).join("  ") + (text ? "\n" + text : "")
    : text;
  addBubble("user", displayText);

  const payload = { type: "message", content: text };
  if (hasAttachments) {
    payload.attachments = attachedFiles.map(f => f.isImage
      ? { name: f.name, mime_type: f.mime_type, data: f.data, is_image: true }
      : { name: f.name, mime_type: f.mime_type, content: f.content, is_image: false }
    );
  }

  ws.send(JSON.stringify(payload));
  input.value = "";
  attachedFiles = [];
  renderAttachPreview();
  autoResize();
  scrollBottom();
}

sendBtn.addEventListener("click", send);
input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
input.addEventListener("input", autoResize);
function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 140) + "px";
}

clearBtn.addEventListener("click", async () => {
  if (!confirm("Clear conversation?")) return;
  await safeFetch(`/api/history/${currentProfile.id}?token=${enc(token)}`, { method: "DELETE" });
  messages.innerHTML = "";
  sessionCostUsd = 0;
  costDisplay.classList.add("hidden");
  loadSessions();
});

// ── Bubbles ────────────────────────────────────────────────────
function addBubble(role, content, streaming = false, agent = null) {
  const div = document.createElement("div");
  div.className = `message ${role}${streaming ? " streaming" : ""}`;

  const sender = document.createElement("div");
  sender.className = "sender";
  if (role === "assistant") {
    const av = document.createElement("img");
    av.className = "sender-avatar";
    av.src = `/static/avatars/${botSettings.avatar}.svg`;
    av.alt = "";
    sender.appendChild(av);
    const nameSpan = document.createElement("span");
    nameSpan.textContent = botSettings.name;
    sender.appendChild(nameSpan);
  } else {
    sender.textContent = currentProfile?.name || "You";
  }
  div.appendChild(sender);

  const body = document.createElement("div");
  body.className = "body";
  if (streaming && !content) {
    body.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  } else {
    body.textContent = content;
  }
  div.appendChild(body);

  messages.appendChild(div);
  return div;
}

function addSysMsg(text) {
  const div = document.createElement("div");
  Object.assign(div.style, { textAlign:"center", color:"var(--text-dim)", fontSize:".78rem", padding:".3rem" });
  div.textContent = text;
  messages.appendChild(div);
  scrollBottom();
}

function scrollBottom() { messages.scrollTop = messages.scrollHeight; }

// ── Nav ────────────────────────────────────────────────────────
navItems.forEach(btn => {
  btn.addEventListener("click", () => {
    const s = btn.dataset.section;
    navItems.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    sections.forEach(sec => sec.classList.remove("active"));
    document.getElementById(`section-${s}`).classList.add("active");
    activeSection = s;
    if (s === "skills") loadSkills();
    if (s === "automation") loadCrons();
    if (s === "agents") loadAgents();
    if (s === "settings") initSettingsTabs();
    if (s === "connexions") loadConnexions();
    if (s === "browser") checkBrowserSession();
  });
});

// ── Skills ─────────────────────────────────────────────────────
async function loadSkills() {
  const data = await safeFetch(`/api/skills?token=${enc(token)}`);
  const skills = data?.skills || [];
  if (skills.length === 0) {
    skillsGrid.innerHTML = '<div class="skills-empty">No skills loaded.<br>Add a SKILL.md file in the skills/ directory.</div>';
    return;
  }

  if (currentProfile?.admin) {
    skillsGrid.innerHTML = '<div class="skills-loading">Loading…</div>';
    const cards = await Promise.all(skills.map(async name => {
      const [mdData, pyData] = await Promise.all([
        safeFetch(`/api/admin/skills/${encodeURIComponent(name)}/content?token=${enc(token)}`),
        safeFetch(`/api/admin/skills/${encodeURIComponent(name)}/script?token=${enc(token)}`),
      ]);
      return { name, md: mdData?.content || "", py: pyData?.content || "", hasPy: pyData?.exists || false };
    }));

    skillsGrid.innerHTML = cards.map(s => `
      <div class="skill-card skill-card--editor">
        <div class="skill-card-header">
          <div class="skill-name">${s.name}</div>
          <div class="skill-file-tabs">
            <button class="skill-tab active" data-skill="${s.name}" data-file="md">SKILL.md</button>
            <button class="skill-tab" data-skill="${s.name}" data-file="py">skill.py${s.hasPy ? "" : " +"}</button>
          </div>
        </div>
        <textarea class="skill-editor" id="skill-md-${s.name}" rows="8">${escHtml(s.md)}</textarea>
        <textarea class="skill-editor hidden" id="skill-py-${s.name}" rows="8" placeholder="# Python script (optional)">${escHtml(s.py)}</textarea>
        <div class="skill-card-footer">
          <button class="btn-secondary skill-save-btn" data-skill="${s.name}">Save</button>
          <span class="skill-save-msg hidden" id="skill-msg-${s.name}"></span>
        </div>
      </div>
    `).join("");

    // Tab switching
    document.querySelectorAll(".skill-tab").forEach(tab => {
      tab.addEventListener("click", () => {
        const name = tab.dataset.skill;
        const file = tab.dataset.file;
        document.querySelectorAll(`.skill-tab[data-skill="${name}"]`).forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(`skill-md-${name}`).classList.toggle("hidden", file !== "md");
        document.getElementById(`skill-py-${name}`).classList.toggle("hidden", file !== "py");
      });
    });

    // Save buttons
    document.querySelectorAll(".skill-save-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.skill;
        const activeMd = !document.getElementById(`skill-md-${name}`).classList.contains("hidden");
        const msg = document.getElementById(`skill-msg-${name}`);
        btn.disabled = true; btn.textContent = "Saving…";

        let res;
        if (activeMd) {
          res = await safeFetch(`/api/admin/skills/${encodeURIComponent(name)}/update?token=${enc(token)}`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: document.getElementById(`skill-md-${name}`).value }),
          });
        } else {
          res = await safeFetch(`/api/admin/skills/${encodeURIComponent(name)}/script?token=${enc(token)}`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: document.getElementById(`skill-py-${name}`).value }),
          });
        }
        btn.disabled = false; btn.textContent = "Save";
        msg.classList.remove("hidden");
        msg.style.color = res?.status === "ok" ? "var(--ok)" : "var(--err)";
        msg.textContent = res?.status === "ok" ? "✓ Saved" : "✗ Failed";
        setTimeout(() => msg.classList.add("hidden"), 3000);
      });
    });
  } else {
    skillsGrid.innerHTML = skills.map(s => `
      <div class="skill-card">
        <div class="skill-name">${s}</div>
        <div class="skill-badge">active</div>
      </div>
    `).join("");
  }
}

reloadSkillsBtn.addEventListener("click", async () => {
  reloadSkillsBtn.textContent = "Reloading…";
  await safeFetch(`/api/skills/reload?token=${enc(token)}`, { method: "POST" });
  await loadSkills();
  reloadSkillsBtn.textContent = "Reload";
});

// ── Crons ──────────────────────────────────────────────────────
function fmtCronTime(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (isToday) return time;
  const date = d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
  return `${date} ${time}`;
}

const NOTIFY_ICONS = { telegram: "✈", email: "✉" };

function renderNotifyBadges(channels) {
  if (!channels || channels.length === 0) return `<span class="cron-notify-none">no notify</span>`;
  return channels.map(ch =>
    `<span class="cron-notify-badge cron-notify-${ch}">${NOTIFY_ICONS[ch] || ch} ${ch}</span>`
  ).join("");
}

function parseCronSchedule(schedule) {
  const parts = schedule.trim().split(/\s+/);
  if (parts.length < 5) return { label: schedule, sortKey: 9999 };
  const [min, hour, , , dow] = parts;

  // Every N minutes
  if (min.startsWith("*/")) {
    return { label: `Every ${min.slice(2)} min`, sortKey: -1 };
  }

  const h = parseInt(hour, 10);
  const m = parseInt(min, 10);
  const timeStr = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;

  const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  let freq;
  if (dow === "*")       freq = "Daily";
  else if (dow === "1-5") freq = "Weekdays";
  else if (dow === "1-6") freq = "Mon–Sat";
  else {
    freq = dow.split(",").map(d => DAY[parseInt(d, 10)] ?? d).join(", ");
  }

  return { label: `${freq} · ${timeStr}`, sortKey: h * 60 + m };
}

async function loadCrons() {
  const cronsRaw = await safeFetch(`/api/crons?token=${enc(token)}`);
  if (!cronsRaw) return;
  cachedCrons = [...cronsRaw].sort((a, b) => {
    const sa = parseCronSchedule(a.schedule).sortKey;
    const sb = parseCronSchedule(b.schedule).sortKey;
    return sa - sb;
  });
  renderCronSidebar();
  if (!selectedCronId && cachedCrons.length > 0) {
    selectedCronId = cachedCrons[0].id;
  }
  if (selectedCronId) {
    const cron = cachedCrons.find(c => c.id === selectedCronId);
    if (cron) renderCronPipeline(cron);
  }
}

function renderCronSidebar() {
  const sidebar = document.getElementById("crons-sidebar");
  if (!sidebar) return;
  if (cachedCrons.length === 0) {
    sidebar.innerHTML = '<div class="auto-sidebar-title">No crons</div>';
    return;
  }
  sidebar.innerHTML = '<div class="auto-sidebar-title">Crons</div>' +
    cachedCrons.map(c => {
      const dot = c.last_status === "ok" ? "ok" : c.last_status === "never" ? "never" : "err";
      const active = c.id === selectedCronId ? " active" : "";
      const { label } = parseCronSchedule(c.schedule);
      return `<div class="auto-cron-item${active}" data-cron-id="${c.id}">
        <div class="auto-cron-dot ${dot}"></div>
        <div>
          <div class="auto-cron-name">${c.name}</div>
          <div class="auto-cron-sched">${label}</div>
        </div>
      </div>`;
    }).join("");
  sidebar.querySelectorAll("[data-cron-id]").forEach(el => {
    el.addEventListener("click", () => {
      selectedCronId = el.dataset.cronId;
      renderCronSidebar();
      const cron = cachedCrons.find(c => c.id === selectedCronId);
      if (cron) renderCronPipeline(cron);
    });
  });
}

function renderCronPipeline(cron) {
  const main = document.getElementById("cron-pipeline");
  if (!main) return;
  const { label: schedLabel } = parseCronSchedule(cron.schedule);
  const isAgent = !!cron.agent_id;
  const lastRun = fmtCronTime(cron.last_run) || "never";
  const nextRun = fmtCronTime(cron.next_run) || "—";
  const statusClass = cron.last_status === "ok" ? "ok" : cron.last_status === "never" ? "never" : "err";
  const outputHtml = cron.last_output
    ? `<pre class="pipeline-output">${escHtml(cron.last_output)}</pre>`
    : '<span style="color:var(--text-dimmer);font-size:.82rem">No output yet — run the cron to see results here.</span>';

  const isAdmin = currentProfile?.admin;
  const channels = cron.notify_channels || [];
  const users = cron.notify_users || [];
  const profiles = (window._profiles || []);

  // Notification node: read-only for non-admins, inline editor for admins
  const notifyNodeHtml = isAdmin ? (() => {
    const chTelegram = channels.includes("telegram") ? "active" : "";
    const chEmail    = channels.includes("email")    ? "active" : "";
    const userBtns   = profiles.length
      ? profiles.map(p => `<button class="cp-user-btn${users.includes(p.id) ? " active" : ""}" data-uid="${p.id}">${p.name || p.id}</button>`).join("")
      : users.map(u => `<button class="cp-user-btn active" data-uid="${u}">${u}</button>`).join("");
    return `
      <div class="p-notify-editor">
        <div class="p-notify-row" style="margin-bottom:.6rem">
          <button class="notif-toggle ${chTelegram}" data-channel="telegram">✈ Telegram</button>
          <button class="notif-toggle ${chEmail}"    data-channel="email">✉ Email</button>
        </div>
        <div style="font-size:.7rem;color:var(--text-dimmer);margin-bottom:.35rem;letter-spacing:.05em">RECIPIENTS</div>
        <div class="p-notify-users" id="pipe-user-btns">${userBtns}</div>
        <div style="margin-top:.75rem;display:flex;align-items:center;gap:.6rem">
          <button class="btn-primary btn-sm" id="pipe-notify-save">Save</button>
          <span id="pipe-notify-msg" style="font-size:.78rem;color:var(--ok)"></span>
        </div>
      </div>`;
  })() : (() => {
    const chips = channels.length
      ? channels.map(ch => `<span class="notify-chip ${ch}">${ch}</span>`).join("")
      : '<span style="color:var(--text-dimmer);font-size:.8rem">No notification configured</span>';
    return `<div class="p-notify-row">${chips}</div>
      ${users.length ? `<div style="margin-top:.4rem;font-size:.75rem;color:var(--text-dim)">${users.join(", ")}</div>` : ""}`;
  })();

  const runBtn = isAdmin
    ? `<button class="btn-primary btn-sm" id="pipe-run-btn">▶ Run now</button>`
    : "";

  main.innerHTML = `
    <div class="pipeline">
      <div class="pipeline-header">
        <div>
          <div class="pipeline-title">${cron.name}</div>
          <div class="pipeline-subtitle">${cron.id}</div>
        </div>
        <div class="pipeline-actions">${runBtn}</div>
      </div>

      <div class="p-node node-trigger">
        <div class="p-node-label">⏰ Trigger</div>
        <div>
          <span class="p-schedule-pill">${schedLabel}</span>
          <span style="font-family:monospace;font-size:.75rem;color:var(--text-dim)">${cron.schedule}</span>
        </div>
        <div class="p-node-meta">
          <span>Last run: <b>${lastRun}</b></span>
          <span>Next run: <b>${nextRun}</b></span>
        </div>
      </div>

      <div class="p-connector"></div>

      <div class="p-node node-task">
        <div class="p-node-label">${isAgent ? "🤖 Agent — " + cron.agent_id : "💻 Shell"}</div>
        <pre class="p-node-code">${escHtml(cron.command || "")}</pre>
      </div>

      <div class="p-connector"></div>

      <div class="p-node node-output ${statusClass}">
        <div class="p-node-label">
          <span>📄 Last output</span>
          <span class="cron-status ${statusClass}">${cron.last_status}</span>
        </div>
        ${outputHtml}
      </div>

      <div class="p-connector"></div>

      <div class="p-node node-notify">
        <div class="p-node-label">📬 Notifications</div>
        ${notifyNodeHtml}
      </div>
    </div>`;

  // Run button
  const runBtnEl = document.getElementById("pipe-run-btn");
  if (runBtnEl) {
    runBtnEl.addEventListener("click", async e => {
      e.stopPropagation();
      runBtnEl.disabled = true; runBtnEl.textContent = "Running…";
      await safeFetch(`/api/crons/${cron.id}/run?token=${enc(token)}`, { method: "POST" });
      runBtnEl.textContent = "▶ Run now"; runBtnEl.disabled = false;
      setTimeout(loadCrons, 3500);
    });
  }

  // Channel toggles
  main.querySelectorAll(".notif-toggle").forEach(btn => {
    btn.addEventListener("click", () => btn.classList.toggle("active"));
  });
  // User buttons
  main.querySelectorAll(".cp-user-btn").forEach(btn => {
    btn.addEventListener("click", () => btn.classList.toggle("active"));
  });

  // Save notifications
  const saveBtn = document.getElementById("pipe-notify-save");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const selChannels = [...main.querySelectorAll(".notif-toggle.active")].map(b => b.dataset.channel);
      const selUsers    = [...main.querySelectorAll(".cp-user-btn.active")].map(b => b.dataset.uid);
      const res = await safeFetch(`/api/crons/${cron.id}/notify?token=${enc(token)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channels: selChannels, users: selUsers }),
      });
      const msg = document.getElementById("pipe-notify-msg");
      if (res?.status === "ok") {
        cron.notify_channels = selChannels;
        cron.notify_users = selUsers;
        if (msg) { msg.textContent = "✓ Saved"; setTimeout(() => { msg.textContent = ""; }, 2000); }
        renderCronSidebar();
      } else {
        if (msg) msg.textContent = "Error";
      }
    });
  }
}

function escHtml(s) {
  return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ── Cron detail panel ────────────────────────────────────────────
function openCronPanel(cron) {
  const panel = document.getElementById("cron-panel");
  const overlay = document.getElementById("cron-panel-overlay");
  if (!panel || !overlay) return;

  // Header
  document.getElementById("cron-panel-title").textContent = cron.name;
  document.getElementById("cron-panel-schedule").textContent = cron.schedule;

  // Status + times
  const sc = cron.last_status === "ok" ? "ok" : cron.last_status === "never" ? "never" : "err";
  const statusEl = document.getElementById("cron-panel-status");
  statusEl.className = `cron-status ${sc}`;
  statusEl.textContent = cron.last_status;
  const lastRun = fmtCronTime(cron.last_run) || "never";
  const nextRun = fmtCronTime(cron.next_run) || "—";
  document.getElementById("cron-panel-times").innerHTML =
    `Dernière : <b>${lastRun}</b> &nbsp;·&nbsp; Prochaine : <b style="color:var(--accent)">${nextRun}</b>`;

  // Type block
  const typeEl = document.getElementById("cron-panel-type");
  if (cron.agent_id) {
    typeEl.innerHTML = `
      <div><span class="cron-panel-type-label">Agent</span>
      <span style="color:var(--accent);font-weight:600">${escHtml(cron.agent_id)}</span></div>
      <div style="margin-top:.4rem"><span class="cron-panel-type-label">Tâche</span>
      <div class="cron-panel-command">${escHtml(cron.command || "—")}</div></div>`;
  } else {
    typeEl.innerHTML = `
      <div><span class="cron-panel-type-label">Shell command</span></div>
      <div class="cron-panel-command" style="margin-top:.3rem">${escHtml(cron.command || "—")}</div>`;
  }

  // Notifications — channel toggles
  document.querySelectorAll(".notif-toggle").forEach(btn => {
    const active = (cron.notify_channels || []).includes(btn.dataset.channel);
    btn.classList.toggle("active", active);
    btn.onclick = () => btn.classList.toggle("active");
  });

  // Notifications — user picker (multi-select)
  const userBtns = document.getElementById("cp-user-btns");
  const selectedUsers = cron.notify_users?.length ? cron.notify_users
    : (cron.notify_user ? [cron.notify_user] : [currentProfile?.id]);
  const profiles = window._profiles || [];
  const profileList = profiles.length ? profiles : selectedUsers.map(id => ({ id, name: id }));
  userBtns.innerHTML = profileList.map(p =>
    `<button class="cp-user-btn ${selectedUsers.includes(p.id) ? "active" : ""}" data-uid="${p.id}">${p.name}</button>`
  ).join("");
  userBtns.querySelectorAll(".cp-user-btn").forEach(b => {
    b.addEventListener("click", () => b.classList.toggle("active"));
  });

  // Save notify
  document.getElementById("cp-notif-msg").textContent = "";
  document.getElementById("cp-notif-save").onclick = async () => {
    const channels = [...document.querySelectorAll(".notif-toggle.active")].map(b => b.dataset.channel);
    const users = [...userBtns.querySelectorAll(".cp-user-btn.active")].map(b => b.dataset.uid);
    const res = await safeFetch(`/api/crons/${cron.id}/notify?token=${enc(token)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channels, users }),
    });
    if (res?.status === "ok") {
      document.getElementById("cp-notif-msg").textContent = "✓ Sauvegardé";
      cron.notify_channels = channels; cron.notify_users = users;
      setTimeout(() => { document.getElementById("cp-notif-msg").textContent = ""; loadCrons(); }, 1500);
    }
  };

  // Run now
  const runBtn = document.getElementById("cp-run-btn");
  runBtn.disabled = false;
  runBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" width="13" height="13"><polygon points="5,3 19,12 5,21"/></svg> Run now';
  runBtn.onclick = async () => {
    const prevLastRun = cron.last_run;
    runBtn.disabled = true; runBtn.textContent = "Running…";
    await safeFetch(`/api/crons/${cron.id}/run?token=${enc(token)}`, { method: "POST" });
    // Poll until last_run changes (job completed in background)
    const poll = async () => {
      const all = await safeFetch(`/api/crons?token=${enc(token)}`);
      if (!all) return;
      const refreshed = all.find(c => c.id === cron.id);
      if (refreshed && refreshed.last_run !== prevLastRun) {
        openCronPanel(refreshed);
        loadCrons();
      } else {
        setTimeout(poll, 2000);
      }
    };
    setTimeout(poll, 2000);
  };

  // Error display
  const errSection = document.getElementById("cp-error-section");
  const errEl = document.getElementById("cp-error");
  if (cron.last_error?.trim()) {
    errEl.textContent = cron.last_error;
    errSection.classList.remove("hidden");
  } else if (errSection) {
    errSection.classList.add("hidden");
  }

  // Output
  const outSection = document.getElementById("cp-output-section");
  const outEl = document.getElementById("cp-output");
  if (cron.last_output?.trim()) {
    outEl.textContent = cron.last_output;
    outSection.classList.remove("hidden");
  } else {
    outSection.classList.add("hidden");
  }

  panel.classList.remove("hidden");
  overlay.classList.remove("hidden");
}

document.getElementById("cron-panel-close")?.addEventListener("click", () => {
  document.getElementById("cron-panel")?.classList.add("hidden");
  document.getElementById("cron-panel-overlay")?.classList.add("hidden");
});
document.getElementById("cron-panel-overlay")?.addEventListener("click", () => {
  document.getElementById("cron-panel")?.classList.add("hidden");
  document.getElementById("cron-panel-overlay")?.classList.add("hidden");
});

// ── Agents ─────────────────────────────────────────────────────
async function loadAgents() {
  const agents = await safeFetch(`/api/agents?token=${enc(token)}`);
  if (!agents || agents.length === 0) {
    agentsGrid.innerHTML = '<div class="skills-empty">No agents configured yet.<br>Click "+ New agent" to create one.</div>';
    return;
  }

  agentsGrid.innerHTML = agents.map(a => `
    <div class="agent-card ${a.enabled ? "" : "agent-disabled"}" data-agent-id="${a.id}">
      <div class="agent-card-avatar">
        <img src="/static/avatars/${a.avatar}.svg" alt="" class="agent-avatar-img"
          onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" />
        <div class="agent-avatar-fallback" style="display:none">${a.name.charAt(0)}</div>
      </div>
      <div class="agent-card-info">
        <div class="agent-card-name">${a.name}</div>
        <div class="agent-card-desc">${a.description || ""}</div>
        <div class="agent-triggers">
          ${(a.triggers || []).slice(0, 4).map(t => `<span class="trigger-tag">${t}</span>`).join("")}
        </div>
      </div>
      <div class="agent-card-status ${a.enabled ? "ok" : "disabled"}">${a.enabled ? "enabled" : "off"}</div>
    </div>
  `).join("");

  if (currentProfile?.admin) {
    document.querySelectorAll(".agent-card").forEach(card => {
      card.addEventListener("click", () => {
        const id = card.dataset.agentId;
        const agent = agents.find(a => a.id === id);
        if (agent) openAgentModal(agent);
      });
      card.style.cursor = "pointer";
    });
  }
}

// ── Agent Modal ─────────────────────────────────────────────────
const agentModal      = document.getElementById("agent-modal");
const agentModalTitle = document.getElementById("agent-modal-title");
const agentModalClose = document.getElementById("agent-modal-close");
const agentModalBack  = document.getElementById("agent-modal-backdrop");
const agentNameIn     = document.getElementById("agent-name");
const agentDescIn     = document.getElementById("agent-desc");
const agentModelIn    = document.getElementById("agent-model");
const agentSysIn      = document.getElementById("agent-system-prompt");
const agentTriggersIn = document.getElementById("agent-triggers");
const agentEnabledIn  = document.getElementById("agent-enabled");
const agentSaveBtn    = document.getElementById("agent-save-btn");
const agentCancelBtn  = document.getElementById("agent-cancel-btn");
const agentDeleteBtn  = document.getElementById("agent-delete-btn");
const avatarPicker    = document.getElementById("avatar-picker");
const createAgentBtn  = document.getElementById("create-agent-btn");

let editingAgentId = null;
let selectedAvatar = "robot-default";

function renderAvatarPicker(current) {
  avatarPicker.innerHTML = AVATARS.map(av => `
    <button class="avatar-option ${av === current ? "selected" : ""}" data-av="${av}" title="${av}">
      <img src="/static/avatars/${av}.svg" alt="${av}" />
    </button>
  `).join("");
  avatarPicker.querySelectorAll(".avatar-option").forEach(btn => {
    btn.addEventListener("click", () => {
      selectedAvatar = btn.dataset.av;
      avatarPicker.querySelectorAll(".avatar-option").forEach(b => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });
}

function openAgentModal(agent = null) {
  editingAgentId = agent?.id || null;
  agentModalTitle.textContent = agent ? "Edit agent" : "New agent";
  agentNameIn.value = agent?.name || "";
  agentDescIn.value = agent?.description || "";
  agentModelIn.value = agent?.model || "claude-sonnet-4-6";
  agentSysIn.value = agent?.system_prompt || "";
  agentTriggersIn.value = (agent?.triggers || []).join(", ");
  agentEnabledIn.checked = agent?.enabled !== false;
  selectedAvatar = agent?.avatar || "robot-default";
  renderAvatarPicker(selectedAvatar);
  agentDeleteBtn.classList.toggle("hidden", !agent);
  agentModal.classList.remove("hidden");
}

function closeAgentModal() {
  agentModal.classList.add("hidden");
  editingAgentId = null;
}

agentModalClose.addEventListener("click", closeAgentModal);
agentCancelBtn.addEventListener("click", closeAgentModal);
agentModalBack.addEventListener("click", closeAgentModal);
if (createAgentBtn) {
  createAgentBtn.addEventListener("click", () => openAgentModal());
}

agentSaveBtn.addEventListener("click", async () => {
  const data = {
    name: agentNameIn.value.trim(),
    description: agentDescIn.value.trim(),
    avatar: selectedAvatar,
    model: agentModelIn.value,
    system_prompt: agentSysIn.value.trim(),
    triggers: agentTriggersIn.value.split(",").map(t => t.trim()).filter(Boolean),
    enabled: agentEnabledIn.checked,
  };
  if (!data.name) { agentNameIn.focus(); return; }

  let res;
  if (editingAgentId) {
    res = await safeFetch(`/api/agents/${editingAgentId}?token=${enc(token)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } else {
    res = await safeFetch(`/api/agents?token=${enc(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  }
  if (res) {
    closeAgentModal();
    loadAgents();
  }
});

agentDeleteBtn.addEventListener("click", async () => {
  if (!editingAgentId) return;
  if (!confirm("Delete this agent?")) return;
  await safeFetch(`/api/agents/${editingAgentId}?token=${enc(token)}`, { method: "DELETE" });
  closeAgentModal();
  loadAgents();
});

// (Contexts are now in the Profile panel — see loadPanelContext())

// ── Profile panel ──────────────────────────────────────────────
profileBtn.addEventListener("click", openProfilePanel);
profilePanelClose.addEventListener("click", closeProfilePanel);
profileOverlay.addEventListener("click", closeProfilePanel);

// Panel tabs
document.querySelectorAll(".panel-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".panel-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    const which = tab.dataset.tab;
    document.querySelectorAll(".panel-tab-content").forEach(c => c.classList.add("hidden"));
    document.querySelector(`[data-tab-body="${which}"]`).classList.remove("hidden");
    if (which === "context") { loadPanelContext(); loadPanelMemory(); }
  });
});

function openProfilePanel() {
  profilePanel.classList.remove("hidden");
  profileOverlay.classList.remove("hidden");
  // Reset to profile tab
  document.querySelectorAll(".panel-tab").forEach(t => t.classList.remove("active"));
  document.querySelector('[data-tab="profile"]').classList.add("active");
  document.querySelectorAll(".panel-tab-content").forEach(c => c.classList.add("hidden"));
  document.querySelector('[data-tab-body="profile"]').classList.remove("hidden");
  renderProfilePanel();
}

function closeProfilePanel() {
  profilePanel.classList.add("hidden");
  profileOverlay.classList.add("hidden");
}

async function renderProfilePanel() {
  const usage = await safeFetch(`/api/usage?token=${enc(token)}`);
  const myUsage = usage?.[currentProfile.id];

  let html = `
    <div class="profile-section">
      <div class="profile-section-title">Current profile</div>
      <div class="profile-info-row">
        <div class="avatar">${currentProfile.name.charAt(0).toUpperCase()}</div>
        <div>
          <div style="font-weight:700">${currentProfile.name}</div>
          <div style="font-size:.75rem;color:var(--text-dim)">${currentProfile.admin ? "Admin" : "User"} · ${currentProfile.id}</div>
        </div>
      </div>
    </div>
  `;

  if (myUsage) {
    html += `
      <div class="profile-section">
        <div class="profile-section-title">API Usage</div>
        <div class="usage-card">
          <div class="usage-label">Today</div>
          <div class="usage-val">$${myUsage.today.cost_usd.toFixed(5)}</div>
          <div class="usage-sub">€${myUsage.today.cost_eur.toFixed(5)} · ${myUsage.today.calls} calls · ${(myUsage.today.input_tokens + myUsage.today.output_tokens).toLocaleString()} tokens</div>
        </div>
        <div class="usage-card">
          <div class="usage-label">All-time total</div>
          <div class="usage-val">$${myUsage.total.cost_usd.toFixed(4)}</div>
          <div class="usage-sub">€${myUsage.total.cost_eur.toFixed(4)} · ${myUsage.total.calls} calls · ${myUsage.total.tokens.toLocaleString()} tokens</div>
        </div>
      </div>
    `;
  }

  if (currentProfile.admin) {
    const allProfiles = await safeFetch(`/api/admin/profiles?token=${enc(token)}`);
    if (allProfiles) {
      html += `
        <div class="profile-section">
          <div class="profile-section-title">All profiles (admin)</div>
          <div class="admin-profiles-list">
            ${allProfiles.map(p => `
              <div class="admin-profile-row">
                <div style="display:flex;align-items:center;gap:.6rem">
                  <div class="avatar" style="width:24px;height:24px;font-size:.7rem">${p.name.charAt(0).toUpperCase()}</div>
                  <span>${p.name}</span>
                  ${p.admin ? '<span class="badge-admin">admin</span>' : ""}
                </div>
                <div style="font-size:.75rem;color:var(--text-dim)">
                  ${usage?.[p.id] ? "$" + (usage[p.id].total?.cost_usd || 0).toFixed(4) : "—"}
                </div>
              </div>
            `).join("")}
          </div>
        </div>
      `;
    }
  }

  // Password change (only if profile has a password set, or admin)
  html += `
    <div class="profile-section" id="pw-change-section">
      <div class="profile-section-title">Change password</div>
      <div style="display:flex;flex-direction:column;gap:.5rem;margin-top:.3rem">
        <input type="password" id="pw-current" class="settings-input" placeholder="Current password" autocomplete="current-password" />
        <input type="password" id="pw-new" class="settings-input" placeholder="New password" autocomplete="new-password" />
        <input type="password" id="pw-confirm" class="settings-input" placeholder="Confirm new password" autocomplete="new-password" />
        <div style="display:flex;align-items:center;gap:.6rem;margin-top:.2rem">
          <button class="btn-primary" id="pw-save-btn">Save</button>
          <span id="pw-msg" class="connexion-msg hidden"></span>
        </div>
      </div>
    </div>
  `;

  html += `<button class="signout-btn" id="signout-btn">Sign out</button>`;

  profilePanelBody.innerHTML = html;
  document.getElementById("signout-btn").addEventListener("click", () => {
    sessionStorage.removeItem("onc_token");
    location.reload();
  });

  document.getElementById("pw-save-btn").addEventListener("click", async () => {
    const currentPw = document.getElementById("pw-current").value;
    const newPw = document.getElementById("pw-new").value.trim();
    const confirmPw = document.getElementById("pw-confirm").value.trim();
    const msg = document.getElementById("pw-msg");
    if (!newPw) { showConnMsg(msg, false, "", "New password required"); return; }
    if (newPw !== confirmPw) { showConnMsg(msg, false, "", "Passwords don't match"); return; }
    const res = await safeFetch(`/api/profiles/password?token=${enc(token)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
    });
    if (res?.status === "ok") {
      // Update token with new password
      const parts = token.split(":");
      token = parts[0] + ":" + newPw;
      sessionStorage.setItem("onc_token", token);
      document.getElementById("pw-current").value = "";
      document.getElementById("pw-new").value = "";
      document.getElementById("pw-confirm").value = "";
      showConnMsg(msg, true, "✓ Password updated", "");
    } else {
      showConnMsg(msg, false, "", "✗ " + (res === null ? "Wrong current password" : "Failed"));
    }
  });
}

// ── Panel Memory ───────────────────────────────────────────────
const panelMemory       = document.getElementById("panel-memory");
const panelSaveMemoryBtn = document.getElementById("panel-save-memory-btn");
const panelMemorySaved  = document.getElementById("panel-memory-saved");

async function loadPanelMemory() {
  const data = await safeFetch(`/api/memory?token=${enc(token)}`);
  if (panelMemory) panelMemory.value = data?.content || "";
}

if (panelSaveMemoryBtn) {
  panelSaveMemoryBtn.addEventListener("click", async () => {
    const res = await safeFetch(`/api/memory?token=${enc(token)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: panelMemory?.value || "" }),
    });
    if (panelMemorySaved) {
      panelMemorySaved.textContent = res?.status === "ok" ? "✓ Sauvegardé" : "✗ Erreur";
      panelMemorySaved.classList.remove("hidden");
      setTimeout(() => panelMemorySaved.classList.add("hidden"), 2000);
    }
  });
}

// ── Panel Context tab ──────────────────────────────────────────
const panelGlobalCtx    = document.getElementById("panel-global-ctx");
const panelSaveGlobalBtn = document.getElementById("panel-save-global-btn");
const panelGlobalSaved  = document.getElementById("panel-global-saved");
const panelProjectsList = document.getElementById("panel-projects-list");
const panelAddProjectBtn = document.getElementById("panel-add-project-btn");

async function loadPanelContext() {
  const data = await safeFetch(`/api/contexts?token=${enc(token)}`);
  if (!data) return;
  panelGlobalCtx.value = data.global || "";
  renderPanelProjects(data.projects || []);
}

function renderPanelProjects(projects) {
  if (!panelProjectsList) return;
  if (projects.length === 0) {
    panelProjectsList.innerHTML = '<div style="color:var(--text-dim);font-size:.82rem;padding:.5rem 0">No project contexts yet.</div>';
    return;
  }
  panelProjectsList.innerHTML = projects.map(p => `
    <div class="panel-project-card">
      <div class="panel-project-header">
        <strong>${p.name}</strong>
        <code class="shortcut-tag">${p.shortcut}</code>
        <button class="icon-btn delete-proj-btn" data-id="${p.id}" title="Delete">✕</button>
      </div>
      <textarea class="context-textarea" id="pprojc-${p.id}" rows="3">${escHtml(p.content || "")}</textarea>
      <button class="btn-secondary save-proj-btn" data-id="${p.id}" style="margin-top:.35rem">Save</button>
    </div>
  `).join("");

  panelProjectsList.querySelectorAll(".save-proj-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const content = document.getElementById(`pprojc-${id}`).value;
      await safeFetch(`/api/contexts/projects/${id}?token=${enc(token)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      btn.textContent = "✓";
      setTimeout(() => { btn.textContent = "Save"; }, 1500);
    });
  });

  panelProjectsList.querySelectorAll(".delete-proj-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this project context?")) return;
      await safeFetch(`/api/contexts/projects/${btn.dataset.id}?token=${enc(token)}`, { method: "DELETE" });
      loadPanelContext();
    });
  });
}

if (panelSaveGlobalBtn) {
  panelSaveGlobalBtn.addEventListener("click", async () => {
    await safeFetch(`/api/contexts/global?token=${enc(token)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: panelGlobalCtx.value }),
    });
    panelGlobalSaved.textContent = "✓ Saved";
    panelGlobalSaved.classList.remove("hidden");
    setTimeout(() => panelGlobalSaved.classList.add("hidden"), 2000);
  });
}

if (panelAddProjectBtn) {
  panelAddProjectBtn.addEventListener("click", async () => {
    const name = prompt("Project name (e.g. FluenzR App):");
    if (!name) return;
    const shortcut = prompt("Shortcut (e.g. fluapp):");
    if (!shortcut) return;
    await safeFetch(`/api/contexts/projects?token=${enc(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, shortcut, content: "" }),
    });
    loadPanelContext();
  });
}

const signoutCtxBtn = document.getElementById("signout-btn-ctx");
if (signoutCtxBtn) {
  signoutCtxBtn.addEventListener("click", () => {
    sessionStorage.removeItem("onc_token");
    location.reload();
  });
}

// ── Helpers ────────────────────────────────────────────────────
async function safeFetch(url, opts = {}) {
  try {
    const resp = await fetch(url, opts);
    if (!resp.ok) return null;
    return await resp.json();
  } catch { return null; }
}

function enc(s) { return encodeURIComponent(s); }

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Settings ───────────────────────────────────────────────────
const apikeyInput      = document.getElementById("apikey-input");
const apikeySaveBtn    = document.getElementById("apikey-save-btn");
const apikeyResultMsg  = document.getElementById("apikey-result-msg");
const apikeyStatusText = document.getElementById("apikey-status-text");
const settingsBackendInfo = document.getElementById("settings-backend-info");

// Auth elements
const authStatusText    = document.getElementById("auth-status-text");
const authIdle          = document.getElementById("auth-idle");
const authStepUrl       = document.getElementById("auth-step-url");
const authStepSuccess   = document.getElementById("auth-step-success");
const authStepError     = document.getElementById("auth-step-error");
const authStartBtn      = document.getElementById("auth-start-btn");
const authUrlLink       = document.getElementById("auth-url-link");
const authUrlText       = document.getElementById("auth-url-text");
const authCallbackInput = document.getElementById("auth-callback-input");
const authCompleteBtn   = document.getElementById("auth-complete-btn");
const authCancelBtn     = document.getElementById("auth-cancel-btn");

async function loadSettings() {
  // Bot identity
  const botPicker = document.getElementById("bot-avatar-picker");
  const botNameInput = document.getElementById("bot-name-input");
  const botSaveBtn = document.getElementById("bot-save-btn");
  const botSaveMsg = document.getElementById("bot-save-msg");
  if (botNameInput) botNameInput.value = botSettings.name;
  if (botPicker) {
    let selectedAvatar = botSettings.avatar;
    botPicker.innerHTML = "";
    BOT_AVATARS.forEach(av => {
      const opt = document.createElement("div");
      opt.className = "bot-avatar-option" + (av.id === selectedAvatar ? " selected" : "");
      opt.dataset.avatarId = av.id;
      opt.innerHTML = `<img src="/static/avatars/${av.id}.svg" alt="${av.label}" /><span>${av.label}</span>`;
      opt.addEventListener("click", () => {
        selectedAvatar = av.id;
        botPicker.querySelectorAll(".bot-avatar-option").forEach(o => o.classList.remove("selected"));
        opt.classList.add("selected");
      });
      botPicker.appendChild(opt);
    });
    if (botSaveBtn) {
      // Remove old listener by cloning
      const newBtn = botSaveBtn.cloneNode(true);
      botSaveBtn.replaceWith(newBtn);
      newBtn.addEventListener("click", async () => {
        const name = botNameInput?.value.trim() || "";
        newBtn.disabled = true;
        const res = await safeFetch(`/api/bot?token=${enc(token)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name || botSettings.name, avatar: selectedAvatar }),
        });
        newBtn.disabled = false;
        if (res) {
          botSettings = res;
          applyBotSettings();
          if (botSaveMsg) showConnMsg(botSaveMsg, true, "✓ Saved!", "");
        } else {
          if (botSaveMsg) showConnMsg(botSaveMsg, false, "", "✗ Failed");
        }
      });
    }
  }

  // API key status
  const keyData = await safeFetch(`/api/admin/apikey/status?token=${enc(token)}`);
  if (keyData) {
    if (keyData.has_key) {
      apikeyStatusText.innerHTML = '<span style="color:var(--ok)">✓ API key configured</span>';
    } else {
      apikeyStatusText.innerHTML = '<span style="color:var(--warn)">No API key — add yours below</span>';
    }
    if (keyData.backend_type && keyData.model) {
      settingsBackendInfo.textContent = `Type: ${keyData.backend_type} · Model: ${keyData.model}`;
    }
  }

  // Claude Code auth status
  const authData = await safeFetch(`/api/admin/auth/status?token=${enc(token)}`);
  if (authData) {
    if (authData.authenticated) {
      authStatusText.innerHTML = '<span style="color:var(--ok)">✓ Claude Code authenticated</span>';
      authIdle.querySelector("#auth-start-btn").textContent = "Re-authenticate";
    } else {
      authStatusText.innerHTML = '<span style="color:var(--warn)">Not authenticated — agents use Claude CLI</span>';
    }
  }

  // Gmail + Google Tasks (in Général tab)
  _loadGeneralConnexions();
}

if (authStartBtn) {
  authStartBtn.addEventListener("click", async () => {
    authStartBtn.disabled = true;
    authStartBtn.textContent = "Starting…";
    authStepError.classList.add("hidden");

    const data = await safeFetch(`/api/admin/auth/start?token=${enc(token)}`, { method: "POST" });
    authStartBtn.disabled = false;
    authStartBtn.textContent = "Start authentication";

    if (!data || data.status === "error") {
      authStepError.textContent = "✗ " + (data?.detail || "Failed to start auth");
      authStepError.classList.remove("hidden");
      return;
    }

    if (data.status === "waiting_for_callback_url" && data.url) {
      authUrlLink.href = data.url;
      authUrlLink.textContent = data.url;
      authIdle.classList.add("hidden");
      authStepUrl.classList.remove("hidden");
    }
  });
}

if (authCompleteBtn) {
  authCompleteBtn.addEventListener("click", async () => {
    const code = authCallbackInput.value.trim();
    if (!code) { authCallbackInput.focus(); return; }
    authCompleteBtn.disabled = true;
    authCompleteBtn.textContent = "Verifying…";
    authStepError.classList.add("hidden");

    const data = await safeFetch(`/api/admin/auth/complete?token=${enc(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });

    authCompleteBtn.disabled = false;
    authCompleteBtn.textContent = "Complete";

    if (data?.status === "ok") {
      authStepUrl.classList.add("hidden");
      authStepSuccess.classList.remove("hidden");
      authIdle.classList.remove("hidden");
      authCallbackInput.value = "";
      setTimeout(loadSettings, 1000);
    } else {
      authStepError.textContent = "✗ " + (data?.detail || "Authentication failed");
      authStepError.classList.remove("hidden");
    }
  });
}

if (authCancelBtn) {
  authCancelBtn.addEventListener("click", () => {
    authStepUrl.classList.add("hidden");
    authIdle.classList.remove("hidden");
    authCallbackInput.value = "";
    authStepError.classList.add("hidden");
  });
}

if (apikeySaveBtn) {
  apikeySaveBtn.addEventListener("click", async () => {
    const key = apikeyInput.value.trim();
    if (!key) return;
    apikeySaveBtn.disabled = true;
    apikeySaveBtn.textContent = "Saving…";
    apikeyResultMsg.classList.add("hidden");

    const data = await safeFetch(`/api/admin/apikey/save?token=${enc(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });

    apikeySaveBtn.disabled = false;
    apikeySaveBtn.textContent = "Save";
    apikeyResultMsg.classList.remove("hidden");

    if (data?.status === "ok") {
      apikeyResultMsg.style.color = "var(--ok)";
      apikeyResultMsg.textContent = "✓ API key saved";
      apikeyInput.value = "";
      setTimeout(loadSettings, 500);
    } else {
      apikeyResultMsg.style.color = "var(--err)";
      apikeyResultMsg.textContent = "✗ " + (data?.detail || "Failed. Check the key format.");
    }
  });
}

// ── Connexions ──────────────────────────────────────────────────

// Called at end of loadSettings to init Gmail + Google Tasks cards (in Général tab)
function _loadGeneralConnexions() {
  loadGmailStatus();
  const hint = document.getElementById("gmail-redirect-uri-hint");
  if (hint) hint.textContent = window.location.origin + "/api/gmail/callback";
  loadGtasksStatus();
}

async function loadConnexions() {
  const forUser = typeof _getForUser === "function" ? _getForUser() : "";
  const data = await safeFetch(`/api/connexions?token=${enc(token)}&for_user=${forUser}`);
  if (!data) return;

  // Telegram
  const tg = data.telegram || {};
  const tgEnabled = document.getElementById("tg-enabled");
  const tgTokenIn = document.getElementById("tg-token");
  const tgChatIn  = document.getElementById("tg-chat-id");
  if (tgEnabled) tgEnabled.checked = tg.enabled || false;
  if (tgTokenIn) tgTokenIn.value = tg.bot_token || "";
  if (tgChatIn)  tgChatIn.value  = tg.chat_id || "";

  // Email
  const email = data.email || {};
  const f = id => document.getElementById(id);
  if (f("email-enabled"))    f("email-enabled").checked = email.enabled || false;
  if (f("email-host"))       f("email-host").value      = email.smtp_host || "smtp.gmail.com";
  if (f("email-port"))       f("email-port").value      = email.smtp_port || 587;
  if (f("email-user"))       f("email-user").value      = email.smtp_user || "";
  if (f("email-password"))   f("email-password").value  = email.smtp_password || "";
  if (f("email-from-name"))  f("email-from-name").value = email.from_name || "";
  if (f("email-from-email"))   f("email-from-email").value   = email.from_email || "";
  if (f("email-notify-email")) f("email-notify-email").value = email.notify_email || "";

  // MCPs
  renderMcps(data.mcps || []);
  loadMcpCatalog();

  // GitHub
  const ghData = data.github || {};
  const f2 = id => document.getElementById(id);
  if (f2("gh-enabled"))    f2("gh-enabled").checked     = ghData.enabled || false;
  if (f2("gh-token"))      f2("gh-token").value          = ghData.token || "";
  if (f2("gh-repo-owner")) f2("gh-repo-owner").value    = ghData.repo_owner || "";
  if (f2("gh-repo-name"))  f2("gh-repo-name").value     = ghData.repo_name || "";

  // Linear
  const linData = data.linear || {};
  if (f2("linear-enabled"))  f2("linear-enabled").checked  = linData.enabled || false;
  if (f2("linear-api-key"))  f2("linear-api-key").value    = linData.api_key || "";

  // Social
  const soc = data.social || {};
  const reddit = soc.reddit || {};
  const bsky = soc.bluesky || {};
  const tw = soc.twitter || {};
  if (f("reddit-enabled"))    f("reddit-enabled").checked      = reddit.enabled || false;
  if (f("reddit-username"))   f("reddit-username").value       = reddit.username || "";
  if (f("bsky-enabled"))      f("bsky-enabled").checked        = bsky.enabled || false;
  if (f("bsky-handle"))       f("bsky-handle").value           = bsky.handle || "";
  if (f("bsky-app-password")) f("bsky-app-password").value     = bsky.app_password || "";
  if (f("twitter-enabled"))             f("twitter-enabled").checked            = tw.enabled || false;
  if (f("twitter-consumer-key"))        f("twitter-consumer-key").value         = tw.consumer_key || "";
  if (f("twitter-consumer-secret"))     f("twitter-consumer-secret").value      = tw.consumer_secret || "";
  if (f("twitter-access-token"))        f("twitter-access-token").value         = tw.access_token || "";
  if (f("twitter-access-token-secret")) f("twitter-access-token-secret").value  = tw.access_token_secret || "";
  if (f("twitter-bearer-token"))        f("twitter-bearer-token").value         = tw.bearer_token || "";

  // Leclerc
  const lec = data.leclerc || {};
  if (f("leclerc-enabled"))  f("leclerc-enabled").checked = lec.enabled || false;
  if (f("leclerc-email"))    f("leclerc-email").value     = lec.email || "";
  if (f("leclerc-password")) f("leclerc-password").value  = lec.password || "";

  // Notion
  const notion = data.notion || {};
  if (f("notion-enabled"))      f("notion-enabled").checked    = notion.enabled || false;
  if (f("notion-api-key"))      f("notion-api-key").value      = notion.api_key || "";
  if (f("notion-database-id"))  f("notion-database-id").value  = notion.database_id || "";

}

// ── Settings tabs ────────────────────────────────────────────────
let _connActiveUser = "";  // empty = current user, else target user id

function _getForUser() {
  if (!currentProfile?.admin) return "";
  return _connActiveUser === currentProfile.id ? "" : _connActiveUser;
}

function _setConnUser(userId) {
  _connActiveUser = userId || currentProfile?.id || "";
  // Show/hide gilles-only cards
  const gillesOnly = document.querySelectorAll(".conn-gilles-only");
  const isGilles = _connActiveUser !== "pam";
  gillesOnly.forEach(el => el.style.display = isGilles ? "" : "none");
  loadConnexions();
}

function _switchStab(tabName) {
  document.querySelectorAll(".stab-btn").forEach(b => b.classList.toggle("active", b.dataset.stab === tabName));
  document.querySelectorAll(".stab-panel").forEach(p => p.classList.remove("active"));
  if (tabName === "general") {
    document.getElementById("stab-general")?.classList.add("active");
    loadSettings();
  } else {
    document.getElementById("stab-connexions")?.classList.add("active");
    _setConnUser(tabName === "pam" ? "pam" : "gilles");
  }
}

async function initSettingsTabs() {
  const tabsEl = document.getElementById("settings-tabs");
  if (currentProfile?.admin) {
    tabsEl?.classList.remove("hidden");
    // Wire tab buttons
    document.querySelectorAll(".stab-btn").forEach(btn => {
      btn.onclick = () => _switchStab(btn.dataset.stab);
    });
    // Default: Général tab
    _switchStab("general");
  } else {
    // Non-admin: show connexions panel directly, no tabs
    tabsEl?.classList.add("hidden");
    document.getElementById("stab-general")?.classList.remove("active");
    document.getElementById("stab-connexions")?.classList.add("active");
    _setConnUser(currentProfile.id);
  }
}

// ── GitHub ───────────────────────────────────────────────────────
const ghSaveBtn = document.getElementById("gh-save-btn");
const ghMsg = document.getElementById("gh-msg");

if (ghSaveBtn) {
  ghSaveBtn.addEventListener("click", async () => {
    const body = {
      enabled: document.getElementById("gh-enabled")?.checked || false,
      token: document.getElementById("gh-token")?.value.trim() || "",
      repo_owner: document.getElementById("gh-repo-owner")?.value.trim() || "",
      repo_name: document.getElementById("gh-repo-name")?.value.trim() || "",
    };
    ghSaveBtn.disabled = true;
    const res = await safeFetch(`/api/connexions/github?token=${enc(token)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    ghSaveBtn.disabled = false;
    showConnMsg(ghMsg, !!res, "✓ Saved", "✗ Failed");
  });
}

// ── Linear ───────────────────────────────────────────────────────
const linearSaveBtn = document.getElementById("linear-save-btn");
const linearMsg = document.getElementById("linear-msg");

if (linearSaveBtn) {
  linearSaveBtn.addEventListener("click", async () => {
    const body = {
      enabled: document.getElementById("linear-enabled")?.checked || false,
      api_key: document.getElementById("linear-api-key")?.value.trim() || "",
    };
    linearSaveBtn.disabled = true;
    const res = await safeFetch(`/api/connexions/linear?token=${enc(token)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    linearSaveBtn.disabled = false;
    showConnMsg(linearMsg, !!res, "✓ Saved", "✗ Failed");
  });
}

// ── Gmail ────────────────────────────────────────────────────────
async function loadGmailStatus() {
  const status = await safeFetch(`/api/gmail/status?token=${enc(token)}`);
  if (!status) return;
  const badge = document.getElementById("gmail-status-badge");
  const connectBtn = document.getElementById("gmail-connect-btn");
  const disconnectBtn = document.getElementById("gmail-disconnect-btn");
  const clientIdIn = document.getElementById("gmail-client-id");
  const secretIn = document.getElementById("gmail-client-secret");
  if (clientIdIn && status.client_id) clientIdIn.value = status.client_id;
  if (secretIn && status.has_secret) secretIn.value = "••••••••••••";
  if (badge) {
    if (status.connected) {
      badge.textContent = `✓ Connected${status.email ? " — " + status.email : ""}`;
      badge.className = "conn-status-badge connected";
    } else {
      badge.textContent = "Not connected";
      badge.className = "conn-status-badge disconnected";
    }
  }
  if (connectBtn) connectBtn.style.display = status.connected ? "none" : "";
  if (disconnectBtn) disconnectBtn.style.display = status.connected ? "" : "none";
}

const gmailSaveBtn = document.getElementById("gmail-save-btn");
const gmailConnectBtn = document.getElementById("gmail-connect-btn");
const gmailDisconnectBtn = document.getElementById("gmail-disconnect-btn");
const gmailMsg = document.getElementById("gmail-msg");

if (gmailSaveBtn) {
  gmailSaveBtn.addEventListener("click", async () => {
    const clientId = document.getElementById("gmail-client-id")?.value.trim() || "";
    const secret = document.getElementById("gmail-client-secret")?.value.trim() || "";
    gmailSaveBtn.disabled = true;
    const res = await safeFetch(`/api/gmail/credentials?token=${enc(token)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId, client_secret: secret }),
    });
    gmailSaveBtn.disabled = false;
    showConnMsg(gmailMsg, !!res, "✓ Credentials saved", "✗ Failed");
  });
}

if (gmailConnectBtn) {
  gmailConnectBtn.addEventListener("click", async () => {
    const redirectUri = window.location.origin + "/api/gmail/callback";
    const res = await safeFetch(`/api/gmail/auth/start?token=${enc(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_uri: redirectUri }),
    });
    if (!res?.url) {
      showConnMsg(gmailMsg, false, "", "✗ Failed — save credentials first");
      return;
    }
    const popup = window.open(res.url, "gmail-auth", "width=600,height=700");
    showConnMsg(gmailMsg, true, "Waiting for authorization…", "");
    // Poll status until connected or popup closed
    const poll = setInterval(async () => {
      if (popup && popup.closed) clearInterval(poll);
      const st = await safeFetch(`/api/gmail/status?token=${enc(token)}`);
      if (st?.connected) {
        clearInterval(poll);
        if (popup && !popup.closed) popup.close();
        loadGmailStatus();
        showConnMsg(gmailMsg, true, "✓ Gmail connected!", "");
      }
    }, 2000);
  });
}

if (gmailDisconnectBtn) {
  gmailDisconnectBtn.addEventListener("click", async () => {
    const res = await safeFetch(`/api/gmail/disconnect?token=${enc(token)}`, { method: "DELETE" });
    if (res) {
      loadGmailStatus();
      showConnMsg(gmailMsg, true, "✓ Disconnected", "");
    }
  });
}

// ── Google Tasks ─────────────────────────────────────────────────
async function loadGtasksStatus() {
  const status = await safeFetch(`/api/google-tasks/status?token=${enc(token)}`);
  if (!status) return;
  const badge = document.getElementById("gtasks-status-badge");
  const connectBtn = document.getElementById("gtasks-connect-btn");
  const disconnectBtn = document.getElementById("gtasks-disconnect-btn");
  const clientIdIn = document.getElementById("gtasks-client-id");
  const secretIn = document.getElementById("gtasks-client-secret");
  if (badge) {
    badge.textContent = status.connected ? "● Connected" : "○ Not connected";
    badge.style.color = status.connected ? "#4caf50" : "var(--text-dim)";
  }
  if (connectBtn) connectBtn.style.display = status.connected ? "none" : "";
  if (disconnectBtn) disconnectBtn.style.display = status.connected ? "" : "none";
  if (clientIdIn && status.client_id) clientIdIn.value = status.client_id;
  if (secretIn && status.has_secret) secretIn.placeholder = "••••••••";
}

const gtasksConnectBtn = document.getElementById("gtasks-connect-btn");
const gtasksDisconnectBtn = document.getElementById("gtasks-disconnect-btn");
const gtasksMsg = document.getElementById("gtasks-msg");

if (gtasksConnectBtn) {
  gtasksConnectBtn.addEventListener("click", async () => {
    const res = await safeFetch(`/api/google-tasks/auth/start?token=${enc(token)}`, { method: "POST" });
    if (!res?.url) {
      showConnMsg(gtasksMsg, false, "", "✗ Failed — save credentials first");
      return;
    }
    const popup = window.open(res.url, "gtasks-auth", "width=600,height=700");
    showConnMsg(gtasksMsg, true, "Waiting for authorization…", "");
    const poll = setInterval(async () => {
      if (popup && popup.closed) {
        clearInterval(poll);
        const st = await safeFetch(`/api/google-tasks/status?token=${enc(token)}`);
        if (st?.connected) {
          loadGtasksStatus();
          showConnMsg(gtasksMsg, true, "✓ Google Tasks connected!", "");
        } else {
          showConnMsg(gtasksMsg, false, "", "✗ Not connected — try again");
        }
      }
    }, 1000);
  });
}

if (gtasksDisconnectBtn) {
  gtasksDisconnectBtn.addEventListener("click", async () => {
    const res = await safeFetch(`/api/google-tasks/disconnect?token=${enc(token)}`, { method: "DELETE" });
    if (res) {
      loadGtasksStatus();
      showConnMsg(gtasksMsg, true, "✓ Disconnected", "");
    }
  });
}

async function loadMcpCatalog() {
  const catalog = await safeFetch(`/api/connexions/catalog?token=${enc(token)}`);
  if (!catalog) return;
  const container = document.getElementById("mcp-catalog");
  if (!container) return;
  container.innerHTML = catalog.map(item => `
    <div class="mcp-catalog-item">
      <div class="mcp-catalog-icon">${item.icon || "🔌"}</div>
      <div class="mcp-catalog-info">
        <div class="mcp-catalog-name">${item.name}</div>
        <div class="mcp-catalog-desc">${item.description || ""}</div>
        ${item.env_required ? `<div class="mcp-env-required">Env: ${item.env_required.join(", ")}</div>` : ""}
      </div>
      <button class="btn-secondary mcp-add-catalog-btn" data-catalog-id="${item.id}">+ Add</button>
    </div>
  `).join("");

  container.querySelectorAll(".mcp-add-catalog-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.catalogId;
      const item = catalog.find(c => c.id === id);
      if (!item) return;
      const env = {};
      if (item.env_required) {
        for (const k of item.env_required) {
          const val = prompt(`Value for ${k}:`);
          if (val === null) return;
          env[k] = val;
        }
      }
      btn.disabled = true; btn.textContent = "Adding…";
      const res = await safeFetch(`/api/connexions/mcps?token=${enc(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id, name: item.name, type: item.type,
          url: item.url || "", command: item.command || "", env, enabled: true }),
      });
      if (res) {
        btn.textContent = "✓ Added";
        const mcps = await safeFetch(`/api/connexions/mcps?token=${enc(token)}`);
        if (mcps) renderMcps(mcps);
      } else {
        btn.textContent = "✗ Failed"; btn.disabled = false;
      }
    });
  });
}

function renderMcps(mcps) {
  const container = document.getElementById("mcps-list");
  if (!container) return;
  if (!mcps.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:.83rem;padding:.3rem 0">No MCP connections yet — browse the catalog below.</div>';
    return;
  }
  container.innerHTML = mcps.map(m => `
    <div class="mcp-row">
      <div class="mcp-row-info">
        <span class="mcp-row-name">${m.name}</span>
        <span class="mcp-row-type">${m.type}</span>
      </div>
      <div class="mcp-row-actions">
        <label class="mcp-toggle-label">
          <input type="checkbox" class="mcp-enabled-toggle" data-mcp-id="${m.id}" ${m.enabled ? "checked" : ""} />
          <span>${m.enabled ? "on" : "off"}</span>
        </label>
        <button class="icon-btn mcp-delete-btn" data-mcp-id="${m.id}" title="Remove">✕</button>
      </div>
    </div>
  `).join("");

  container.querySelectorAll(".mcp-enabled-toggle").forEach(toggle => {
    toggle.addEventListener("change", async () => {
      const id = toggle.dataset.mcpId;
      await safeFetch(`/api/connexions/mcps/${id}?token=${enc(token)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: toggle.checked }),
      });
      toggle.nextElementSibling.textContent = toggle.checked ? "on" : "off";
    });
  });

  container.querySelectorAll(".mcp-delete-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Remove this MCP connection?")) return;
      await safeFetch(`/api/connexions/mcps/${btn.dataset.mcpId}?token=${enc(token)}`, { method: "DELETE" });
      const mcps = await safeFetch(`/api/connexions/mcps?token=${enc(token)}`);
      if (mcps !== null) renderMcps(mcps || []);
    });
  });
}

function showConnMsg(el, ok, successMsg = "✓ Saved", errorMsg = "✗ Failed") {
  if (!el) return;
  el.textContent = ok ? successMsg : errorMsg;
  el.style.color = ok ? "var(--ok)" : "var(--err)";
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 3000);
}

// Telegram save/test
const tgSaveBtn = document.getElementById("tg-save-btn");
const tgTestBtn = document.getElementById("tg-test-btn");
const tgMsg = document.getElementById("tg-msg");

if (tgSaveBtn) {
  tgSaveBtn.addEventListener("click", async () => {
    tgSaveBtn.disabled = true;
    const res = await safeFetch(`/api/connexions/telegram?token=${enc(token)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: document.getElementById("tg-enabled").checked,
        bot_token: document.getElementById("tg-token").value.trim(),
        chat_id: document.getElementById("tg-chat-id").value.trim(),
      }),
    });
    tgSaveBtn.disabled = false;
    showConnMsg(tgMsg, res?.status === "ok");
  });
}

if (tgTestBtn) {
  tgTestBtn.addEventListener("click", async () => {
    tgTestBtn.disabled = true;
    const res = await safeFetch(`/api/connexions/test/telegram?token=${enc(token)}`, { method: "POST" });
    tgTestBtn.disabled = false;
    showConnMsg(tgMsg, res?.ok === true, "✓ Message sent!", "✗ Failed — check token and chat ID");
  });
}

// Email save/test
const emailSaveBtn = document.getElementById("email-save-btn");
const emailTestBtn = document.getElementById("email-test-btn");
const emailMsg = document.getElementById("email-msg");

if (emailSaveBtn) {
  emailSaveBtn.addEventListener("click", async () => {
    emailSaveBtn.disabled = true;
    const res = await safeFetch(`/api/connexions/email?token=${enc(token)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: document.getElementById("email-enabled").checked,
        smtp_host: document.getElementById("email-host").value.trim(),
        smtp_port: parseInt(document.getElementById("email-port").value) || 587,
        smtp_user: document.getElementById("email-user").value.trim(),
        smtp_password: document.getElementById("email-password").value,
        from_name: document.getElementById("email-from-name").value.trim(),
        from_email: document.getElementById("email-from-email").value.trim(),
        notify_email: document.getElementById("email-notify-email").value.trim(),
      }),
    });
    emailSaveBtn.disabled = false;
    showConnMsg(emailMsg, res?.status === "ok");
  });
}

if (emailTestBtn) {
  emailTestBtn.addEventListener("click", async () => {
    emailTestBtn.disabled = true;
    const res = await safeFetch(`/api/connexions/test/email?token=${enc(token)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    emailTestBtn.disabled = false;
    showConnMsg(emailMsg, res?.ok === true, "✓ Email sent!", "✗ Failed — check SMTP config");
  });
}

// ── Social ───────────────────────────────────────────────────────
const socialSaveBtn = document.getElementById("social-save-btn");
const socialMsg = document.getElementById("social-msg");

if (socialSaveBtn) {
  socialSaveBtn.addEventListener("click", async () => {
    socialSaveBtn.disabled = true;
    const forUser = _getForUser();
    const res = await safeFetch(`/api/connexions/social?token=${enc(token)}&for_user=${forUser}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reddit: {
          enabled: document.getElementById("reddit-enabled")?.checked || false,
          username: document.getElementById("reddit-username")?.value.trim() || "",
        },
        bluesky: {
          enabled: document.getElementById("bsky-enabled")?.checked || false,
          handle: document.getElementById("bsky-handle")?.value.trim() || "",
          app_password: document.getElementById("bsky-app-password")?.value || "",
        },
        twitter: {
          enabled: document.getElementById("twitter-enabled")?.checked || false,
          consumer_key: document.getElementById("twitter-consumer-key")?.value.trim() || "",
          consumer_secret: document.getElementById("twitter-consumer-secret")?.value || "",
          access_token: document.getElementById("twitter-access-token")?.value || "",
          access_token_secret: document.getElementById("twitter-access-token-secret")?.value || "",
          bearer_token: document.getElementById("twitter-bearer-token")?.value || "",
        },
      }),
    });
    socialSaveBtn.disabled = false;
    showConnMsg(socialMsg, res?.status === "ok");
  });
}

// ── Leclerc ──────────────────────────────────────────────────────
const leclercSaveBtn = document.getElementById("leclerc-save-btn");
const leclercMsg = document.getElementById("leclerc-msg");

if (leclercSaveBtn) {
  leclercSaveBtn.addEventListener("click", async () => {
    leclercSaveBtn.disabled = true;
    const forUser = _getForUser();
    const res = await safeFetch(`/api/connexions/leclerc?token=${enc(token)}&for_user=${forUser}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: document.getElementById("leclerc-enabled")?.checked || false,
        email: document.getElementById("leclerc-email")?.value.trim() || "",
        password: document.getElementById("leclerc-password")?.value || "",
      }),
    });
    leclercSaveBtn.disabled = false;
    showConnMsg(leclercMsg, res?.status === "ok");
  });
}

// ── Notion ──────────────────────────────────────────────────────
const notionSaveBtn = document.getElementById("notion-save-btn");
const notionMsg = document.getElementById("notion-msg");

if (notionSaveBtn) {
  notionSaveBtn.addEventListener("click", async () => {
    notionSaveBtn.disabled = true;
    const forUser = _getForUser();
    const res = await safeFetch(`/api/connexions/notion?token=${enc(token)}&for_user=${forUser}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: document.getElementById("notion-enabled")?.checked || false,
        api_key: document.getElementById("notion-api-key")?.value || "",
        database_id: document.getElementById("notion-database-id")?.value.trim() || "",
      }),
    });
    notionSaveBtn.disabled = false;
    showConnMsg(notionMsg, res?.status === "ok");
  });
}

// ── Browser ────────────────────────────────────────────────────
async function checkBrowserSession() {
  const res = await safeFetch(`/api/browser/status?token=${enc(token)}`);
  if (!res) return;
  if (!res.available && browserStatus) {
    browserStatus.textContent = "Playwright not available in this environment";
    return;
  }
  if (res.has_session) {
    // Restore existing session screenshot
    const snap = await safeFetch(`/api/browser/screenshot?token=${enc(token)}`);
    if (snap) browserApplyState(snap);
  }
}
const browserImg        = document.getElementById("browser-img");
const browserPlaceholder= document.getElementById("browser-placeholder");
const browserLoading    = document.getElementById("browser-loading");
const browserUrlInput   = document.getElementById("browser-url-input");
const browserTypeInput  = document.getElementById("browser-type-input");
const browserStatus     = document.getElementById("browser-status");
const browserCookiesOut = document.getElementById("browser-cookies-out");

let browserViewportW = 1280;
let browserViewportH = 800;

function browserSetLoading(on) {
  browserLoading?.classList.toggle("hidden", !on);
}

function browserApplyState(state) {
  if (!state) return;
  if (state.url && browserUrlInput) browserUrlInput.value = state.url;
  if (state.screenshot && browserImg) {
    browserImg.src = "data:image/png;base64," + state.screenshot;
    browserImg.classList.remove("hidden");
    browserPlaceholder?.classList.add("hidden");
    // Detect actual viewport from image
    browserImg.onload = () => {
      browserViewportW = browserImg.naturalWidth;
      browserViewportH = browserImg.naturalHeight;
    };
  }
  if (browserStatus) browserStatus.textContent = state.url || "";
}

async function browserNavigate(url) {
  if (!url) return;
  browserSetLoading(true);
  const state = await safeFetch(`/api/browser/navigate?token=${enc(token)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  browserSetLoading(false);
  browserApplyState(state);
}

async function browserAction(payload) {
  browserSetLoading(true);
  const state = await safeFetch(`/api/browser/action?token=${enc(token)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  browserSetLoading(false);
  browserApplyState(state);
}

if (document.getElementById("browser-go-btn")) {
  document.getElementById("browser-go-btn").addEventListener("click", () => {
    browserNavigate(browserUrlInput?.value.trim());
  });
  browserUrlInput?.addEventListener("keydown", e => {
    if (e.key === "Enter") browserNavigate(browserUrlInput.value.trim());
  });

  document.getElementById("browser-back-btn")?.addEventListener("click", () =>
    browserAction({ action: "back" }));
  document.getElementById("browser-refresh-btn")?.addEventListener("click", () =>
    browserAction({ action: "refresh" }));
  document.getElementById("browser-scroll-up-btn")?.addEventListener("click", () =>
    browserAction({ action: "scroll", delta_y: -400 }));
  document.getElementById("browser-scroll-down-btn")?.addEventListener("click", () =>
    browserAction({ action: "scroll", delta_y: 400 }));
  document.getElementById("browser-enter-btn")?.addEventListener("click", () =>
    browserAction({ action: "key", key: "Enter" }));
  document.getElementById("browser-tab-btn")?.addEventListener("click", () =>
    browserAction({ action: "key", key: "Tab" }));

  document.getElementById("browser-type-btn")?.addEventListener("click", () => {
    const text = browserTypeInput?.value || "";
    if (text) { browserAction({ action: "type", text }); browserTypeInput.value = ""; }
  });
  browserTypeInput?.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      const text = browserTypeInput.value;
      if (text) { browserAction({ action: "type", text }); browserTypeInput.value = ""; }
    }
  });

  // Click on screenshot → compute coords and send
  browserImg?.addEventListener("click", e => {
    const rect = browserImg.getBoundingClientRect();
    const scaleX = browserViewportW / rect.width;
    const scaleY = browserViewportH / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    browserAction({ action: "click", x, y });
  });

  // Export cookies
  document.getElementById("browser-cookies-btn")?.addEventListener("click", async () => {
    const res = await safeFetch(`/api/browser/cookies?token=${enc(token)}`);
    if (res?.cookies && browserCookiesOut) {
      browserCookiesOut.textContent = JSON.stringify(res.cookies, null, 2);
      browserCookiesOut.classList.remove("hidden");
      browserStatus.textContent = `${res.count} cookies exported`;
    }
  });

  // Close session
  document.getElementById("browser-close-btn")?.addEventListener("click", async () => {
    await safeFetch(`/api/browser/close?token=${enc(token)}`, { method: "DELETE" });
    browserImg?.classList.add("hidden");
    browserPlaceholder?.classList.remove("hidden");
    browserCookiesOut?.classList.add("hidden");
    if (browserUrlInput) browserUrlInput.value = "";
    if (browserStatus) browserStatus.textContent = "Session closed";
  });
}

// ── Service Worker ────────────────────────────────────────────
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(() => {});
}

// ── Start ──────────────────────────────────────────────────────
boot();
