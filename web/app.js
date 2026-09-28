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
const DEFAULT_SETTINGS = { pointerOn: true, sensitivity: 20, invertX: false, invertY: false, playMode: "media", vibration: true };

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

// Il pulsante d'accensione in alto accende/spegne il giroscopio (e se lo ricorda).
// Su iOS il permesso al movimento si può chiedere solo dentro un tocco: lo
// chiediamo quando si accende, oppure al primo tocco se era già acceso.
let pointerOn = settings.pointerOn !== false;
let motionSeen = false;
let motionGranted = false;
window.addEventListener("devicemotion", () => { motionSeen = true; }, { once: true });

async function askMotionPermission() {
  if (motionGranted) return true;
  if (typeof DeviceMotionEvent === "undefined" || typeof DeviceMotionEvent.requestPermission !== "function") {
    motionGranted = true;
    return true;
  }
  try {
    motionGranted = (await DeviceMotionEvent.requestPermission()) === "granted";
  } catch (e) {
    motionGranted = false;
  }
  if (!motionGranted) {
    island.show({ text: "Movimento non consentito: chiudi e riapri l'app e tocca Consenti", tone: "err", ms: 0 });
  } else if (pointerEngine.getAxes().detecting) {
    island.show({ text: "Muovi il telefono su e giù per calibrarlo", tone: "info", ms: 3000 });
  }
  return motionGranted;
}

function setPointerOn(on) {
  pointerOn = on;
  settings.pointerOn = on;
  saveSettings();
  pointerEngine.setEnabled(on);
  $("#btn-power").setAttribute("aria-checked", on ? "true" : "false");
  clickpad.classList.toggle("pointer-off", !on);
}

async function togglePointer() {
  if (pointerOn) {
    setPointerOn(false);
    island.show({ text: "Puntatore spento", tone: "info", ms: 900 });
    return;
  }
  setPointerOn(true);
  if (await askMotionPermission()) island.show({ text: "Puntatore acceso", tone: "ok", ms: 900 });
  else setPointerOn(false);
}

document.addEventListener("pointerdown", () => { if (pointerOn) askMotionPermission(); }, { capture: true, once: true });

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

/* ---------- Pannelli Tastiera / Altro ---------- */

function openSheet(id) {
  const sheet = $("#" + id);
  sheet.classList.add("open");
  if (id === "panel-keyboard") textInput.focus({ preventScroll: true });
}
function closeSheets() {
  $$(".sheet.open").forEach((sh) => sh.classList.remove("open"));
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
}
$$(".sheet-done").forEach((btn) => btn.addEventListener("click", () => { haptic(); closeSheets(); }));

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
let padActiveZone = null;
let padHoldTimer = null;
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


function centerDown() {
  if (!pointerOn) return;  // a puntatore spento il centro è "OK": si invia al rilascio
  pointerEngine.freeze(250);
  clearInterval(centerFreezeRenew);
  centerFreezeRenew = setInterval(() => pointerEngine.freeze(250), 100);
  centerDragging = false;
  centerHoldTimer = setTimeout(() => {
    centerDragging = true;
    clickpad.classList.add("dragging");
    send({ type: "button", button: "left", down: true });
  }, 350);
}
function centerUp() {
  if (!pointerOn) {
    send({ type: "key", name: "return", mods: [] });
    return;
  }
  clearTimeout(centerHoldTimer);
  clearInterval(centerFreezeRenew);
  centerHoldTimer = null;
  centerFreezeRenew = null;
  if (centerDragging) {
    send({ type: "button", button: "left", down: false });
  } else {
    send({ type: "click", button: "left" });
  }
  centerDragging = false;
  clickpad.classList.remove("dragging");
  pointerEngine.freeze(120);
}

/* Anello: un tocco = freccia. Tenendo il dito fermo per un attimo (o
 * cominciando subito a girare) il cerchio diventa la manopola del volume: le
 * frecce spariscono, un puntino segue il dito e ogni scatto alza (senso
 * orario) o abbassa (antiorario) il volume, come la manopola di una radio.
 * Finché non si capisce quale dei due è, non si invia niente. */
const KNOB_HOLD_MS = 350;   // dito fermo sull'anello: diventa manopola
const KNOB_START_DEG = 25;  // oppure: rotazione che la attiva subito
const KNOB_STEP_DEG = 18;   // uno scatto di volume ogni tot gradi
let ring = null;            // {zone, mode: "pending"|"knob", lastAngle, acc}
const knobArm = $("#knob-dot-arm");
const knobSign = $("#knob-sign");

function angleAt(clientX, clientY) {
  const rect = clickpad.getBoundingClientRect();
  return Math.atan2(clientY - (rect.top + rect.height / 2), clientX - (rect.left + rect.width / 2)) * 180 / Math.PI;
}

function placeKnobDot(angle) {
  // atan2 misura da destra in senso orario; il braccio CSS parte da "ore 12"
  knobArm.style.transform = "rotate(" + (angle + 90) + "deg)";
}

function volumeStep(dir) {
  send({ type: "media", name: dir > 0 ? "volup" : "voldown" });
  haptic();
  knobSign.textContent = dir > 0 ? "+" : "−";
  knobSign.classList.remove("pop");
  void knobSign.offsetWidth;  // riavvia l'animazione
  knobSign.classList.add("pop");
  island.show({ text: dir > 0 ? "Volume +" : "Volume −", tone: "info", ms: 900 });
}

function enterKnob() {
  if (!ring || ring.mode === "knob") return;
  ring.mode = "knob";
  clearTimeout(padHoldTimer);
  setGlow(ring.zone, false);
  knobSign.textContent = "±";
  placeKnobDot(ring.lastAngle);
  clickpad.classList.add("knob");
  haptic();
}

function ringDown(zone, e) {
  ring = { zone: zone, mode: "pending", lastAngle: angleAt(e.clientX, e.clientY), acc: 0 };
  setGlow(zone, true);
  padHoldTimer = setTimeout(enterKnob, KNOB_HOLD_MS);
}

function ringMove(e) {
  if (!ring) return;
  const angle = angleAt(e.clientX, e.clientY);
  let delta = angle - ring.lastAngle;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  ring.lastAngle = angle;
  ring.acc += delta;  // con l'asse y dello schermo verso il basso, positivo = senso orario
  if (ring.mode === "pending") {
    if (Math.abs(ring.acc) < KNOB_START_DEG) return;
    enterKnob();
  }
  placeKnobDot(angle);
  while (ring.acc >= KNOB_STEP_DEG) { volumeStep(+1); ring.acc -= KNOB_STEP_DEG; }
  while (ring.acc <= -KNOB_STEP_DEG) { volumeStep(-1); ring.acc += KNOB_STEP_DEG; }
}

function ringUp() {
  if (!ring) return;
  clearTimeout(padHoldTimer);
  if (ring.mode === "pending") sendArrow(ring.zone);
  setGlow(ring.zone, false);
  clickpad.classList.remove("knob");
  ring = null;
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
    ringDown(zone, e);
  }
});
clickpad.addEventListener("pointermove", (e) => {
  if (padActiveZone && padActiveZone !== "center") ringMove(e);
});

function releasePad() {
  if (!padActiveZone) return;
  if (padActiveZone === "center") {
    clickpad.classList.remove("pressed-center");
    centerUp();
  } else {
    ringUp();
  }
  padActiveZone = null;
}
clickpad.addEventListener("pointerup", releasePad);
clickpad.addEventListener("pointercancel", releasePad);

/* ---------- Pulsanti in alto e voci di "Altro" ---------- */

bindClick($("#btn-power"), togglePointer);
setPointerOn(pointerOn);
bindReactive($("#btn-rightclick"), () => send({ type: "click", button: "right" }));
bindReactive($("#btn-play"), () => {
  if (settings.playMode === "space") send({ type: "key", name: "space", mods: [] });
  else send({ type: "media", name: "play" });
});
bindClick($("#btn-keyboard"), () => openSheet("panel-keyboard"));
bindClick($("#btn-more"), () => openSheet("panel-other"));

bindClick($("#btn-back"), () => send({ type: "key", name: "escape", mods: [] }));
bindClick($("#btn-fullscreen"), () => send({ type: "key", name: "f", mods: [] }));
bindClick($("#btn-mute"), () => {
  send({ type: "media", name: "mute" });
  island.show({ text: "Muto", tone: "info", ms: 900 });
});
bindClick($("#btn-prev"), () => send({ type: "media", name: "prev" }));
bindClick($("#btn-next"), () => send({ type: "media", name: "next" }));

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
let demoDragging = false;
let demoText = "";

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

// In demo non c'è un Mac: l'azione che sarebbe arrivata si vede nella pillola in alto.
function demoSetAction(text) {
  island.show({ text: text, tone: "info", ms: 900 });
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
  } else if (obj.type === "click") {
    demoSetAction(obj.button === "right" ? "Clic destro" : "Clic sinistro");
  } else if (obj.type === "button") {
    if (obj.button === "left" && obj.down) {
      demoDragging = true;
      demoSetAction("Trascinamento…");
    } else if (obj.button === "left" && !obj.down) {
      demoDragging = false;
      demoSetAction("Fine trascinamento");
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

if (demoMode) {
  document.body.classList.add("demo-mode");
  $("#demo-keyboard-wrap").hidden = false;
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
  hasMotion: () => motionSeen,
  getSendCount: () => debugSendCount,
};
