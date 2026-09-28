"use strict";

/* ---------- Blocco zoom / scroll / rimbalzo: il telecomando deve restare fermo ---------- */

document.addEventListener("gesturestart", (e) => e.preventDefault());
document.addEventListener("gesturechange", (e) => e.preventDefault());
document.addEventListener("dblclick", (e) => e.preventDefault(), { passive: false });

let lastTouchEnd = 0;
document.addEventListener(
  "touchend",
  (e) => {
    const now = Date.now();
    if (now - lastTouchEnd < 350) e.preventDefault(); // niente doppio-tap-zoom
    lastTouchEnd = now;
  },
  { passive: false }
);

document.addEventListener(
  "touchmove",
  (e) => {
    if (e.touches.length > 1) e.preventDefault(); // niente pinch-zoom
  },
  { passive: false }
);

/* ---------- Piccola cassetta degli attrezzi ---------- */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function tapFeedback(el) {
  el.classList.add("pressed");
  setTimeout(() => el.classList.remove("pressed"), 120);
}

// Ogni pulsante ha un piccolo "scatto" visivo al tocco: è la cosa più vicina a un
// feedback fisico che Safari su iPhone ci lascia fare (niente vibrazione reale sul web).
document.addEventListener(
  "touchstart",
  (e) => {
    const btn = e.target.closest("button");
    if (btn) tapFeedback(btn);
  },
  { passive: true }
);

/* ---------- Connessione al Mac ---------- */

const statusEl = $("#status");
let socket = null;
let reconnectDelay = 1000;

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status" + (cls ? " " + cls : "");
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.onopen = () => {
    setStatus("Collegato", "ok");
    reconnectDelay = 1000;
  };
  socket.onclose = () => {
    setStatus("Riconnessione...", "err");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, 8000);
  };
  socket.onerror = () => socket.close();
}

connect();

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  }
}

/* ---------- Schede in basso ---------- */

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#panel-${tab.dataset.panel}`).classList.add("active");
  });
});

/* ---------- Pulsanti generici: tasti, media, mouse ---------- */

$$("[data-key]").forEach((btn) => {
  btn.addEventListener("click", () => send({ type: "key", name: btn.dataset.key, mods: [] }));
});
$$("[data-media]").forEach((btn) => {
  btn.addEventListener("click", () => send({ type: "media", name: btn.dataset.media }));
});

let playIsSpace = false; // impostabile in futuro; per ora manda il tasto multimediale Play
$("#play-btn").addEventListener("click", () => {
  send(playIsSpace ? { type: "key", name: "space", mods: [] } : { type: "media", name: "play" });
});

$("#right-click-btn").addEventListener("click", () => send({ type: "click", button: "right" }));

$("#spotlight-btn").addEventListener("click", () => send({ type: "key", name: "space", mods: ["cmd"] }));
$("#urlbar-btn").addEventListener("click", () => send({ type: "key", name: "l", mods: ["cmd"] }));

$("#switch-app-btn").addEventListener("click", () => send({ type: "key", name: "tab", mods: ["cmd"] }));
$("#mission-control-btn").addEventListener("click", () => send({ type: "key", name: "up", mods: ["ctrl"] }));
$("#back-btn").addEventListener("click", () => send({ type: "key", name: "left", mods: ["cmd"] }));
$("#fwd-btn").addEventListener("click", () => send({ type: "key", name: "right", mods: ["cmd"] }));
$("#reload-btn").addEventListener("click", () => send({ type: "key", name: "r", mods: ["cmd"] }));
$("#newtab-btn").addEventListener("click", () => send({ type: "key", name: "t", mods: ["cmd"] }));
$("#closetab-btn").addEventListener("click", () => send({ type: "key", name: "w", mods: ["cmd"] }));
$("#sleep-btn").addEventListener("click", () => send({ type: "sleep" }));

/* ---------- Tastiera ---------- */

const textInput = $("#text-input");
let lastValue = "";

textInput.addEventListener("input", () => {
  const value = textInput.value;
  let prefix = 0;
  while (prefix < value.length && prefix < lastValue.length && value[prefix] === lastValue[prefix]) prefix++;
  const removed = lastValue.length - prefix;
  const added = value.slice(prefix);
  for (let i = 0; i < removed; i++) send({ type: "key", name: "backspace", mods: [] });
  if (added) send({ type: "text", text: added });
  lastValue = value;
  // Non lasciamo crescere il campo all'infinito: lo svuotiamo ogni tanto.
  if (value.length > 200) {
    textInput.value = "";
    lastValue = "";
  }
});

/* ---------- Impostazioni (salvate sul telefono) ---------- */

const settings = Object.assign(
  { sensitivity: 80, invertX: false, invertY: false, swapAxes: false },
  JSON.parse(localStorage.getItem("telemac-settings") || "{}")
);

function saveSettings() {
  localStorage.setItem("telemac-settings", JSON.stringify(settings));
}

const sensitivityEl = $("#sensitivity");
const invertXEl = $("#invert-x");
const invertYEl = $("#invert-y");
const swapAxesEl = $("#swap-axes");

sensitivityEl.value = settings.sensitivity;
invertXEl.checked = settings.invertX;
invertYEl.checked = settings.invertY;
swapAxesEl.checked = settings.swapAxes;

sensitivityEl.addEventListener("input", () => {
  settings.sensitivity = Number(sensitivityEl.value);
  saveSettings();
});
invertXEl.addEventListener("change", () => {
  settings.invertX = invertXEl.checked;
  saveSettings();
});
invertYEl.addEventListener("change", () => {
  settings.invertY = invertYEl.checked;
  saveSettings();
});
swapAxesEl.addEventListener("change", () => {
  settings.swapAxes = swapAxesEl.checked;
  saveSettings();
});

/* ---------- Puntatore a giroscopio ----------
 *
 * Usiamo la VELOCITÀ angolare (rotationRate, gradi/secondo) e non l'orientamento
 * assoluto: ogni fotogramma calcoliamo quanto il telefono ha ruotato da un istante
 * all'altro e lo trasformiamo in uno spostamento del cursore, esattamente come si
 * muove un mouse vero. Vantaggio: nessuna calibrazione, nessuna deriva da correggere.
 */

const gyroToggle = $("#gyro-toggle");
const pointerPad = $(".pointer-pad");
let gyroActive = false;
let lastMotionTime = null;

const DEADZONE_DEG_S = 2; // ignora i tremolii naturali della mano

function onDeviceMotion(e) {
  const rr = e.rotationRate;
  if (!rr || rr.beta === null) return;

  const now = performance.now();
  if (lastMotionTime === null) {
    lastMotionTime = now;
    return;
  }
  const dt = Math.min((now - lastMotionTime) / 1000, 0.1); // clamp: evita scatti dopo una pausa
  lastMotionTime = now;

  let pitch = Math.abs(rr.beta) > DEADZONE_DEG_S ? rr.beta : 0; // su/giù
  let yaw = Math.abs(rr.alpha) > DEADZONE_DEG_S ? rr.alpha : 0; // sinistra/destra

  if (settings.swapAxes) [pitch, yaw] = [yaw, pitch];

  const pxPerDeg = settings.sensitivity / 30;
  let dx = yaw * dt * pxPerDeg;
  let dy = pitch * dt * pxPerDeg;

  if (settings.invertX) dx = -dx;
  if (settings.invertY) dy = -dy;

  if (dx || dy) send({ type: "move", dx, dy });
}

async function enableGyro() {
  try {
    if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
      const result = await DeviceMotionEvent.requestPermission();
      if (result !== "granted") {
        setStatus("Permesso movimento negato", "err");
        return;
      }
    }
    window.addEventListener("devicemotion", onDeviceMotion);
    gyroActive = true;
    lastMotionTime = null;
    gyroToggle.textContent = "Puntatore attivo";
    gyroToggle.classList.add("active");
    pointerPad.hidden = false;
  } catch (err) {
    setStatus("Giroscopio non disponibile", "err");
  }
}

function disableGyro() {
  window.removeEventListener("devicemotion", onDeviceMotion);
  gyroActive = false;
  gyroToggle.textContent = "Attiva puntatore";
  gyroToggle.classList.remove("active");
  pointerPad.hidden = true;
}

gyroToggle.addEventListener("click", () => (gyroActive ? disableGyro() : enableGyro()));

/* ---------- OK = clic sinistro; tenuto premuto = trascinamento ---------- */

const okBtn = $("#ok-btn");
let okHoldTimer = null;
let okDragging = false;

okBtn.addEventListener(
  "touchstart",
  (e) => {
    e.preventDefault();
    okDragging = false;
    okHoldTimer = setTimeout(() => {
      okDragging = true;
      send({ type: "button", button: "left", down: true });
    }, 350);
  },
  { passive: false }
);

okBtn.addEventListener("touchend", (e) => {
  e.preventDefault();
  clearTimeout(okHoldTimer);
  if (okDragging) {
    send({ type: "button", button: "left", down: false });
    okDragging = false;
  } else {
    send({ type: "click", button: "left" });
  }
});
