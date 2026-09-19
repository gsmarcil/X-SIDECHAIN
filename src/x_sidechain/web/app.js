"use strict";

const translations = {
  en: {
    "nav.room": "Room", "nav.sessions": "Sessions", "nav.agents": "Agents", "nav.workspaces": "Workspaces", "nav.settings": "Settings",
    "action.newSession": "New session", "action.hold": "Hold input", "action.finish": "Finish now", "action.agent": "Agent", "action.files": "Files", "action.sendRoom": "Send to room",
    "room.running": "Running", "room.title": "Chair room", "room.workflow": "Live deliberation", "room.session": "Session",
    "composer.label": "Send one correction to the whole room",
    "sessions.title": "Sessions", "agents.title": "Agents", "workspaces.title": "Workspaces", "settings.title": "Settings",
    promptPlaceholder: "Add a fact, correction, or decisive artifact…", openSidebar: "Expand sidebar", closeSidebar: "Collapse sidebar",
    sent: "Correction sent. A new revision has started.", held: "Room input is held.", resumed: "Room input resumed.", finished: "Demo session finished.", saved: "Settings saved on this device."
  },
  es: {
    "nav.room": "Sala", "nav.sessions": "Sesiones", "nav.agents": "Agentes", "nav.workspaces": "Espacios", "nav.settings": "Ajustes",
    "action.newSession": "Nueva sesión", "action.hold": "Pausar entrada", "action.finish": "Finalizar", "action.agent": "Agente", "action.files": "Archivos", "action.sendRoom": "Enviar a la sala",
    "room.running": "En curso", "room.title": "Sala del coordinador", "room.workflow": "Deliberación activa", "room.session": "Sesión",
    "composer.label": "Enviar una corrección a toda la sala",
    "sessions.title": "Sesiones", "agents.title": "Agentes", "workspaces.title": "Espacios de trabajo", "settings.title": "Ajustes",
    promptPlaceholder: "Añade un hecho, corrección o prueba decisiva…", openSidebar: "Expandir barra lateral", closeSidebar: "Contraer barra lateral",
    sent: "Corrección enviada. Ha comenzado una nueva revisión.", held: "La entrada de la sala está pausada.", resumed: "La entrada de la sala se ha reanudado.", finished: "La sesión de demostración ha finalizado.", saved: "Ajustes guardados en este dispositivo."
  },
  fr: {
    "nav.room": "Salle", "nav.sessions": "Sessions", "nav.agents": "Agents", "nav.workspaces": "Espaces", "nav.settings": "Paramètres",
    "action.newSession": "Nouvelle session", "action.hold": "Suspendre", "action.finish": "Terminer", "action.agent": "Agent", "action.files": "Fichiers", "action.sendRoom": "Envoyer à la salle",
    "room.running": "En cours", "room.title": "Salle du président", "room.workflow": "Délibération en direct", "room.session": "Session",
    "composer.label": "Envoyer une correction à toute la salle",
    "sessions.title": "Sessions", "agents.title": "Agents", "workspaces.title": "Espaces de travail", "settings.title": "Paramètres",
    promptPlaceholder: "Ajoutez un fait, une correction ou une preuve décisive…", openSidebar: "Développer la barre latérale", closeSidebar: "Réduire la barre latérale",
    sent: "Correction envoyée. Une nouvelle révision a commencé.", held: "La saisie de la salle est suspendue.", resumed: "La saisie de la salle a repris.", finished: "La session de démonstration est terminée.", saved: "Paramètres enregistrés sur cet appareil."
  },
  ar: {
    "nav.room": "الغرفة", "nav.sessions": "الجلسات", "nav.agents": "الوكلاء", "nav.workspaces": "مساحات العمل", "nav.settings": "الإعدادات",
    "action.newSession": "جلسة جديدة", "action.hold": "إيقاف الإدخال", "action.finish": "إنهاء الآن", "action.agent": "وكيل", "action.files": "الملفات", "action.sendRoom": "إرسال إلى الغرفة",
    "room.running": "قيد التشغيل", "room.title": "غرفة الرئيس", "room.workflow": "النقاش المباشر", "room.session": "الجلسة",
    "composer.label": "إرسال تصحيح واحد إلى جميع الوكلاء",
    "sessions.title": "الجلسات", "agents.title": "الوكلاء", "workspaces.title": "مساحات العمل", "settings.title": "الإعدادات",
    promptPlaceholder: "أضف حقيقة أو تصحيحاً أو دليلاً حاسماً…", openSidebar: "توسيع الشريط الجانبي", closeSidebar: "تقليص الشريط الجانبي",
    sent: "أُرسل التصحيح وبدأت مراجعة جديدة.", held: "تم إيقاف الإدخال إلى الغرفة.", resumed: "تم استئناف الإدخال إلى الغرفة.", finished: "انتهت الجلسة التجريبية.", saved: "حُفظت الإعدادات على هذا الجهاز."
  }
};

const storage = {
  get(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* Device storage may be disabled. */ }
  }
};

const shell = document.querySelector("#appShell");
const sidebarToggle = document.querySelector("#sidebarToggle");
const languageSelect = document.querySelector("#languageSelect");
const promptInput = document.querySelector("#promptInput");
const eventList = document.querySelector("#eventList");
const phaseItems = [...document.querySelectorAll("#phaseTrack li")];
const drawer = document.querySelector("#agentDrawer");
const scrim = document.querySelector("#scrim");
const toast = document.querySelector("#toast");
let currentLanguage = storage.get("xsc-language", "en");
let currentPhase = 4;
let held = false;
let toastTimer;

function t(key) {
  return translations[currentLanguage]?.[key] ?? translations.en[key] ?? key;
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function setSidebar(collapsed) {
  shell.classList.toggle("sidebar-collapsed", collapsed);
  sidebarToggle.setAttribute("aria-label", collapsed ? t("openSidebar") : t("closeSidebar"));
  sidebarToggle.title = `${collapsed ? t("openSidebar") : t("closeSidebar")} (Ctrl+\\)`;
  storage.set("xsc-sidebar-collapsed", String(collapsed));
}

function setLanguage(language) {
  if (!translations[language]) return;
  currentLanguage = language;
  document.documentElement.lang = language;
  document.documentElement.dir = language === "ar" ? "rtl" : "ltr";
  languageSelect.value = language;
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  promptInput.placeholder = t("promptPlaceholder");
  const tooltipKeys = ["nav.room", "nav.sessions", "nav.agents", "nav.workspaces", "nav.settings"];
  document.querySelectorAll(".nav-item").forEach((node, index) => {
    node.dataset.tooltip = t(tooltipKeys[index]);
  });
  document.querySelectorAll("[data-language]").forEach((node) => node.classList.toggle("selected", node.dataset.language === language));
  storage.set("xsc-language", language);
  setSidebar(shell.classList.contains("sidebar-collapsed"));
}

function selectView(name) {
  document.querySelectorAll("[data-view]").forEach((node) => node.classList.toggle("active", node.dataset.view === name));
  document.querySelectorAll("[data-view-target]").forEach((node) => node.classList.toggle("active", node.dataset.viewTarget === name));
  const activeHeading = document.querySelector(`[data-view="${name}"] h1`);
  if (activeHeading) document.title = `${activeHeading.textContent} · X-SIDECHAIN`;
  storage.set("xsc-active-view", name);
}

function selectSettings(name) {
  document.querySelectorAll("[data-settings]").forEach((node) => node.classList.toggle("active", node.dataset.settings === name));
  document.querySelectorAll("[data-settings-target]").forEach((node) => node.classList.toggle("active", node.dataset.settingsTarget === name));
}

function openAgent(name) {
  document.querySelector("#drawerAgent").textContent = name;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  scrim.hidden = false;
  document.querySelector("#closeDrawer").focus();
}

function closeAgent() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  scrim.hidden = true;
}

function setPhase(nextPhase) {
  currentPhase = Math.max(1, Math.min(7, nextPhase));
  phaseItems.forEach((item, index) => {
    item.classList.toggle("done", index + 1 < currentPhase);
    item.classList.toggle("current", index + 1 === currentPhase);
  });
  document.querySelector("#phaseCounter").textContent = `phase ${currentPhase} of 7`;
}

function createCorrectionEvent(message) {
  const card = document.createElement("article");
  card.className = "event-card chair-event";
  const meta = document.createElement("div");
  meta.className = "event-meta";
  const avatar = document.createElement("span");
  avatar.className = "avatar small";
  avatar.textContent = "YO";
  const author = document.createElement("strong");
  author.textContent = currentLanguage === "ar" ? "أنت" : "You";
  const tag = document.createElement("span");
  tag.className = "tag blue";
  tag.textContent = "CORRECTION";
  const time = document.createElement("time");
  time.textContent = new Intl.DateTimeFormat(currentLanguage, {hour: "2-digit", minute: "2-digit"}).format(new Date());
  meta.append(avatar, author, tag, time);
  const content = document.createElement("p");
  content.textContent = message;
  const foot = document.createElement("div");
  foot.className = "event-foot";
  const revision = document.createElement("span");
  revision.className = "tag warning";
  const revisionNumber = Number(document.querySelector("#revisionNumber").textContent) + 1;
  document.querySelector("#revisionNumber").textContent = String(revisionNumber);
  revision.textContent = `Revision ${revisionNumber}`;
  const note = document.createElement("span");
  note.textContent = "Cycle restarted from private work";
  foot.append(revision, note);
  card.append(meta, content, foot);
  return card;
}

sidebarToggle.addEventListener("click", () => setSidebar(!shell.classList.contains("sidebar-collapsed")));
document.querySelectorAll("[data-view-target]").forEach((button) => button.addEventListener("click", () => selectView(button.dataset.viewTarget)));
document.querySelectorAll("[data-settings-target]").forEach((button) => button.addEventListener("click", () => selectSettings(button.dataset.settingsTarget)));
document.querySelectorAll("[data-open-agent], .agent-chip[data-agent]").forEach((button) => button.addEventListener("click", () => openAgent(button.dataset.openAgent || button.dataset.agent)));
document.querySelector("#closeDrawer").addEventListener("click", closeAgent);
scrim.addEventListener("click", closeAgent);

languageSelect.addEventListener("change", (event) => setLanguage(event.target.value));
document.querySelectorAll("[data-language]").forEach((button) => button.addEventListener("click", () => setLanguage(button.dataset.language)));

document.querySelector("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const message = promptInput.value.trim();
  if (!message || held) {
    if (held) showToast(t("held"));
    return;
  }
  eventList.append(createCorrectionEvent(message));
  promptInput.value = "";
  document.querySelector("#fileChips").replaceChildren();
  setPhase(1);
  eventList.lastElementChild.scrollIntoView({behavior: "smooth", block: "center"});
  showToast(t("sent"));
  window.setTimeout(() => setPhase(2), 900);
});

document.querySelector("#holdButton").addEventListener("click", (event) => {
  held = !held;
  event.currentTarget.textContent = held ? (currentLanguage === "ar" ? "استئناف الإدخال" : "Resume input") : t("action.hold");
  promptInput.disabled = held;
  showToast(held ? t("held") : t("resumed"));
});

document.querySelector("#finishButton").addEventListener("click", () => {
  setPhase(7);
  document.querySelector("#runState").textContent = currentLanguage === "ar" ? "اكتملت" : "Complete";
  document.querySelector("#runState").previousElementSibling?.classList.remove("live-dot");
  promptInput.disabled = true;
  showToast(t("finished"));
});

const fileInput = document.querySelector("#fileInput");
document.querySelector("#attachButton").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  const chips = document.querySelector("#fileChips");
  chips.replaceChildren();
  [...fileInput.files].slice(0, 6).forEach((file) => {
    const chip = document.createElement("span");
    chip.className = "file-chip";
    chip.textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
    chips.append(chip);
  });
});

document.querySelector("#privateComposer").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = event.currentTarget.querySelector("textarea");
  const message = input.value.trim();
  if (!message) return;
  const article = document.createElement("article");
  article.className = "user-private";
  const bubble = document.createElement("div");
  const author = document.createElement("strong");
  author.textContent = currentLanguage === "ar" ? "أنت" : "You";
  const content = document.createElement("p");
  content.textContent = message;
  bubble.append(author, content);
  article.append(bubble);
  document.querySelector("#privateThread").append(article);
  input.value = "";
  article.scrollIntoView({behavior: "smooth"});
});

const dialog = document.querySelector("#agentDialog");
document.querySelectorAll("#addAgentButton, #addAgentInline").forEach((button) => button.addEventListener("click", () => dialog.showModal()));
dialog.addEventListener("close", () => {
  if (dialog.returnValue === "default") showToast("Agent draft added to the local team.");
});

document.querySelector("#saveSettings").addEventListener("click", () => showToast(t("saved")));
document.querySelector("#viewAudit").addEventListener("click", () => showToast("Audit chain verified: 18 records."));
document.querySelector("#newSession").addEventListener("click", () => {
  selectView("room");
  promptInput.disabled = false;
  held = false;
  setPhase(1);
  document.querySelector("#runState").textContent = t("room.running");
  promptInput.focus();
  showToast("New local draft ready.");
});

const moreButton = document.querySelector("#moreActions");
const actionMenu = document.querySelector("#actionMenu");
moreButton.addEventListener("click", () => { actionMenu.hidden = !actionMenu.hidden; });
document.addEventListener("click", (event) => {
  if (!actionMenu.contains(event.target) && !moreButton.contains(event.target)) actionMenu.hidden = true;
});

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "\\") {
    event.preventDefault();
    setSidebar(!shell.classList.contains("sidebar-collapsed"));
  }
  if (event.key === "Escape" && drawer.classList.contains("open")) closeAgent();
});

setSidebar(storage.get("xsc-sidebar-collapsed", "false") === "true");
setLanguage(currentLanguage);
selectView(storage.get("xsc-active-view", "room"));
