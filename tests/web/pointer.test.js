"use strict";

// Test dell'algoritmo del puntatore. Eseguibili con: node --test tests/web/*.test.js
// Nessuna dipendenza esterna (solo node:test / node:assert).

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { computeDelta, createPointer } = require(path.join(__dirname, "..", "..", "web", "pointer.js"));

const SETTINGS = { sensitivity: 20, invertX: false, invertY: false };

function deg2rad(d) {
  return (d * Math.PI) / 180;
}

// Il telefono è inclinato di theta gradi attorno al proprio asse x: R = Rx(theta).
// Trasforma un vettore espresso nel mondo nel frame del telefono: v_tel = R^T * v_mondo.
function worldToPhone(thetaDeg, v) {
  const c = Math.cos(deg2rad(thetaDeg));
  const s = Math.sin(deg2rad(thetaDeg));
  return {
    x: v.x,
    y: c * v.y + s * v.z,
    z: -s * v.y + c * v.z,
  };
}

function normalize(v) {
  const mag = Math.hypot(v.x, v.y, v.z);
  return { x: v.x / mag, y: v.y / mag, z: v.z / mag };
}

// Gravità (mondo: 0,0,-9.81) vista nel frame del telefono, per un'inclinazione data.
function gravityForTilt(thetaDeg) {
  return normalize(worldToPhone(thetaDeg, { x: 0, y: 0, z: -9.81 }));
}

// "Gira a sinistra" nel mondo = rotazione (0,0,+w) attorno alla verticale.
function rotForTurnLeft(thetaDeg, w) {
  const wTel = worldToPhone(thetaDeg, { x: 0, y: 0, z: w });
  return { alpha: wTel.z, beta: wTel.x, gamma: wTel.y };
}

// "Alza la punta" = rotazione (w,0,0) già nel frame del telefono (attorno al suo asse x).
function rotForRaiseTip(w) {
  return { alpha: 0, beta: w, gamma: 0 };
}

const POSTURES = [
  { name: "piatto (0°)", theta: 0 },
  { name: "inclinato di 40°", theta: 40 },
  { name: "dritto (90°)", theta: 90 },
];

for (const { name, theta } of POSTURES) {
  test(`postura ${name}: girare a sinistra -> dx<0, dy≈0`, () => {
    const rot = rotForTurnLeft(theta, 30);
    const grav = gravityForTilt(theta);
    const { dx, dy } = computeDelta(rot, grav, 0.02, SETTINGS);
    assert.ok(dx < 0, `dx atteso negativo, trovato ${dx}`);
    assert.ok(Math.abs(dy) < 1e-6, `dy atteso ≈0, trovato ${dy}`);
  });

  test(`postura ${name}: alzare la punta -> dy<0, dx≈0`, () => {
    const rot = rotForRaiseTip(30);
    const grav = gravityForTilt(theta);
    const { dx, dy } = computeDelta(rot, grav, 0.02, SETTINGS);
    assert.ok(dy < 0, `dy atteso negativo, trovato ${dy}`);
    assert.ok(Math.abs(dx) < 1e-6, `dx atteso ≈0, trovato ${dx}`);
  });

  test(`postura ${name}: gravità con segno opposto -> stesso risultato`, () => {
    const rot = rotForTurnLeft(theta, 30);
    const grav = gravityForTilt(theta);
    const gravOpp = { x: -grav.x, y: -grav.y, z: -grav.z };
    const a = computeDelta(rot, grav, 0.02, SETTINGS);
    const b = computeDelta(rot, gravOpp, 0.02, SETTINGS);
    assert.ok(Math.abs(a.dx - b.dx) < 1e-9, `dx diversi: ${a.dx} vs ${b.dx}`);
    assert.ok(Math.abs(a.dy - b.dy) < 1e-9, `dy diversi: ${a.dy} vs ${b.dy}`);
  });
}

test("zona morta: 0.5°/s non produce movimento", () => {
  const grav = gravityForTilt(40);
  const rot = rotForRaiseTip(0.5);
  const { dx, dy } = computeDelta(rot, grav, 0.02, SETTINGS);
  assert.ok(dx === 0, `dx=${dx}`);
  assert.ok(dy === 0, `dy=${dy}`);
});

test("zona morta: esattamente sulla soglia (1.5°/s) non produce movimento", () => {
  const grav = gravityForTilt(40);
  const rot = rotForRaiseTip(1.5);
  const { dy } = computeDelta(rot, grav, 0.02, SETTINGS);
  assert.ok(dy === 0, `dy=${dy}`);
});

test("il guadagno cresce con la velocità", () => {
  const grav = gravityForTilt(40);
  const slow = computeDelta(rotForRaiseTip(3), grav, 0.02, SETTINGS);
  const fast = computeDelta(rotForRaiseTip(60), grav, 0.02, SETTINGS);
  const gainSlow = Math.abs(slow.dy) / ((3 - 1.5) * 0.02);
  const gainFast = Math.abs(fast.dy) / ((60 - 1.5) * 0.02);
  assert.ok(gainFast > gainSlow, `gainSlow=${gainSlow} gainFast=${gainFast}`);
  // clamp superiore: sensitivity * 1.8
  assert.ok(gainFast <= SETTINGS.sensitivity * 1.8 + 1e-9);
  assert.ok(gainSlow >= SETTINGS.sensitivity * 0.3 - 1e-9);
});

test("invertX/invertY invertono il segno dello spostamento", () => {
  const grav = gravityForTilt(40);

  const rotX = rotForTurnLeft(40, 30);
  const normalX = computeDelta(rotX, grav, 0.02, SETTINGS);
  const invX = computeDelta(rotX, grav, 0.02, Object.assign({}, SETTINGS, { invertX: true }));
  assert.ok(Math.abs(invX.dx + normalX.dx) < 1e-9);
  assert.equal(invX.dy, normalX.dy);

  const rotY = rotForRaiseTip(30);
  const normalY = computeDelta(rotY, grav, 0.02, SETTINGS);
  const invY = computeDelta(rotY, grav, 0.02, Object.assign({}, SETTINGS, { invertY: true }));
  assert.ok(Math.abs(invY.dy + normalY.dy) < 1e-9);
  assert.ok(invY.dx === normalY.dx, `dx=${invY.dx} vs ${normalY.dx}`);
});

test("senza gravità disponibile si usa l'asse di ripiego letterale (0, 0.643, 0.766)", () => {
  const rot = rotForTurnLeft(40, 30);
  const withFallback = computeDelta(rot, null, 0.02, SETTINGS);
  const withLiteralFallback = computeDelta(rot, { x: 0, y: 0.643, z: 0.766 }, 0.02, SETTINGS);
  assert.ok(Math.abs(withFallback.dx - withLiteralFallback.dx) < 1e-9);
  assert.ok(Math.abs(withFallback.dy - withLiteralFallback.dy) < 1e-9);

  // È comunque vicino, nella sostanza, a un'inclinazione reale di 40° (la costante
  // di ripiego è arrotondata a 3 decimali, quindi la tolleranza resta larga).
  const withExplicit40 = computeDelta(rot, gravityForTilt(40), 0.02, SETTINGS);
  assert.ok(Math.abs(withFallback.dx - withExplicit40.dx) < 5e-3);
});

test("dt=0 non produce movimento (primo campione)", () => {
  const grav = gravityForTilt(40);
  const rot = rotForRaiseTip(30);
  const { dx, dy } = computeDelta(rot, grav, 0, SETTINGS);
  assert.ok(dx === 0, `dx=${dx}`);
  assert.ok(dy === 0, `dy=${dy}`);
});

// ---------- createPointer: stato, dt da now(), freeze ----------

function motionEvent(beta, gravity) {
  return {
    rotationRate: { alpha: 0, beta: beta, gamma: 0 },
    accelerationIncludingGravity: gravity || null,
  };
}

test("createPointer: il primo campione dopo setEnabled ha dt=0", () => {
  let t = 1000;
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => t });
  pointer.setEnabled(true);
  pointer.handleMotion(motionEvent(30));
  const d = pointer.takeDelta();
  assert.equal(d.dx, 0);
  assert.equal(d.dy, 0);
});

test("createPointer: dt è limitato a 0.05s anche dopo una pausa lunga", () => {
  let t = 1000;
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => t });
  pointer.setEnabled(true);
  pointer.handleMotion(motionEvent(30)); // dt=0, azzera il "primo campione"
  pointer.takeDelta();

  t += 5000; // pausa enorme (5s): dt reale sarebbe 5, deve restare clampato a 0.05
  pointer.handleMotion(motionEvent(30));
  const d = pointer.takeDelta();

  const expected = computeDelta({ alpha: 0, beta: 30, gamma: 0 }, null, 0.05, SETTINGS).dy;
  assert.ok(Math.abs(d.dy - expected) < 1e-9, `dy=${d.dy} atteso=${expected}`);
});

test("createPointer: quando è disabilitato non accumula", () => {
  let t = 0;
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => t });
  pointer.handleMotion(motionEvent(30)); // setEnabled non è mai stato chiamato con true
  t += 20;
  pointer.handleMotion(motionEvent(30));
  const d = pointer.takeDelta();
  assert.equal(d.dx, 0);
  assert.equal(d.dy, 0);
});

test("createPointer: freeze blocca l'accumulo fino alla scadenza", () => {
  let t = 0;
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => t });
  pointer.setEnabled(true);
  pointer.handleMotion(motionEvent(30)); // primo campione, dt=0
  pointer.takeDelta();

  t += 20;
  pointer.freeze(250);
  pointer.handleMotion(motionEvent(30)); // durante il freeze: niente accumulo
  let d = pointer.takeDelta();
  assert.equal(d.dx, 0);
  assert.equal(d.dy, 0);

  t += 20; // ancora dentro la finestra di freeze (250ms dalla chiamata)
  pointer.handleMotion(motionEvent(30));
  d = pointer.takeDelta();
  assert.equal(d.dy, 0);

  t += 300; // ora il freeze è scaduto
  pointer.handleMotion(motionEvent(30));
  d = pointer.takeDelta();
  assert.ok(d.dy < 0, `dy atteso negativo dopo la scadenza del freeze, trovato ${d.dy}`);
});

test("createPointer: il filtro passa-basso della gravità converge al valore reale", () => {
  let t = 0;
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => t });
  pointer.setEnabled(true);
  const grav40 = gravityForTilt(40); // non normalizzata: usiamo un vettore proporzionale a 9.81
  const rawGravity = { x: grav40.x * 9.81, y: grav40.y * 9.81, z: grav40.z * 9.81 };

  // tante iterazioni per far convergere il filtro passa-basso (alpha=0.1)
  for (let i = 0; i < 200; i++) {
    t += 16;
    pointer.handleMotion(motionEvent(0, rawGravity));
  }
  pointer.takeDelta();

  t += 16;
  pointer.handleMotion(motionEvent(30, rawGravity));
  const withFilteredGravity = pointer.takeDelta();

  const expected = computeDelta({ alpha: 0, beta: 30, gamma: 0 }, grav40, 0.016, SETTINGS).dy;
  assert.ok(Math.abs(withFilteredGravity.dy - expected) < 1e-3, `dy=${withFilteredGravity.dy} atteso≈${expected}`);
});

/* ---------- Rilevamento automatico degli assi di rotationRate ---------- */

// Generatore pseudo-casuale ripetibile per il rumore dei sensori.
function makeRandom(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296 - 0.5;
  };
}

function encodeRot(w, map, sign) {
  const r = map === "xyz"
    ? { alpha: w.x, beta: w.y, gamma: w.z }
    : { alpha: w.z, beta: w.x, gamma: w.y };
  return { alpha: sign * r.alpha, beta: sign * r.beta, gamma: sign * r.gamma };
}

// Simula un telefono tenuto a `base` gradi che oscilla su/giù (pitch) e/o gira a
// destra/sinistra (yaw), e passa gli eventi al puntatore. Restituisce il tempo finale.
function simulate(pointer, clock, opts) {
  const o = Object.assign({ base: 40, pitchAmp: 25, yawAmp: 0, freq: 1.2, seconds: 3, map: "spec", sign: 1, gravSign: -1, noise: 0, seed: 1 }, opts);
  const rnd = makeRandom(o.seed);
  const steps = Math.round(o.seconds / 0.016);
  for (let i = 0; i < steps; i++) {
    const t = i * 0.016;
    const phase = 2 * Math.PI * o.freq * t;
    const theta = o.base + o.pitchAmp * Math.sin(phase);
    const pitchRate = o.pitchAmp * 2 * Math.PI * o.freq * Math.cos(phase);
    const yawRate = o.yawAmp * 2 * Math.PI * o.freq * Math.cos(phase);
    const yawInPhone = worldToPhone(theta, { x: 0, y: 0, z: yawRate });
    const w = { x: pitchRate + yawInPhone.x, y: yawInPhone.y, z: yawInPhone.z };
    const rot = encodeRot(w, o.map, o.sign);
    rot.alpha += o.noise * 6 * rnd();
    rot.beta += o.noise * 6 * rnd();
    rot.gamma += o.noise * 6 * rnd();
    const g = worldToPhone(theta, { x: 0, y: 0, z: o.gravSign * 9.81 });
    g.x += o.noise * rnd();
    g.y += o.noise * rnd();
    g.z += o.noise * rnd();
    clock.t += 16;
    pointer.handleMotion({ rotationRate: rot, accelerationIncludingGravity: g });
  }
}

const AXIS_CASES = [];
for (const map of ["spec", "xyz"]) {
  for (const sign of [1, -1]) {
    for (const gravSign of [-1, 1]) AXIS_CASES.push({ map, sign, gravSign });
  }
}

for (const c of AXIS_CASES) {
  test(`assi: riconosce map=${c.map} segno=${c.sign} (gravità ${c.gravSign > 0 ? "+" : "-"}) con un'oscillazione su/giù`, () => {
    const clock = { t: 0 };
    const found = [];
    // partiamo apposta dall'ipotesi "sbagliata" per vedere che si corregge
    const wrong = { map: c.map === "spec" ? "xyz" : "spec", sign: 1 };
    const pointer = createPointer({ getSettings: () => SETTINGS, now: () => clock.t, axes: wrong, onAxes: (a) => found.push(a) });
    simulate(pointer, clock, { map: c.map, sign: c.sign, gravSign: c.gravSign, noise: 1 });
    assert.deepEqual(found, [{ map: c.map, sign: c.sign }]);
    assert.deepEqual(pointer.getAxes(), { map: c.map, sign: c.sign, detecting: false });
  });
}

test("assi: girare solo a destra/sinistra non basta per decidere (nessun falso rilevamento)", () => {
  const clock = { t: 0 };
  const found = [];
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => clock.t, onAxes: (a) => found.push(a) });
  simulate(pointer, clock, { map: "xyz", pitchAmp: 0, yawAmp: 30, seconds: 5, noise: 1 });
  assert.deepEqual(found, []);
  assert.equal(pointer.getAxes().detecting, true);
});

test("assi: dopo il rilevamento (xyz) girare a sinistra muove il cursore a sinistra, alzare la punta lo muove su", () => {
  const clock = { t: 0 };
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => clock.t });
  simulate(pointer, clock, { map: "xyz", noise: 1 });
  assert.equal(pointer.getAxes().map, "xyz");
  pointer.setEnabled(true);
  const grav = worldToPhone(40, { x: 0, y: 0, z: -9.81 });
  const send = (w) => {
    for (let i = 0; i < 20; i++) {
      clock.t += 16;
      pointer.handleMotion({ rotationRate: encodeRot(w, "xyz", 1), accelerationIncludingGravity: grav });
    }
    return pointer.takeDelta();
  };
  pointer.takeDelta();
  const left = send(worldToPhone(40, { x: 0, y: 0, z: 30 }));
  assert.ok(left.dx < -10 && Math.abs(left.dy) < 1e-6, `sinistra: ${JSON.stringify(left)}`);
  const up = send({ x: 30, y: 0, z: 0 });
  assert.ok(up.dy < -10 && Math.abs(up.dx) < 1e-6, `su: ${JSON.stringify(up)}`);
});

test("assi: ricalibra riapre il rilevamento, autoDetect=false lo disattiva", () => {
  const clock = { t: 0 };
  const pointer = createPointer({ getSettings: () => SETTINGS, now: () => clock.t });
  simulate(pointer, clock, { map: "xyz" });
  assert.equal(pointer.getAxes().detecting, false);
  pointer.recalibrate();
  assert.equal(pointer.getAxes().detecting, true);

  const fixed = createPointer({ getSettings: () => SETTINGS, now: () => clock.t, axes: { map: "spec", sign: 1 }, autoDetect: false });
  simulate(fixed, clock, { map: "xyz" });
  assert.deepEqual(fixed.getAxes(), { map: "spec", sign: 1, detecting: false });
});

test("computeDelta: la mappa xyz legge l'asse x da alpha", () => {
  const grav = gravityForTilt(40);
  const spec = computeDelta({ alpha: 0, beta: 30, gamma: 0 }, grav, 0.02, SETTINGS);
  const xyz = computeDelta({ alpha: 30, beta: 0, gamma: 0 }, grav, 0.02, SETTINGS, { map: "xyz", sign: 1 });
  assert.ok(Math.abs(spec.dy - xyz.dy) < 1e-12 && spec.dy < 0);
});
