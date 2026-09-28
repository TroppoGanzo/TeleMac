"use strict";
/*
 * Matematica del puntatore "ad aria".
 *
 * Usiamo la velocità angolare del telefono (rotationRate, gradi/secondo) invece
 * dell'orientamento assoluto: ogni campione calcoliamo di quanto il telefono ha
 * ruotato attorno all'asse "verticale del mondo" (yaw, per sinistra/destra) e
 * attorno al proprio asse orizzontale (pitch, per su/giù), esattamente come fa
 * un mouse vero. Non serve calibrazione né si accumula deriva.
 *
 * L'asse "verticale del mondo" visto dal telefono si chiama qui `u`: è la
 * direzione della gravità (capovolta) misurata dall'accelerometro, filtrata
 * passa-basso per togliere il rumore. Se non è disponibile si usa un valore di
 * ripiego che corrisponde a un telefono tenuto inclinato di 40°.
 *
 * Assi di rotationRate: la specifica W3C dice alpha = asse z, beta = x,
 * gamma = y, ma i browser non sono d'accordo fra loro (Chrome, e forse Safari,
 * usano alpha = x, beta = y, gamma = z). Qui gli assi sono una "mappa"
 * esplicita e createPointer la può scoprire da sola: quando il telefono si
 * inclina avanti/indietro, la gravità vista dal telefono ruota nel piano y-z
 * esattamente alla velocità angolare attorno all'asse x (dθ/dt = ωx, con
 * θ = atan2(gy, gz), qualunque sia il segno della gravità). Basta vedere quale
 * componente di rotationRate segue quel valore, e con che segno.
 *
 * Nota per chi tocca questo file: l'algoritmo è stato verificato con una
 * simulazione (vedi tests/web/pointer.test.js) — non cambiare le costanti o
 * l'ordine delle operazioni senza rifare la verifica.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TeleMacPointer = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  var DEADZONE_DEG_S = 1.5;
  var GRAVITY_ALPHA = 0.1;
  var GAIN_MIN = 0.3;
  var GAIN_MAX = 1.8;
  var GAIN_SPEED_DIV = 80;
  var DT_MAX = 0.05;
  var FALLBACK_U = { x: 0, y: 0.643, z: 0.766 };

  // Quale campo di rotationRate corrisponde a ciascun asse del telefono.
  var AXIS_MAPS = {
    spec: { x: "beta", y: "gamma", z: "alpha" }, // specifica W3C
    xyz: { x: "alpha", y: "beta", z: "gamma" }, // come Chrome (e forse Safari)
  };
  var DEFAULT_AXES = { map: "spec", sign: 1 };

  // Rilevamento automatico degli assi (vedi commento in cima).
  var DETECT_MIN_RATE = 20; // °/s di inclinazione per considerare un campione
  var DETECT_EVIDENCE = 90; // gradi di inclinazione "forte" da accumulare
  var DETECT_MIN_SAMPLES = 8;
  var DETECT_MIN_CORR = 0.7;
  var DETECT_MIN_GAP = 0.2; // distacco minimo fra le due ipotesi
  var FAST_GRAVITY_ALPHA = 0.5;

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function softDeadzone(r, dz) {
    var s = r < 0 ? -1 : r > 0 ? 1 : 0;
    return s * Math.max(0, Math.abs(r) - dz);
  }

  /* Stabilizzazione (anti-tremolio): filtro "One Euro" (Casiez et al., 2012).
   * È un passa-basso che si adatta alla velocità: con la mano quasi ferma
   * filtra molto (via il tremolio), nei movimenti decisi quasi niente (niente
   * ritardo). `stabilization` va da 0 (spento) a 1 (massimo). */
  var STAB_BETA = 0.004;       // quanto la velocità "apre" il filtro
  var STAB_D_CUTOFF = 1.0;     // Hz, filtro sulla derivata
  function stabMinCutoff(st) { return 3.0 - 2.4 * st; }   // Hz: da 3 (poco) a 0.6 (tanto)
  function stabDeadzone(st) { return DEADZONE_DEG_S + 2.5 * st; }

  function createOneEuro() {
    var xPrev = null;
    var dxPrev = 0;
    function alpha(cutoff, dt) {
      var tau = 1 / (2 * Math.PI * cutoff);
      return 1 / (1 + tau / dt);
    }
    return {
      filter: function (x, dt, minCutoff) {
        if (xPrev === null || !(dt > 0)) {
          xPrev = x;
          dxPrev = 0;
          return x;
        }
        var dx = (x - xPrev) / dt;
        dxPrev += alpha(STAB_D_CUTOFF, dt) * (dx - dxPrev);
        var cutoff = minCutoff + STAB_BETA * Math.abs(dxPrev);
        xPrev += alpha(cutoff, dt) * (x - xPrev);
        return xPrev;
      },
      reset: function () { xPrev = null; dxPrev = 0; },
    };
  }

  function resolveUp(gravityUnit) {
    if (!gravityUnit) return FALLBACK_U;
    var u = { x: gravityUnit.x, y: gravityUnit.y, z: gravityUnit.z };
    if (u.y + u.z < 0) {
      u.x = -u.x;
      u.y = -u.y;
      u.z = -u.z;
    }
    return u;
  }

  function normalizeAxes(axes) {
    if (!axes || !AXIS_MAPS[axes.map]) return { map: DEFAULT_AXES.map, sign: DEFAULT_AXES.sign };
    return { map: axes.map, sign: axes.sign === -1 ? -1 : 1 };
  }

  // ω nel frame del telefono (x = destra, y = alto, z = fuori dallo schermo).
  function readOmega(rot, axes) {
    var a = normalizeAxes(axes);
    var m = AXIS_MAPS[a.map];
    return { x: a.sign * rot[m.x], y: a.sign * rot[m.y], z: a.sign * rot[m.z] };
  }

  // computeDelta: funzione pura, nessuno stato. rot in gradi/secondo,
  // gravityUnit normalizzato (o null), dt in secondi, settings {sensitivity,invertX,invertY},
  // axes {map: "spec"|"xyz", sign: 1|-1} (facoltativo, default: specifica W3C).
  function computeDelta(rot, gravityUnit, dt, settings, axes) {
    var u = resolveUp(gravityUnit);
    var w = readOmega(rot, axes);
    var wx = w.x; // attorno all'asse x del telefono ("alza la punta")
    var wy = w.y;
    var wz = w.z;

    var yaw = wx * u.x + wy * u.y + wz * u.z; // ω·u: rotazione attorno alla verticale del mondo
    var pitch = wx; // ω.x

    var dz = stabDeadzone(settings.stabilization || 0);
    var yawP = softDeadzone(yaw, dz);
    var pitchP = softDeadzone(pitch, dz);
    var speed = Math.hypot(yawP, pitchP);
    var gain = settings.sensitivity * clamp(GAIN_MIN + speed / GAIN_SPEED_DIV, GAIN_MIN, GAIN_MAX);

    var dx = -yawP * dt * gain;
    var dy = -pitchP * dt * gain;

    if (settings.invertX) dx = -dx;
    if (settings.invertY) dy = -dy;

    return { dx: dx, dy: dy };
  }

  // createPointer: oggetto con stato che collega computeDelta agli eventi reali.
  function createPointer(opts) {
    opts = opts || {};
    var getSettings = opts.getSettings || function () {
      return { sensitivity: 20, invertX: false, invertY: false };
    };
    var now = opts.now || function () {
      return typeof performance !== "undefined" ? performance.now() : Date.now();
    };

    var enabled = false;
    var lastTime = null;
    var freezeUntil = 0;
    var accX = 0;
    var accY = 0;
    var gravFilt = null; // {x,y,z} non normalizzato, filtrato passa-basso
    var axes = normalizeAxes(opts.axes);
    var onAxes = opts.onAxes || null;
    var detect = opts.autoDetect === false ? null : newDetection();
    var stab = { alpha: createOneEuro(), beta: createOneEuro(), gamma: createOneEuro() };

    function newDetection() {
      return { fast: null, prevTheta: null, sAlpha: 0, sBeta: 0, nAlpha: 0, nBeta: 0, nTheta: 0, evidence: 0, samples: 0 };
    }

    // Aggiorna le statistiche per capire se il "pitch" (asse x) sta in alpha o in beta.
    function observeAxes(rr, g, dt) {
      var d = detect;
      if (d.fast === null) {
        d.fast = { x: g.x, y: g.y, z: g.z };
      } else {
        d.fast.x += (g.x - d.fast.x) * FAST_GRAVITY_ALPHA;
        d.fast.y += (g.y - d.fast.y) * FAST_GRAVITY_ALPHA;
        d.fast.z += (g.z - d.fast.z) * FAST_GRAVITY_ALPHA;
      }
      var gyz = Math.hypot(d.fast.y, d.fast.z);
      var gAll = Math.hypot(d.fast.x, d.fast.y, d.fast.z);
      if (gAll < 1e-6 || gyz < 0.5 * gAll) {
        d.prevTheta = null; // telefono troppo di lato: l'angolo nel piano y-z non è affidabile
        return;
      }
      var theta = (Math.atan2(d.fast.y, d.fast.z) * 180) / Math.PI;
      var prev = d.prevTheta;
      d.prevTheta = theta;
      if (prev === null || !(dt > 0)) return;
      var dTheta = theta - prev;
      if (dTheta > 180) dTheta -= 360;
      if (dTheta < -180) dTheta += 360;
      var rate = dTheta / dt;
      if (Math.abs(rate) < DETECT_MIN_RATE) return;

      d.sAlpha += rate * rr.alpha;
      d.sBeta += rate * rr.beta;
      d.nAlpha += rr.alpha * rr.alpha;
      d.nBeta += rr.beta * rr.beta;
      d.nTheta += rate * rate;
      d.evidence += Math.abs(dTheta);
      d.samples += 1;
      if (d.evidence < DETECT_EVIDENCE || d.samples < DETECT_MIN_SAMPLES) return;

      var corrAlpha = d.nAlpha > 0 ? d.sAlpha / Math.sqrt(d.nAlpha * d.nTheta) : 0;
      var corrBeta = d.nBeta > 0 ? d.sBeta / Math.sqrt(d.nBeta * d.nTheta) : 0;
      var best = Math.abs(corrAlpha) >= Math.abs(corrBeta)
        ? { map: "xyz", corr: corrAlpha, other: corrBeta }
        : { map: "spec", corr: corrBeta, other: corrAlpha };
      if (Math.abs(best.corr) >= DETECT_MIN_CORR && Math.abs(best.corr) - Math.abs(best.other) >= DETECT_MIN_GAP) {
        axes = { map: best.map, sign: best.corr > 0 ? 1 : -1 };
        detect = null; // deciso: non si cambia più finché non si ricalibra
        if (onAxes) {
          try { onAxes({ map: axes.map, sign: axes.sign }); } catch (e) { /* niente */ }
        }
      } else {
        // Dati ambigui (es. molto rumore): ricominciamo da capo con campioni nuovi.
        var fresh = newDetection();
        fresh.fast = d.fast;
        fresh.prevTheta = d.prevTheta;
        detect = fresh;
      }
    }

    function recalibrate() {
      detect = newDetection();
    }

    function getAxes() {
      return { map: axes.map, sign: axes.sign, detecting: detect !== null };
    }

    function setEnabled(value) {
      enabled = !!value;
      stab.alpha.reset();
      stab.beta.reset();
      stab.gamma.reset();
      lastTime = null; // il campione dopo un cambio di stato riparte con dt=0
    }

    function freeze(ms) {
      freezeUntil = now() + ms;
    }

    function handleMotion(event) {
      var rr = event && event.rotationRate;
      if (!rr || rr.alpha == null || rr.beta == null || rr.gamma == null) return;

      var t = now();
      var dt;
      if (lastTime === null) {
        dt = 0;
      } else {
        dt = clamp((t - lastTime) / 1000, 0, DT_MAX);
      }
      lastTime = t;

      var g = event.accelerationIncludingGravity;
      if (g && g.x != null && g.y != null && g.z != null) {
        if (detect) observeAxes(rr, g, dt);
        if (gravFilt === null) {
          gravFilt = { x: g.x, y: g.y, z: g.z };
        } else {
          gravFilt.x += (g.x - gravFilt.x) * GRAVITY_ALPHA;
          gravFilt.y += (g.y - gravFilt.y) * GRAVITY_ALPHA;
          gravFilt.z += (g.z - gravFilt.z) * GRAVITY_ALPHA;
        }
      }

      if (!enabled) return;

      var settings = getSettings();
      var rot = { alpha: rr.alpha, beta: rr.beta, gamma: rr.gamma };
      var st = settings.stabilization || 0;
      if (st > 0) {
        var mc = stabMinCutoff(st);
        rot = {
          alpha: stab.alpha.filter(rr.alpha, dt, mc),
          beta: stab.beta.filter(rr.beta, dt, mc),
          gamma: stab.gamma.filter(rr.gamma, dt, mc),
        };
      }

      if (t < freezeUntil) return; // congelato: non accumula

      var gravityUnit = null;
      if (gravFilt) {
        var mag = Math.hypot(gravFilt.x, gravFilt.y, gravFilt.z);
        if (mag > 1e-6) {
          gravityUnit = { x: gravFilt.x / mag, y: gravFilt.y / mag, z: gravFilt.z / mag };
        }
      }

      var d = computeDelta(rot, gravityUnit, dt, settings, axes);
      accX += d.dx;
      accY += d.dy;
    }

    function takeDelta() {
      var d = { dx: accX, dy: accY };
      accX = 0;
      accY = 0;
      return d;
    }

    return {
      handleMotion: handleMotion,
      freeze: freeze,
      setEnabled: setEnabled,
      takeDelta: takeDelta,
      recalibrate: recalibrate,
      getAxes: getAxes,
    };
  }

  return { computeDelta: computeDelta, createPointer: createPointer, AXIS_MAPS: AXIS_MAPS };
});
