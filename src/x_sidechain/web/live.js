/* Connects the page to the local engine. Without a running session it renders
   only configuration-backed or explicit empty states.

   This is a module on purpose: app.js is a classic script, so the two would
   otherwise share one global scope and collide over names such as `toast`. */

const params = new URLSearchParams(location.search);
const token = params.get("token") || sessionStorage.getItem("xsc-token") || "";
if (params.get("token")) {
  try { sessionStorage.setItem("xsc-token", token); } catch { /* private window */ }
  history.replaceState(null, "", location.pathname);
}

const api = {
  async get(path) {
    const response = await fetch(path, { headers: { "X-Sidechain-Token": token } });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || response.statusText);
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "X-Sidechain-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }
};

const el = {
  events: document.querySelector("#eventList"),
  phaseCounter: document.querySelector("#phaseCounter"),
  runState: document.querySelector("#runState"),
  revision: document.querySelector("#revisionNumber"),
  prompt: document.querySelector("#promptInput"),
  composer: document.querySelector("#composer"),
  hold: document.querySelector("#holdButton"),
  strip: document.querySelector("#agentStrip"),
  title: document.querySelector("#topTitle"),
  finish: document.querySelector("#finishButton"),
  chooseTeam: document.querySelector("#addAgentInline"),
  newSession: document.querySelector("#newSession"),
  setupDialog: document.querySelector("#sessionSetupDialog"),
  setupForm: document.querySelector("#sessionSetupForm"),
  setupOptions: document.querySelector("#sessionAgentOptions"),
  setupError: document.querySelector("#teamSetupError"),
  setupClose: document.querySelector("#closeSessionSetup"),
  setupCancel: document.querySelector("#cancelSessionSetup"),
  phases: [...document.querySelectorAll("#phaseTrack li")]
};

function toast(message) {
  const node = document.querySelector("#toast");
  if (!node) return;
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 3200);
}

function translated(key, fallback) {
  return window.xscTranslate?.(key) || fallback;
}

function setPhase(phase) {
  el.phases.forEach((item, index) => {
    item.classList.toggle("done", index + 1 < phase);
    item.classList.toggle("current", index + 1 === phase);
  });
  if (el.phaseCounter) el.phaseCounter.textContent = `phase ${phase} of 7`;
}

const KIND_LABEL = {
  "task": "TASK", "agent.summary": "PUBLIC BRIEF", "chair.question": "CHAIR QUESTION",
  "agent.clarification": "CLARIFICATION", "chair.draft": "CHAIR DRAFT",
  "agent.review": "PEER REVIEW", "chair.final": "FINAL RESULT",
  "agent.abstention": "ABSTAINED", "user.steering": "CORRECTION"
};

function renderEvent(event) {
  const card = document.createElement("article");
  card.className = "event-card";
  if (event.kind === "chair.question" || event.kind === "chair.draft" || event.kind === "chair.final") {
    card.classList.add("chair-event");
  }
  const meta = document.createElement("div");
  meta.className = "event-meta";
  const avatar = document.createElement("span");
  avatar.className = "avatar small";
  avatar.textContent = event.author.slice(0, 2).toUpperCase();
  const author = document.createElement("strong");
  author.textContent = event.author;
  const tag = document.createElement("span");
  tag.className = event.kind === "agent.abstention" ? "tag warning" : "tag blue";
  tag.textContent = KIND_LABEL[event.kind] || event.kind;
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  meta.append(avatar, author, tag, time);

  const body = document.createElement("p");
  body.style.whiteSpace = "pre-wrap";
  body.textContent = event.content;

  const foot = document.createElement("div");
  foot.className = "event-foot";
  const rev = document.createElement("span");
  rev.className = "tag warning";
  rev.textContent = `Revision ${event.revision}`;
  const seq = document.createElement("span");
  seq.textContent = `#${event.sequence}`;
  foot.append(rev, seq);

  card.append(meta, body, foot);
  return card;
}

function appendEvent(event) {
  const card = renderEvent(event);
  el.events.append(card);
  if (el.revision) el.revision.textContent = String(event.revision);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* A session that is running takes corrections; otherwise the composer starts
   the next one. The page is the same either way. */
let running = false;
let liveConfig = null;
let selectedAgents = new Set();
let chairId = "";

function setRunning(value, label) {
  running = value;
  if (el.runState) el.runState.textContent = label;
  if (el.finish) el.finish.disabled = !value;
  if (el.hold) el.hold.disabled = !value;
  if (el.chooseTeam) el.chooseTeam.disabled = value;
}

function renderRoster(config) {
  if (!el.strip) return;
  const addButton = el.strip.querySelector(".add-agent");
  el.strip.querySelectorAll(".agent-chip").forEach((chip) => chip.remove());
  const chosen = config.agents.filter((agent) => selectedAgents.has(agent.id));
  const chips = chosen.map((agent) => {
    const chip = document.createElement("button");
    chip.className = agent.id === chairId ? "agent-chip chair" : "agent-chip";
    chip.type = "button";
    chip.dataset.agent = agent.id;

    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = agent.id.slice(0, 2).toUpperCase();

    const label = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = agent.id;
    const detail = document.createElement("small");
    /* The role is what the agent was hired for; the model is what answers. */
    detail.textContent = `${agent.id === chairId ? "Chair" : agent.role || "Agent"} \u00b7 ${agent.model}`;
    label.append(name, detail);

    const dot = document.createElement("span");
    const provider = config.providers.find((entry) => entry.id === agent.provider);
    dot.className = provider && provider.base_url.includes("127.0.0.1")
      ? "online-dot local" : "online-dot";

    chip.append(avatar, label, dot);
    return chip;
  });
  el.strip.prepend(...chips);
  if (addButton) el.strip.append(addButton);
  setText("#agentCount", String(chosen.length));
  setText("#spendQuorum", `${chosen.length} agents`);
}

function initializeTeam(config) {
  liveConfig = config;
  if (!selectedAgents.size) {
    config.agents.forEach((agent) => selectedAgents.add(agent.id));
  }
  if (!chairId || !selectedAgents.has(chairId)) {
    chairId = config.chair || config.agents.find((agent) => agent.chair)?.id || config.agents[0]?.id || "";
  }
  renderRoster(config);
  /* The page ships no version of its own, so it cannot disagree with the engine. */
  if (config.version) setText(".version", `v${config.version} \u00b7 Linux`);
  renderAgentLibrary(config);
  renderProviderSettings(config);
  renderRuntimeSettings(config);
}

function appendEmpty(container, message) {
  const note = document.createElement("p");
  note.className = "empty-room";
  note.textContent = message;
  container.append(note);
}

function renderAgentLibrary(config) {
  const library = document.querySelector("#agentLibrary");
  if (!library) return;
  library.replaceChildren();
  if (!Array.isArray(config.agents) || !config.agents.length) {
    appendEmpty(library, "No agent configuration is loaded.");
    return;
  }
  config.agents.forEach((agent) => {
    const card = document.createElement("article");
    card.className = agent.chair ? "config-card featured" : "config-card";
    const top = document.createElement("div");
    top.className = "card-top";
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = agent.id.slice(0, 2).toUpperCase();
    const state = document.createElement("span");
    state.className = agent.chair ? "tag blue" : "tag neutral";
    state.textContent = agent.chair ? "Chair" : "Member";
    top.append(avatar, state);
    const name = document.createElement("h2");
    name.textContent = agent.id;
    const role = document.createElement("p");
    role.textContent = agent.role || "No role description configured.";
    const facts = document.createElement("dl");
    for (const [label, value] of [["Provider", agent.provider], ["Model", agent.model]]) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      row.append(term, detail);
      facts.append(row);
    }
    card.append(top, name, role, facts);
    library.append(card);
  });
}

function setValue(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.value = value;
}

function renderRuntimeSettings(config) {
  const limits = config.limits || {};
  setValue("#settingChair", config.chair || "—");
  setValue("#settingClarifications", String(limits.max_clarification_questions ?? "—"));
  setValue("#settingCallBudget", String(limits.max_model_calls ?? "—"));
  setValue("#settingBriefLimit", limits.max_public_brief_chars == null
    ? "—" : `${limits.max_public_brief_chars} characters`);
  setValue("#settingRevisionLimit", String(limits.max_revisions ?? "—"));
  setValue("#settingDeadline", limits.session_deadline_seconds == null
    ? "—" : `${limits.session_deadline_seconds} seconds`);
}

function renderSessionList(state) {
  const rows = document.querySelector("#sessionRows");
  if (!rows) return;
  rows.replaceChildren();
  const hasSession = state && state.status && state.status !== "idle";
  setText("#sessionCount", hasSession ? "1" : "0");
  if (!hasSession) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    appendEmpty(cell, "No session has run in this process.");
    row.append(cell);
    rows.append(row);
    return;
  }
  const row = document.createElement("tr");
  const task = document.createElement("td");
  const title = document.createElement("strong");
  title.textContent = state.task || "Untitled task";
  const id = document.createElement("small");
  id.textContent = state.session_id || "Session id assigned on completion";
  task.append(title, id);
  const status = document.createElement("td");
  const statusTag = document.createElement("span");
  statusTag.className = state.status === "failed" ? "tag warning" :
    state.status === "completed" ? "tag green" : "tag blue";
  statusTag.textContent = state.status;
  status.append(statusTag);
  const agents = document.createElement("td");
  agents.textContent = String(state.agents?.length || 0);
  const revision = document.createElement("td");
  revision.textContent = String(state.spend?.revision ?? 0);
  const audit = document.createElement("td");
  audit.textContent = state.audit_path ? "Available" : "Pending";
  row.append(task, status, agents, revision, audit);
  rows.append(row);
}

function renderWorkspace(state) {
  const list = document.querySelector("#workspaceList");
  if (!list) return;
  list.replaceChildren();
  if (!state?.workspace_root) {
    appendEmpty(list, "No session workspace exists in this process.");
    return;
  }
  const card = document.createElement("article");
  card.className = "config-card wide-card";
  const top = document.createElement("div");
  top.className = "card-top";
  const heading = document.createElement("div");
  const tag = document.createElement("span");
  tag.className = "tag green";
  tag.textContent = "Created";
  const title = document.createElement("h2");
  title.textContent = state.session_id || "Current session";
  heading.append(tag, title);
  top.append(heading);
  const path = document.createElement("p");
  path.className = "path";
  path.textContent = state.workspace_root;
  const audit = document.createElement("p");
  audit.className = "helper";
  audit.textContent = state.audit_path ? `Audit: ${state.audit_path}` : "Audit path pending";
  card.append(top, path, audit);
  list.append(card);
}

function renderProcessState(state) {
  renderSessionList(state);
  renderWorkspace(state);
  const auditButton = document.querySelector("#viewAudit");
  if (auditButton) {
    auditButton.dataset.auditPath = state?.audit_path || "";
    auditButton.disabled = !state?.audit_path;
  }
  const auditState = document.querySelector("#auditState span:last-child");
  if (auditState) auditState.textContent = state?.audit_path ? "Audit available" : "No active audit";
}

function renderProviderSettings(config) {
  const list = document.querySelector("#providerSettingsList");
  if (!list || !Array.isArray(config.providers)) return;
  list.replaceChildren();
  config.providers.forEach((provider) => {
    const row = document.createElement("article");
    row.className = "setting-group provider-row";
    const identity = document.createElement("div");
    const badge = document.createElement("span");
    badge.className = "provider-badge";
    badge.textContent = provider.id.slice(0, 1).toUpperCase();
    const copy = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = provider.id;
    const detail = document.createElement("p");
    detail.textContent = provider.auth === "chatgpt_account"
      ? `ChatGPT account · ${provider.protocol}`
      : `${provider.auth} · ${provider.protocol}`;
    copy.append(name, detail);
    identity.append(badge, copy);

    const state = document.createElement("span");
    const local = provider.base_url?.startsWith("http://127.0.0.1") ||
      provider.base_url?.startsWith("http://localhost");
    const accountChecking = provider.auth === "chatgpt_account" && provider.account_checking;
    const accountReady = provider.auth === "chatgpt_account" && provider.account_authenticated;
    const envReady = provider.auth === "api_key" && provider.env_present;
    const ready = accountReady || envReady || provider.auth === "none";
    state.className = ready ? "tag green" : "tag warning";
    state.textContent = accountChecking ? "Checking…" : local ? "Local" : ready ? "Connected" :
      provider.account_available === false ? "Codex missing" : "Sign in required";

    const action = document.createElement("button");
    action.className = "button secondary";
    action.type = "button";
    if (provider.auth === "chatgpt_account") {
      const command = `x-sidechain auth login ${provider.id} --config YOUR_CONFIG.json`;
      action.textContent = accountChecking ? "Checking account" :
        accountReady ? "Account connected" : "Copy login command";
      action.disabled = accountReady || accountChecking;
      action.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(command); } catch { /* clipboard may be denied */ }
        toast(command);
      });
    } else {
      action.textContent = "Configured in file";
      action.disabled = true;
    }
    row.append(identity, state, action);
    list.append(row);
  });
}

function renderTeamOptions() {
  if (!liveConfig || !el.setupOptions) return;
  el.setupOptions.replaceChildren();
  liveConfig.agents.forEach((agent) => {
    const row = document.createElement("div");
    row.className = "team-option";

    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.className = "team-enabled";
    enabled.value = agent.id;
    enabled.checked = selectedAgents.has(agent.id);
    enabled.setAttribute("aria-label", `${translated("action.team", "Choose team")}: ${agent.id}`);

    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = agent.id.slice(0, 2).toUpperCase();

    const copy = document.createElement("span");
    copy.className = "team-option-copy";
    const name = document.createElement("strong");
    name.textContent = agent.id;
    const detail = document.createElement("small");
    detail.textContent = `${agent.role || "Agent"} \u00b7 ${agent.provider} / ${agent.model}`;
    copy.append(name, detail);

    const chair = document.createElement("label");
    chair.className = "chair-choice";
    const chairRadio = document.createElement("input");
    chairRadio.type = "radio";
    chairRadio.name = "session-chair";
    chairRadio.value = agent.id;
    chairRadio.checked = agent.id === chairId;
    chairRadio.disabled = !enabled.checked;
    const chairText = document.createElement("span");
    chairText.textContent = translated("team.chair", "Chair");
    chair.append(chairRadio, chairText);

    enabled.addEventListener("change", () => {
      chairRadio.disabled = !enabled.checked;
      if (!enabled.checked && chairRadio.checked) {
        const replacement = el.setupOptions.querySelector(".team-enabled:checked")
          ?.closest(".team-option")?.querySelector('input[name="session-chair"]');
        if (replacement) replacement.checked = true;
      }
    });
    row.append(enabled, avatar, copy, chair);
    el.setupOptions.append(row);
  });
}

function openTeamDialog() {
  if (!liveConfig || !el.setupDialog) return;
  if (running) {
    toast("The team is locked while a session is running.");
    return;
  }
  if (el.setupError) {
    el.setupError.textContent = "";
    el.setupError.hidden = true;
  }
  renderTeamOptions();
  el.setupDialog.showModal();
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value;
}

function applySpend(spend) {
  if (!spend) return;
  setText("#spendCalls", `${spend.model_calls} / ${spend.max_model_calls}`);
  const left = Math.max(spend.max_revisions - spend.revision, 0);
  setText("#spendRevisions", left === 1 ? "1 available" : `${left} available`);
  const tokens = Object.entries(spend.usage || {})
    .filter(([key]) => key.includes("token"))
    .reduce((total, [, value]) => total + value, 0);
  setText("#spendTokens", tokens ? tokens.toLocaleString() : "\u2014");
  const bar = document.querySelector("#budgetBar");
  if (bar) {
    const spent = spend.max_model_calls
      ? Math.min(spend.model_calls / spend.max_model_calls, 1) : 0;
    bar.style.width = `${Math.round(spent * 100)}%`;
  }
}

function takeOver(state) {
  window.__xscLive = true;
  if (Array.isArray(state.agents) && state.agents.length) {
    selectedAgents = new Set(state.agents);
    chairId = state.chair || chairId;
    if (liveConfig) renderRoster(liveConfig);
  }
  if (el.title && state.task) el.title.textContent = state.task;
  el.events.replaceChildren();
  state.events.forEach(appendEvent);
  setPhase(state.phase || 1);
  applySpend(state.spend);
  renderProcessState(state);
  setRunning(state.status === "running", state.status);
}

function emptyRoom(message) {
  const note = document.createElement("p");
  note.className = "empty-room";
  note.textContent = message;
  el.events.replaceChildren(note);
}

function idle(config) {
  /* Live, but nothing is running: clear the sample room rather than leave
     invented events on screen, and let the composer open the next session. */
  window.__xscLive = true;
  if (el.title) el.title.textContent = "No session";
  el.events.replaceChildren();
  emptyRoom("No session is running. Describe the task below to open one.");
  setPhase(1);
  if (el.revision) el.revision.textContent = "0";
  applySpend({
    model_calls: 0,
    max_model_calls: config.limits.max_model_calls,
    revision: 0,
    max_revisions: config.limits.max_revisions,
    usage: {}
  });
  setRunning(false, "Idle");
  renderProcessState({ status: "idle" });
}

async function begin(task) {
  emptyRoom("Opening the session…");
  await api.post("/api/run", { task, agents: [...selectedAgents], chair: chairId });
  setRunning(true, "Running");
  if (el.title) el.title.textContent = task;
  listen();
}

if (el.chooseTeam) {
  el.chooseTeam.addEventListener("click", (event) => {
    if (!liveConfig) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openTeamDialog();
  }, true);
}

if (el.setupForm) {
  el.setupForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const agents = [...el.setupOptions.querySelectorAll(".team-enabled:checked")]
      .map((input) => input.value);
    const chair = el.setupOptions.querySelector('input[name="session-chair"]:checked:not(:disabled)')?.value || "";
    if (agents.length < 2) {
      if (el.setupError) {
        el.setupError.textContent = "Choose at least two agents.";
        el.setupError.hidden = false;
      }
      return;
    }
    if (!chair || !agents.includes(chair)) {
      if (el.setupError) {
        el.setupError.textContent = "Choose a chair from the enabled agents.";
        el.setupError.hidden = false;
      }
      return;
    }
    selectedAgents = new Set(agents);
    chairId = chair;
    renderRoster(liveConfig);
    el.setupDialog.close();
    toast(`${agents.length} agents selected; ${chair} is the chair.`);
  });
}

[el.setupClose, el.setupCancel].forEach((button) => {
  button?.addEventListener("click", () => el.setupDialog?.close());
});

function listen() {
  const source = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
  source.addEventListener("state", (message) => takeOver(JSON.parse(message.data)));
  source.addEventListener("room", (message) => {
    appendEvent(JSON.parse(message.data));
    api.get("/api/state").then((state) => {
      applySpend(state.spend);
      renderProcessState(state);
    }).catch(() => {});
  });
  source.addEventListener("progress", (message) => {
    const payload = JSON.parse(message.data);
    setPhase(payload.phase);
    if (el.runState) el.runState.textContent = payload.message;
  });
  source.addEventListener("done", (message) => {
    const payload = JSON.parse(message.data);
    setPhase(7);
    setRunning(false, "Completed");
    /* The run's own final snapshot. Deriving the budget from the calls it made
       would show every finished session as having spent all of it. */
    applySpend(payload.spend);
    toast(`Session complete — ${payload.model_calls} model calls.`);
    api.get("/api/state").then(renderProcessState).catch(() => {});
    source.close();
  });
  source.addEventListener("failed", (message) => {
    toast(`Session failed: ${JSON.parse(message.data).error}`);
    setRunning(false, "Failed");
    api.get("/api/state").then(renderProcessState).catch(() => {});
    source.close();
  });
  source.onerror = () => source.close();
}

if (el.composer) {
  el.composer.addEventListener("submit", async (event) => {
    if (!window.__xscLive) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const message = el.prompt.value.trim();
    if (!message) return;
    try {
      if (running) {
        const result = await api.post("/api/steer", { message });
        toast(`Correction accepted — revision ${result.revision} restarts the cycle.`);
      } else {
        await begin(message);
      }
      el.prompt.value = "";
    } catch (error) {
      toast(error.message);
    }
  }, true);
}

if (el.finish) {
  el.finish.addEventListener("click", async (event) => {
    if (!window.__xscLive) return;
    event.stopImmediatePropagation();
    try {
      await api.post("/api/finish", {});
      toast("Input closed. The running cycle will finish.");
    } catch (error) {
      toast(error.message);
    }
  }, true);
}

async function start() {
  if (!token) {
    setRunning(false, "Offline");
    if (el.chooseTeam) el.chooseTeam.disabled = true;
    if (el.prompt) el.prompt.disabled = true;
    return;
  }
  const config = await api.get("/api/config");
  if (!config.live) {
    window.__xscLive = true;
    emptyRoom("No configuration is loaded. Restart the local UI with --config.");
    setRunning(false, "No configuration");
    renderProcessState({ status: "idle" });
    return;
  }
  initializeTeam(config);
  pollProviderStatus(config);
  const deadline = config.limits.session_deadline_seconds;
  setText("#spendDeadline", deadline ? `${Math.round(deadline / 60)} min` : "none");
  const state = await api.get("/api/state");
  if (state.status && state.status !== "idle") {
    takeOver(state);
    listen();
  } else {
    idle(config);
  }
}

async function pollProviderStatus(config, attempt = 0) {
  if (!config.providers?.some((provider) => provider.account_checking) || attempt >= 24) return;
  await new Promise((resolve) => setTimeout(resolve, 500));
  try {
    const refreshed = await api.get("/api/config");
    liveConfig = refreshed;
    renderProviderSettings(refreshed);
    await pollProviderStatus(refreshed, attempt + 1);
  } catch { /* The main startup/error path reports connectivity failures. */ }
}

start().catch((error) => {
  /* A refused token or a dead server is worth saying out loud. */
  console.error("live layer did not start:", error);
  toast(`Live view unavailable: ${error.message}`);
});
