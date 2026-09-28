"use strict";

/* ================================================================
 * TeleMac — logica dell'app web (telecomando ad aria per Mac).
 * Script classico (niente moduli ES): gira su Safari iOS 17+.
 * ================================================================ */

/* ---------- Blocco zoom / scorrimento / rimbalzo ---------- */

document.addEventListener("gesturestart", (e) => e.preventDefault());
document.addEventListener("gesturechange", (e) => e.preventDefault());
document.addEventListener("dblclick", (e) => e.preventDefault(), { passive: false });
document.addEventListener(
  "touchmove",
  (e) => {
    if (e.touches.length > 1) e.preventDefault(); // niente pinch-zoom
  },
  { passive: false }
);

/* ---------- Cassetta degli attrezzi ---------- */

function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

/* ---------- Impostazioni (localStorage, con try/catch) ---------- */

const SETTINGS_KEY = "telemac-settings";
const TOKEN_KEY = "telemac-token";
const DEFAULT_SETTINGS = { sensitivity: 20, invertX: false, invertY: false, playMode: "media", vibration: true };

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return Object.assign({}, DEFAULT_SETTINGS, raw ? JSON.parse(raw) : {});
  } catch (e) {
    return Object.assign({}, DEFAULT_SETTINGS);
  }
}
function saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* niente */ }
}
function getToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}
function setToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* niente */ }
}

const settings = loadSettings();

/* ---------- Vibrazione (haptics) ----------
 * navigator.vibrate quando c'è; altrimenti, su iOS 18+, il trucco dello
 * switch nascosto (attivarlo/disattivarlo produce un piccolo "tick" aptico).
 */
function haptic() {
  if (!settings.vibration) return;
  try {
    if (navigator.vibrate) {
      navigator.vibrate(8);
      return;
    }
  } catch (e) { /* niente */ }
  try {
    const label = document.createElement("label");
    label.setAttribute("aria-hidden", "true");
    label.style.display = "none";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.setAttribute("switch", "");
    label.appendChild(input);
    document.head.appendChild(label);
    label.click();
    label.remove();
  } catch (e) { /* niente: nessuna vibrazione disponibile */ }
}

/* ---------- Modalità demo (GitHub Pages) ---------- */

const demoMode = location.hostname.endsWith("github.io") || /(?:^|[?&])demo(?:=|&|$)/.test(location.search);

/* ---------- Isola di stato (finta Dynamic Island) ---------- */

const island = (function () {
  const wrap = $("#island-wrap");
  const el = $("#island");
  let hideTimer = null;

  function measureSafeTop() {
    const probe = document.createElement("div");
    probe.style.position = "fixed";
    probe.style.top = "0";
    probe.style.left = "0";
    probe.style.height = "0";
    probe.style.visibility = "hidden";
    probe.style.paddingTop = "env(safe-area-inset-top, 0px)";
    document.body.appendChild(probe);
    const value = parseFloat(getComputedStyle(probe).paddingTop) || 0;
    probe.remove();
    return value;
  }

  function refreshMode() {
    let standalone = false;
    try {
      standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    } catch (e) { /* niente */ }
    const fixed = standalone && measureSafeTop() >= 55;
    document.body.classList.toggle("island-fixed", fixed);
  }
  refreshMode();
  window.addEventListener("resize", refreshMode);
  window.addEventListener("orientationchange", refreshMode);

  function show(opts) {
    opts = opts || {};
    const text = opts.text || "";
    const tone = opts.tone || "info";
    const ms = opts.ms || 0;

    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }

    el.dataset.tone = tone;
    el.innerHTML = "";
    const dot = document.createElement("span");
    dot.className = "island-dot";
    el.appendChild(dot);
    if (opts.icon) {
      const icon = document.createElement("span");
      icon.className = "island-icon";
      icon.innerHTML = opts.icon;
      el.appendChild(icon);
    }
    const txt = document.createElement("span");
    txt.className = "island-text";
    txt.textContent = text;
    el.appendChild(txt);

    el.classList.add("show");
    el.classList.toggle("tall", text.length > 28);

    if (ms > 0) {
      hideTimer = setTimeout(() => {
        el.classList.remove("show", "tall");
      }, ms);
    }
  }

  return { show: show };
})();

/* ---------- Motore del puntatore (pointer.js) ---------- */

// Convenzione degli assi del giroscopio: si parte da quella più diffusa nei
// browser e pointer.js la verifica da sola al primo movimento su/giù.
const AXES_KEY = "telemac-axes";
function loadAxes() {
  try {
    const raw = localStorage.getItem(AXES_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}
const savedAxes = loadAxes();

const pointerEngine = window.TeleMacPointer.createPointer({
  getSettings: () => ({ sensitivity: settings.sensitivity, invertX: settings.invertX, invertY: settings.invertY }),
  now: () => performance.now(),
  axes: savedAxes || { map: "xyz", sign: 1 },
  autoDetect: !savedAxes,
  onAxes: (axes) => {
    try { localStorage.setItem(AXES_KEY, JSON.stringify(axes)); } catch (e) { /* niente */ }
    island.show({ text: "Giroscopio calibrato", tone: "ok", ms: 1500 });
  },
});
window.addEventListener("devicemotion", (e) => pointerEngine.handleMotion(e));

let pointerOn = false;

function setPointerOn(on) {
  pointerOn = on;
  pointerEngine.setEnabled(on);
  const sw = $("#pointer-toggle");
  sw.setAttribute("aria-checked", on ? "true" : "false");
  $("#pad-center-label").textContent = on ? "" : "OK";
}

async function togglePointer() {
  if (pointerOn) {
    setPointerOn(false);
    island.show({ text: "Puntatore disattivato", tone: "info", ms: 900 });
    return;
  }
  try {
    if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
      const result = await DeviceMotionEvent.requestPermission();
      if (result !== "granted") {
        setPointerOn(false);
        island.show({ text: "Movimento non consentito: chiudi e riapri l'app e tocca Consenti", tone: "err", ms: 0 });
        return;
      }
    }
    setPointerOn(true);
    if (pointerEngine.getAxes().detecting) {
      island.show({ text: "Muovi il telefono su e giù per calibrarlo", tone: "info", ms: 3000 });
    } else {
      island.show({ text: "Puntatore attivo", tone: "ok", ms: 900 });
    }
  } catch (e) {
    setPointerOn(false);
    island.show({ text: "Movimento non consentito: chiudi e riapri l'app e tocca Consenti", tone: "err", ms: 0 });
  }
}

$("#pointer-toggle").addEventListener("click", () => {
  haptic();
  togglePointer();
});

// Invia i movimenti accumulati una volta per fotogramma.
(function pointerLoop() {
  const d = pointerEngine.takeDelta();
  const dx = Math.round(d.dx * 100) / 100;
  const dy = Math.round(d.dy * 100) / 100;
  if (dx !== 0 || dy !== 0) send({ type: "move", dx: dx, dy: dy });
  requestAnimationFrame(pointerLoop);
})();

/* ---------- Schermo sempre acceso ---------- */

let wakeLock = null;
async function requestWakeLock() {
  try {
    if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
  } catch (e) { /* niente: va bene anche senza */ }
}
document.addEventListener("pointerdown", function firstGesture() {
  requestWakeLock();
  document.removeEventListener("pointerdown", firstGesture);
}, { once: true });
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") requestWakeLock();
});

/* ---------- Connessione al Mac (WebSocket) ---------- */

let socket = null;
let reconnectDelay = 500;
let pingTimer = null;
let watchdogTimer = null;
let lastMessageAt = 0;

function wsUrl() {
  const u = new URL("./ws", location.href);
  u.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  u.searchParams.set("token", getToken() || "");
  return u.toString();
}

let debugSendCount = 0;
function send(obj) {
  debugSendCount++;
  if (window.TeleMacDebug) window.TeleMacDebug.lastSend = obj;
  if (demoMode) {
    demoHandle(obj);
    return;
  }
  if (socket && socket.readyState === WebSocket.OPEN) {
    try { socket.send(JSON.stringify(obj)); } catch (e) { /* niente */ }
  }
}

function stopTimers() {
  clearInterval(pingTimer);
  clearInterval(watchdogTimer);
  pingTimer = null;
  watchdogTimer = null;
}

function connectWS() {
  if (demoMode) return;
  const token = getToken();
  if (!token) { showPairing(); return; }

  let ws;
  try {
    ws = new WebSocket(wsUrl());
  } catch (e) {
    scheduleReconnect();
    return;
  }
  socket = ws;

  ws.onopen = () => {
    reconnectDelay = 500;
    lastMessageAt = Date.now();
    stopTimers();
    pingTimer = setInterval(() => send({ type: "ping" }), 10000);
    watchdogTimer = setInterval(() => {
      if (Date.now() - lastMessageAt > 25000) {
        try { ws.close(); } catch (e) { /* niente */ }
      }
    }, 2000);
  };

  ws.onmessage = (ev) => {
    lastMessageAt = Date.now();
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleServerMessage(msg);
  };

  ws.onclose = () => {
    stopTimers();
    if (socket === ws) socket = null;
    if (getToken()) island.show({ text: "Mac non raggiungibile…", tone: "err", ms: 0 });
    scheduleReconnect();
  };

  ws.onerror = () => {
    try { ws.close(); } catch (e) { /* niente */ }
  };
}

function scheduleReconnect() {
  const delay = reconnectDelay;
  reconnectDelay = Math.min(reconnectDelay * 2, 5000);
  setTimeout(() => {
    if (!demoMode && getToken() && (!socket || socket.readyState === WebSocket.CLOSED)) connectWS();
  }, delay);
}

function handleServerMessage(msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "hello") {
    hidePairing();
    island.show({ text: "Collegato a " + (msg.name || "Mac"), tone: "ok", ms: 1800 });
    if (msg.accessibility === false) {
      setTimeout(() => {
        island.show({ text: "Sul Mac manca il permesso Accessibilità", tone: "warn", ms: 6000 });
      }, 1900);
    }
  } else if (msg.type === "auth" && msg.ok === false) {
    setToken(null);
    showPairing();
  }
  // "pong": non serve fare nulla, è solo la conferma che la linea è viva.
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !demoMode) {
    if (!socket || socket.readyState === WebSocket.CLOSED) {
      reconnectDelay = 500;
      connectWS();
    }
  }
});

/* ---------- Abbinamento ---------- */

const pairingEl = $("#pairing");
function showPairing() { if (!demoMode) pairingEl.hidden = false; }
function hidePairing() { pairingEl.hidden = true; }

function setPairStatus(text) { $("#pair-status").textContent = text || ""; }
function setPairError(text) { $("#pair-error").textContent = text || ""; }

$("#pair-start-btn").addEventListener("click", async () => {
  setPairError("");
  setPairStatus("Richiesta in corso…");
  try {
    const res = await fetch("./api/pair/start", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      setPairStatus("Codice mostrato sul Mac. Hai " + data.expires_in + " secondi per digitarlo.");
      $("#pair-code").focus();
    } else if (data.error === "too_fast") {
      setPairStatus("Aspetta qualche secondo e riprova.");
    } else {
      setPairStatus("");
      setPairError("Errore imprevisto: riprova.");
    }
  } catch (e) {
    setPairStatus("");
    setPairError("Impossibile contattare il Mac: controlla la rete.");
  }
});

const pairCodeInput = $("#pair-code");
pairCodeInput.addEventListener("input", () => {
  const digits = pairCodeInput.value.replace(/\D/g, "").slice(0, 6);
  pairCodeInput.value = digits;
  if (digits.length === 6) finishPairing(digits);
});

async function finishPairing(code) {
  setPairError("");
  setPairStatus("Verifica in corso…");
  try {
    const res = await fetch("./api/pair/finish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code, device: "iPhone" }),
    });
    const data = await res.json();
    pairCodeInput.value = "";
    setPairStatus("");
    if (data.ok) {
      setToken(data.token);
      hidePairing();
      island.show({ text: "Collegato a " + (data.name || "Mac"), tone: "ok", ms: 1800 });
      reconnectDelay = 500;
      connectWS();
      return;
    }
    if (data.error === "wrong_code") {
      const left = data.attempts_left != null ? data.attempts_left : "?";
      setPairError("Codice sbagliato, restano " + left + " tentativi");
    } else if (data.error === "expired") {
      setPairError("Codice scaduto: richiedine uno nuovo");
    } else if (data.error === "locked") {
      setPairError("Troppi tentativi: richiedi un nuovo codice");
    } else if (data.error === "no_code") {
      setPairError('Nessun codice attivo: tocca "Mostra il codice sul Mac"');
    } else {
      setPairError("Errore: riprova");
    }
  } catch (e) {
    setPairStatus("");
    setPairError("Impossibile contattare il Mac.");
  }
}

/* ---------- Schede in basso ---------- */

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("#panel-" + tab.dataset.panel).classList.add("active");
  });
});

/* ---------- Pulsanti "reattivi": azione su pointerdown, vibrazione ad ogni pressione ---------- */

function bindReactive(el, action, opts) {
  opts = opts || {};
  let holdTimer = null;
  let repeatTimer = null;

  function stop() {
    clearTimeout(holdTimer);
    clearInterval(repeatTimer);
    holdTimer = null;
    repeatTimer = null;
    el.classList.remove("accent-active");
  }

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* niente */ }
    haptic();
    el.classList.add("accent-active");
    action();
    if (opts.repeat) {
      holdTimer = setTimeout(() => {
        repeatTimer = setInterval(action, 90);
      }, 380);
    }
  });
  el.addEventListener("pointerup", stop);
  el.addEventListener("pointercancel", stop);
}

function bindClick(el, action) {
  el.addEventListener("click", () => {
    haptic();
    action();
  });
}

/* ---------- Clickpad ---------- */

const clickpad = $("#clickpad");
const padCenterLabel = $("#pad-center-label");
let padActiveZone = null;
let padHoldTimer = null;
let padRepeatTimer = null;
let centerHoldTimer = null;
let centerFreezeRenew = null;
let centerDragging = false;

function zoneFromPoint(clientX, clientY) {
  const rect = clickpad.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const dx = clientX - cx;
  const dy = clientY - cy;
  const dist = Math.hypot(dx, dy);
  if (dist < (rect.width / 2) * 0.45) return "center";
  if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? "right" : "left";
  return dy > 0 ? "down" : "up";
}

function setGlow(dir, on) {
  const g = clickpad.querySelector('.pad-glow[data-dir="' + dir + '"]');
  if (g) g.classList.toggle("active", on);
}

function sendArrow(dir) { send({ type: "key", name: dir, mods: [] }); }

function startArrowRepeat(dir) {
  sendArrow(dir);
  clearTimeout(padHoldTimer);
  clearInterval(padRepeatTimer);
  padHoldTimer = setTimeout(() => {
    padRepeatTimer = setInterval(() => sendArrow(dir), 90);
  }, 380);
}
function stopArrowRepeat() {
  clearTimeout(padHoldTimer);
  clearInterval(padRepeatTimer);
  padHoldTimer = null;
  padRepeatTimer = null;
}

function centerDown() {
  if (pointerOn) {
    pointerEngine.freeze(250);
    clearInterval(centerFreezeRenew);
    centerFreezeRenew = setInterval(() => pointerEngine.freeze(250), 100);
    centerDragging = false;
    centerHoldTimer = setTimeout(() => {
      centerDragging = true;
      clickpad.classList.add("dragging");
      send({ type: "button", button: "left", down: true });
    }, 350);
  } else {
    send({ type: "key", name: "return", mods: [] });
  }
}
function centerUp() {
  clearTimeout(centerHoldTimer);
  clearInterval(centerFreezeRenew);
  centerHoldTimer = null;
  centerFreezeRenew = null;
  if (pointerOn) {
    if (centerDragging) {
      send({ type: "button", button: "left", down: false });
    } else {
      send({ type: "click", button: "left" });
    }
    centerDragging = false;
    clickpad.classList.remove("dragging");
    pointerEngine.freeze(120);
  }
}

clickpad.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  try { clickpad.setPointerCapture(e.pointerId); } catch (err) { /* niente */ }
  const zone = zoneFromPoint(e.clientX, e.clientY);
  padActiveZone = zone;
  haptic();
  if (zone === "center") {
    clickpad.classList.add("pressed-center");
    centerDown();
  } else {
    setGlow(zone, true);
    startArrowRepeat(zone);
  }
});

function releasePad() {
  if (!padActiveZone) return;
  if (padActiveZone === "center") {
    clickpad.classList.remove("pressed-center");
    centerUp();
  } else {
    setGlow(padActiveZone, false);
    stopArrowRepeat();
  }
  padActiveZone = null;
}
clickpad.addEventListener("pointerup", releasePad);
clickpad.addEventListener("pointercancel", releasePad);

/* ---------- Striscia "Scorri" ---------- */

const scrollbarEl = $("#scrollbar");
let scrollLastY = null;

scrollbarEl.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  try { scrollbarEl.setPointerCapture(e.pointerId); } catch (err) { /* niente */ }
  scrollLastY = e.clientY;
  scrollbarEl.classList.add("active");
  haptic();
});
scrollbarEl.addEventListener("pointermove", (e) => {
  if (scrollLastY === null) return;
  // deltaFingerY nel senso "normale" dello schermo: positivo quando il dito scende,
  // negativo quando il dito sale. Verso naturale (come iOS): il dito sale -> il
  // contenuto scorre su -> dy negativo. Con questa convenzione basta non invertire
  // il segno: dy = deltaFingerY * 3.
  const deltaFingerY = e.clientY - scrollLastY;
  scrollLastY = e.clientY;
  if (!deltaFingerY) return;
  const dy = clamp(deltaFingerY * 3, -2000, 2000);
  send({ type: "scroll", dx: 0, dy: dy });
});
function endScroll() {
  scrollLastY = null;
  scrollbarEl.classList.remove("active");
}
scrollbarEl.addEventListener("pointerup", endScroll);
scrollbarEl.addEventListener("pointercancel", endScroll);

/* ---------- Riga pulsanti del Telecomando ---------- */

bindReactive($("#btn-back"), () => send({ type: "key", name: "escape", mods: [] }));
bindReactive($("#btn-play"), () => {
  if (settings.playMode === "space") send({ type: "key", name: "space", mods: [] });
  else send({ type: "media", name: "play" });
});
bindReactive($("#btn-fullscreen"), () => send({ type: "key", name: "f", mods: [] }));

bindReactive($("#btn-voldown"), () => {
  send({ type: "media", name: "voldown" });
  island.show({ text: "Volume −", tone: "info", ms: 900 });
}, { repeat: true });
bindReactive($("#btn-mute"), () => {
  send({ type: "media", name: "mute" });
  island.show({ text: "Muto", tone: "info", ms: 900 });
});
bindReactive($("#btn-volup"), () => {
  send({ type: "media", name: "volup" });
  island.show({ text: "Volume +", tone: "info", ms: 900 });
}, { repeat: true });

bindReactive($("#btn-rightclick"), () => send({ type: "click", button: "right" }));
bindReactive($("#btn-prev"), () => send({ type: "media", name: "prev" }));
bindReactive($("#btn-next"), () => send({ type: "media", name: "next" }));

/* ---------- Tastiera ---------- */

const textInput = $("#text-input");
let lastChars = [];

textInput.addEventListener("input", () => {
  const chars = Array.from(textInput.value); // per codepoint: corretto per le coppie surrogate
  let prefix = 0;
  while (prefix < chars.length && prefix < lastChars.length && chars[prefix] === lastChars[prefix]) prefix++;
  const removed = lastChars.length - prefix;
  const added = chars.slice(prefix).join("");
  for (let i = 0; i < removed; i++) send({ type: "key", name: "backspace", mods: [] });
  if (added) send({ type: "text", text: added });
  lastChars = chars;
  if (chars.length > 300) { // non lasciamo crescere il campo all'infinito
    textInput.value = "";
    lastChars = [];
  }
});

textInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    send({ type: "key", name: "return", mods: [] });
    textInput.value = "";
    lastChars = [];
  } else if (e.key === "Backspace" && textInput.value.length === 0) {
    send({ type: "key", name: "backspace", mods: [] });
  }
});

bindReactive($("#btn-kb-return"), () => send({ type: "key", name: "return", mods: [] }));
bindReactive($("#btn-kb-backspace"), () => send({ type: "key", name: "backspace", mods: [] }));
bindReactive($("#btn-kb-tab"), () => send({ type: "key", name: "tab", mods: [] }));
bindReactive($("#btn-kb-escape"), () => send({ type: "key", name: "escape", mods: [] }));
bindReactive($("#btn-kb-spotlight"), () => send({ type: "key", name: "space", mods: ["cmd"] }));
bindReactive($("#btn-kb-urlbar"), () => send({ type: "key", name: "l", mods: ["cmd"] }));
bindReactive($("#btn-kb-search"), () => send({ type: "key", name: "f", mods: ["cmd"] }));

/* ---------- Altro (scorciatoie: azione su click) ---------- */

bindClick($("#btn-switchapp"), () => send({ type: "key", name: "tab", mods: ["cmd"] }));
bindClick($("#btn-mission"), () => send({ type: "key", name: "up", mods: ["ctrl"] }));
bindClick($("#btn-pageback"), () => send({ type: "key", name: "left", mods: ["cmd"] }));
bindClick($("#btn-pageforward"), () => send({ type: "key", name: "right", mods: ["cmd"] }));
bindClick($("#btn-reload"), () => send({ type: "key", name: "r", mods: ["cmd"] }));
bindClick($("#btn-newtab"), () => send({ type: "key", name: "t", mods: ["cmd"] }));
bindClick($("#btn-closetab"), () => send({ type: "key", name: "w", mods: ["cmd"] }));
bindClick($("#btn-fullscreenwin"), () => send({ type: "key", name: "f", mods: ["cmd", "ctrl"] }));
bindClick($("#btn-sleep"), () => send({ type: "sleep" }));

/* ---------- Impostazioni ---------- */

function bindMiniSwitch(el, key) {
  el.setAttribute("aria-checked", settings[key] ? "true" : "false");
  el.addEventListener("click", () => {
    settings[key] = !settings[key];
    el.setAttribute("aria-checked", settings[key] ? "true" : "false");
    saveSettings();
    haptic();
  });
}

const sensitivityInput = $("#setting-sensitivity");
sensitivityInput.value = String(settings.sensitivity);
sensitivityInput.addEventListener("input", () => {
  settings.sensitivity = Number(sensitivityInput.value);
  saveSettings();
});

bindMiniSwitch($("#setting-invert-x"), "invertX");
bindMiniSwitch($("#setting-invert-y"), "invertY");
bindMiniSwitch($("#setting-vibration"), "vibration");

$$(".play-mode-btn").forEach((btn) => {
  btn.classList.toggle("active", btn.dataset.mode === settings.playMode);
  btn.addEventListener("click", () => {
    settings.playMode = btn.dataset.mode;
    $$(".play-mode-btn").forEach((b) => b.classList.toggle("active", b === btn));
    saveSettings();
  });
});

$("#btn-recalibrate").addEventListener("click", () => {
  haptic();
  try { localStorage.removeItem(AXES_KEY); } catch (e) { /* niente */ }
  pointerEngine.recalibrate();
  island.show({ text: "Muovi il telefono su e giù per calibrarlo", tone: "info", ms: 3000 });
});

$("#btn-forget").addEventListener("click", () => {
  haptic();
  setToken(null);
  try { if (socket) socket.close(); } catch (e) { /* niente */ }
  showPairing();
});

/* ---------- Modalità demo (per GitHub Pages) ---------- */

let demoCursor = { x: 960, y: 540 };
let demoTrail = [];
let demoClicks = [];
let demoDragging = false;
let demoDragTrail = [];
let demoText = "";
let demoCanvas, demoCtx;

const KEY_LABEL = {
  return: "Invio", escape: "Esc", tab: "Tab", backspace: "Cancella", space: "Spazio",
  f: "Schermo intero", up: "▲", down: "▼", left: "◀ Pagina", right: "▶ Pagina",
  l: "Indirizzo", r: "Ricarica", t: "Nuova scheda", w: "Chiudi scheda",
};
const MOD_SYMBOL = { cmd: "⌘", shift: "⇧", alt: "⌥", ctrl: "⌃" };
const MEDIA_LABEL = { volup: "Volume +", voldown: "Volume −", mute: "Muto", play: "Play/Pausa", next: "Successivo", prev: "Precedente" };

function describeKey(obj) {
  const mods = (obj.mods || []).map((m) => MOD_SYMBOL[m] || m).join("");
  const label = KEY_LABEL[obj.name] || obj.name;
  return (mods ? mods + " " : "") + label;
}

function demoSetAction(text) {
  $("#demo-caption").textContent = text;
}

function demoBackspaceChar() {
  const arr = Array.from(demoText);
  arr.pop();
  demoText = arr.join("");
}
function updateDemoKeyboardBox() {
  $("#demo-keyboard-box").textContent = demoText;
}

function demoHandle(obj) {
  if (!obj || typeof obj !== "object") return;
  if (obj.type === "move") {
    demoCursor.x = clamp(demoCursor.x + obj.dx, 0, 1920);
    demoCursor.y = clamp(demoCursor.y + obj.dy, 0, 1080);
    demoTrail.push({ x: demoCursor.x, y: demoCursor.y });
    if (demoTrail.length > 16) demoTrail.shift();
    if (demoDragging) {
      demoDragTrail.push({ x: demoCursor.x, y: demoCursor.y });
      if (demoDragTrail.length > 200) demoDragTrail.shift();
    }
  } else if (obj.type === "click") {
    const color = obj.button === "right" ? "#ff453a" : "#0a84ff";
    demoClicks.push({ x: demoCursor.x, y: demoCursor.y, color: color, start: performance.now() });
    demoSetAction(obj.button === "right" ? "Clic destro" : "Clic sinistro");
  } else if (obj.type === "button") {
    if (obj.button === "left" && obj.down) {
      demoDragging = true;
      demoDragTrail = [{ x: demoCursor.x, y: demoCursor.y }];
      demoSetAction("Trascinamento…");
    } else if (obj.button === "left" && !obj.down) {
      demoDragging = false;
      demoClicks.push({ x: demoCursor.x, y: demoCursor.y, color: "#0a84ff", start: performance.now() });
      demoSetAction("Fine trascinamento");
      demoDragTrail = [];
    }
  } else if (obj.type === "scroll") {
    demoSetAction("Scorri");
  } else if (obj.type === "key") {
    if (obj.name === "backspace" && (!obj.mods || obj.mods.length === 0)) demoBackspaceChar();
    updateDemoKeyboardBox();
    demoSetAction(describeKey(obj));
  } else if (obj.type === "media") {
    demoSetAction(MEDIA_LABEL[obj.name] || obj.name);
  } else if (obj.type === "text") {
    demoText += obj.text;
    updateDemoKeyboardBox();
    demoSetAction("Testo");
  } else if (obj.type === "sleep") {
    demoSetAction("Spegni schermo");
  }
}

function demoScale() {
  const rect = demoCanvas.getBoundingClientRect();
  return rect.width / 1920 || 1;
}

function resizeDemoCanvas() {
  if (!demoCanvas) return;
  const rect = demoCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  demoCanvas.width = Math.max(1, Math.round(rect.width * dpr));
  demoCanvas.height = Math.max(1, Math.round(rect.height * dpr));
  demoCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawDemo() {
  const rect = demoCanvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  const scale = demoScale();
  demoCtx.clearRect(0, 0, w, h);
  demoCtx.fillStyle = "#0b0b0c";
  demoCtx.fillRect(0, 0, w, h);

  // scia che sfuma
  demoTrail.forEach((p, i) => {
    const alpha = ((i + 1) / demoTrail.length) * 0.3;
    demoCtx.beginPath();
    demoCtx.fillStyle = "rgba(255,255,255," + alpha.toFixed(3) + ")";
    demoCtx.arc(p.x * scale, p.y * scale, 3, 0, Math.PI * 2);
    demoCtx.fill();
  });

  // scia gialla durante il trascinamento
  if (demoDragTrail.length > 1) {
    demoCtx.beginPath();
    demoCtx.strokeStyle = "rgba(255,214,10,.85)";
    demoCtx.lineWidth = 2;
    demoDragTrail.forEach((p, i) => {
      const x = p.x * scale, y = p.y * scale;
      if (i === 0) demoCtx.moveTo(x, y); else demoCtx.lineTo(x, y);
    });
    demoCtx.stroke();
  }

  // cerchi dei clic, che si allargano e svaniscono
  const now = performance.now();
  demoClicks = demoClicks.filter((c) => now - c.start < 500);
  demoClicks.forEach((c) => {
    const t = (now - c.start) / 500;
    demoCtx.beginPath();
    demoCtx.strokeStyle = c.color;
    demoCtx.globalAlpha = 1 - t;
    demoCtx.lineWidth = 2;
    demoCtx.arc(c.x * scale, c.y * scale, 6 + t * 18, 0, Math.PI * 2);
    demoCtx.stroke();
    demoCtx.globalAlpha = 1;
  });

  // cursore
  demoCtx.beginPath();
  demoCtx.fillStyle = "#fff";
  demoCtx.arc(demoCursor.x * scale, demoCursor.y * scale, 6, 0, Math.PI * 2);
  demoCtx.fill();
  demoCtx.lineWidth = 1.5;
  demoCtx.strokeStyle = "rgba(0,0,0,.5)";
  demoCtx.stroke();
}

function demoLoop() {
  drawDemo();
  requestAnimationFrame(demoLoop);
}

if (demoMode) {
  document.body.classList.add("demo-mode");
  demoCanvas = $("#demo-canvas");
  demoCtx = demoCanvas.getContext("2d");
  $("#demo-screen").hidden = false;
  $("#demo-keyboard-wrap").hidden = false;
  resizeDemoCanvas();
  window.addEventListener("resize", resizeDemoCanvas);
  requestAnimationFrame(demoLoop);
  island.show({ text: "Modalità demo", tone: "info", ms: 2500 });
} else if (!getToken()) {
  showPairing();
} else {
  connectWS();
}

// Esposto solo per verifiche manuali/di test (Playwright): non ha altro scopo.
window.TeleMacDebug = {
  isDemoMode: () => demoMode,
  getDemoCursor: () => ({ x: demoCursor.x, y: demoCursor.y }),
  getDemoText: () => demoText,
  pointerEngine: pointerEngine,
  isPointerOn: () => pointerOn,
  getSendCount: () => debugSendCount,
};
