/* Connects the page to the local engine. Without a running session it stays out
   of the way and the prototype content is left exactly as it is.

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
  phases: [...document.querySelectorAll("#phaseTrack li")]
};

function toast(message) {
  const node = document.querySelector("#toast");
  if (!node) return;
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 3200);
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

function setRunning(value, label) {
  running = value;
  if (el.runState) el.runState.textContent = label;
  if (el.finish) el.finish.disabled = !value;
}

function renderRoster(config) {
  if (!el.strip) return;
  const addButton = el.strip.querySelector(".add-agent");
  el.strip.querySelectorAll(".agent-chip").forEach((chip) => chip.remove());
  const chips = config.agents.map((agent) => {
    const chip = document.createElement("button");
    chip.className = agent.chair ? "agent-chip chair" : "agent-chip";
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
    detail.textContent = `${agent.chair ? "Chair" : agent.role || "Agent"} \u00b7 ${agent.model}`;
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
  setText("#agentCount", String(config.agents.length));
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
  if (el.title && state.task) el.title.textContent = state.task;
  el.events.replaceChildren();
  state.events.forEach(appendEvent);
  setPhase(state.phase || 1);
  applySpend(state.spend);
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
}

async function begin(task) {
  emptyRoom("Opening the session…");
  await api.post("/api/run", { task });
  setRunning(true, "Running");
  if (el.title) el.title.textContent = task;
  listen();
}

function listen() {
  const source = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
  source.addEventListener("state", (message) => takeOver(JSON.parse(message.data)));
  source.addEventListener("room", (message) => {
    appendEvent(JSON.parse(message.data));
    api.get("/api/state").then((state) => applySpend(state.spend)).catch(() => {});
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
    applySpend({ model_calls: payload.model_calls, max_model_calls: payload.model_calls,
                 revision: 0, max_revisions: 0, usage: payload.usage_totals });
    toast(`Session complete — ${payload.model_calls} model calls.`);
    source.close();
  });
  source.addEventListener("failed", (message) => {
    toast(`Session failed: ${JSON.parse(message.data).error}`);
    setRunning(false, "Failed");
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
    /* Served without a token in the address: the page stays a prototype. */
    return;
  }
  const config = await api.get("/api/config");
  if (!config.live) return;
  renderRoster(config);
  setText("#spendQuorum", `${config.agents.length} agents`);
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

start().catch((error) => {
  /* A refused token or a dead server is worth saying out loud: silence here
     looks exactly like the prototype, which is how a real fault hides. */
  console.error("live layer did not start:", error);
  toast(`Live view unavailable: ${error.message}`);
});
