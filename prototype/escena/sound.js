/*
 * Sonido del Centro de Agentes (PRODUCT.md §5.4). Sin archivos de audio: todo se sintetiza con Web Audio
 * en estilo 8 bits, así no hay licencias y combina con el pixel art.
 *
 * Sonidos (cada uno se activa o desactiva por separado; además hay un interruptor general y volumen):
 *   done     «terminó»: arpegio ascendente con el tono del agente (trabajando → en reposo)
 *   start    «empezó»: dos blips cortos (en reposo/sin datos → trabajando)
 *   offline  «sin conexión»: dos notas que bajan (se cae la API después de haber estado conectada)
 *   ambient  ambiente: zumbido suave de sala de servidores, continuo
 * Por defecto todo apagado; al encender el general quedan activos «terminó» y «sin conexión».
 * La configuración se guarda en el navegador (localStorage) y es solo de quien mira.
 *
 * Reglas anti-ruido: el primer estado al abrir no suena; «terminó» solo si el agente trabajó al menos
 * MIN_WORK_MS; cada agente espera COOLDOWN_MS entre sonidos.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CentroSound = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const KEY = "centro.sonido.v1";
  const DEFAULTS = { on: false, volume: 0.5, done: true, start: false, offline: true, ambient: false };
  const KINDS = ["done", "start", "offline", "ambient"];
  const MIN_WORK_MS = 5000, COOLDOWN_MS = 4000;
  // un tono por agente (escala pentatónica: suenan bien juntos y se distinguen de oído)
  const NOTES = { codex: 523.25, claude: 587.33, vision: 659.25, ultron: 783.99, gentle: 880.0, antigravity: 1046.5 };

  function createSound(opts) {
    opts = opts || {};
    const storage = opts.storage || null;
    const AC = opts.AudioContext || (typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext)) || null;
    let cfg = load(), ac = null, master = null, amb = null, conn = null;
    const prev = {}, since = {}, lastAt = {}, log = [];

    function load() {
      try { return Object.assign({}, DEFAULTS, JSON.parse((storage && storage.getItem(KEY)) || "{}")); }
      catch (e) { return Object.assign({}, DEFAULTS); }
    }
    function save() { try { storage && storage.setItem(KEY, JSON.stringify(cfg)); } catch (e) { /* navegador sin almacenamiento */ } }
    function gainFor(v) { return Math.pow(Math.max(0, Math.min(1, v)), 2) * 0.9; }

    function ctx() {
      if (!AC) return null;
      if (!ac) {
        try { ac = new AC(); } catch (e) { return null; }
        master = ac.createGain(); master.gain.value = gainFor(cfg.volume); master.connect(ac.destination);
      }
      if (ac.state === "suspended" && ac.resume) ac.resume().catch(() => {});
      return ac;
    }
    // una nota con envolvente corta (ataque 5 ms, caída exponencial): sin clics
    function note(c, freq, t0, dur, type, vol, slideTo) {
      const o = c.createOscillator(), g = c.createGain();
      o.type = type; o.frequency.setValueAtTime(freq, t0);
      if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(vol, t0 + 0.005);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      o.connect(g); g.connect(master); o.start(t0); o.stop(t0 + dur + 0.02);
    }
    const SFX = {
      done(c, id) {
        const f = NOTES[id] || 660, t = c.currentTime + 0.01;
        note(c, f, t, 0.11, "square", 0.28); note(c, f * 1.25, t + 0.09, 0.11, "square", 0.28);
        note(c, f * 1.5, t + 0.18, 0.11, "square", 0.28); note(c, f * 2, t + 0.27, 0.26, "square", 0.22);
      },
      start(c, id) {
        const f = NOTES[id] || 660, t = c.currentTime + 0.01;
        note(c, f / 2, t, 0.06, "triangle", 0.35); note(c, f, t + 0.07, 0.08, "triangle", 0.35);
      },
      offline(c) {
        const t = c.currentTime + 0.01;
        note(c, 440, t, 0.22, "triangle", 0.4, 392); note(c, 330, t + 0.24, 0.42, "triangle", 0.4, 220);
      },
    };

    // ambiente: ruido filtrado muy grave + un zumbido de 55 Hz, a volumen bajo
    function syncAmbient() {
      const want = cfg.on && cfg.ambient;
      if (want && !amb) {
        const c = ctx(); if (!c) return;
        const buf = c.createBuffer(1, c.sampleRate * 2, c.sampleRate), d = buf.getChannelData(0);
        let lastN = 0;
        for (let i = 0; i < d.length; i++) { lastN = (lastN + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = lastN * 3.5; }
        const src = c.createBufferSource(); src.buffer = buf; src.loop = true;
        const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 420;
        const hum = c.createOscillator(); hum.type = "sine"; hum.frequency.value = 55;
        const g = c.createGain(); g.gain.value = 0.0001; g.gain.exponentialRampToValueAtTime(0.18, c.currentTime + 1.5);
        const hg = c.createGain(); hg.gain.value = 0.25;
        src.connect(lp); lp.connect(g); hum.connect(hg); hg.connect(g); g.connect(master);
        src.start(); hum.start();
        amb = { src, hum, g };
      } else if (!want && amb) {
        const a = amb; amb = null;
        try {
          a.g.gain.setTargetAtTime(0.0001, ac.currentTime, 0.2);
          setTimeout(() => { try { a.src.stop(); a.hum.stop(); } catch (e) { /* ya detenido */ } }, 900);
        } catch (e) { /* sin contexto */ }
      }
    }

    // Reproduce un sonido si el general y ese sonido están activos. `log` registra todo (para pruebas).
    function play(kind, id, force) {
      const enabled = cfg.on && cfg[kind];
      const entry = { kind, id: id || null, played: false, t: Date.now() };
      log.push(entry); if (log.length > 200) log.shift();
      if (!enabled && !force) return false;
      const c = ctx(); if (!c || !SFX[kind]) return false;
      SFX[kind](c, id); entry.played = true;
      return true;
    }
    function emit(kind, id, now) {
      if (lastAt[id] !== undefined && now - lastAt[id] < COOLDOWN_MS) return;
      lastAt[id] = now; play(kind, id);
    }

    // Recibe el estado real de cada agente ({codex: "working", ...}) y detecta cambios.
    function observe(states, now) {
      for (const id of Object.keys(states)) {
        const s = states[id], p = prev[id];
        if (p === undefined) { prev[id] = s; since[id] = now; continue; }          // primer estado: no suena
        if (s === p) continue;
        if (p === "working" && s === "idle" && now - since[id] >= MIN_WORK_MS) emit("done", id, now);
        else if (s === "working") emit("start", id, now);
        prev[id] = s; since[id] = now;
      }
    }
    // Conexión con la API: suena solo al PERDERLA después de haber estado conectada.
    function connection(online) {
      if (conn === true && online === false) play("offline");
      conn = online;
    }

    function set(key, value) {
      if (!(key in DEFAULTS)) return;
      cfg[key] = key === "volume" ? Math.max(0, Math.min(1, +value)) : !!value;
      save();
      if (master) master.gain.value = gainFor(cfg.volume);
      if (cfg.on) ctx();
      syncAmbient();
    }
    // Los navegadores no dejan sonar nada hasta el primer clic: se reactiva ahí si estaba encendido.
    function unlock() { if (cfg.on) { ctx(); syncAmbient(); } }

    return { get: () => Object.assign({}, cfg), set, play, observe, connection, unlock, log, KINDS, NOTES };
  }

  return { createSound, DEFAULTS, KINDS, NOTES, MIN_WORK_MS, COOLDOWN_MS, KEY };
});
