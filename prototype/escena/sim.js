/*
 * Centro de Agentes — motor de comportamiento de los robots (V4).
 * Lógica pura, sin DOM: la usa la escena (index.html) y la prueban los escenarios de tests/test_sim.js.
 *
 * Reglas (PRODUCT.md §4):
 *  - "working"  → el robot va a su box y trabaja. Si estaba en una actividad, la INTERRUMPE de inmediato
 *                 y vuelve por una ruta válida del grafo (tools/escena/nav.py).
 *  - "idle"     → recién después de un margen de estabilidad (idleStableS) sale del box: festeja con el
 *                 puño si venía de trabajar y elige una actividad al azar (ocupación: un robot por lugar).
 *                 Juegos de mesa (ajedrez o UNO) solo con pareja: uno espera en la mesa y, si llega otro
 *                 robot inactivo, juegan juntos; si nadie llega, se va.
 *  - "unknown"  → después de unknownStableS vuelve al box y muestra el visor apagado con «?».
 *  - La vida en ocio es DECORATIVA: no representa consumo ni actividad real.
 * Posiciones en px de la referencia (1672×942); la posición de un robot es la de sus pies.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CentroSim = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DEFAULTS = {
    speed: 70,                 // px de referencia por segundo
    idleStableS: 15,           // margen de estabilidad antes de salir del box
    unknownStableS: 5,
    floorSplitY: 470,          // por debajo: tamaño L
    fistS: 1.6,
    waitTableS: 25,            // cuánto espera en la mesa a una pareja
    durations: { sofa: [25, 60], oil: [15, 30], recarga: [30, 70], paseo_holo: [6, 12], paseo_lounge: [6, 12], game: [40, 80] },
    weights: { sofa: 3, oil: 3, recarga: 2, paseo_holo: 1, paseo_lounge: 1, table: 3 },
    reducedMotion: false,      // true: los robots aparecen en el destino sin caminar
  };
  const ACT_POSE = { oil: "oil", sofa: "sit", recarga: "sleep", paseo_holo: "wave", paseo_lounge: "wave" };
  const TABLE = ["chess_a", "chess_b"];

  function createSim(NAV, agents, opts = {}) {
    const cfg = Object.assign({}, DEFAULTS, opts, { durations: Object.assign({}, DEFAULTS.durations, opts.durations || {}),
      weights: Object.assign({}, DEFAULTS.weights, opts.weights || {}) });
    let seed = opts.seed || 12345;
    const rnd = opts.random || (() => ((seed = (seed * 16807) % 2147483647) / 2147483647));
    const graph = {};
    for (const [a, b] of NAV.edges) { (graph[a] = graph[a] || []).push(b); (graph[b] = graph[b] || []).push(a); }
    const pt = id => NAV.nodes[id] || NAV.spots[id];
    const dist = (a, b) => Math.hypot(pt(a)[0] - pt(b)[0], pt(a)[1] - pt(b)[1]);

    function route(from, to) {            // Dijkstra por distancia sobre el grafo validado
      const d = { [from]: 0 }, prev = {}, done = new Set();
      while (true) {
        let u = null, best = Infinity;
        for (const k in d) if (!done.has(k) && d[k] < best) { best = d[k]; u = k; }
        if (u === null || u === to) break;
        done.add(u);
        for (const v of graph[u] || []) {
          const nd = d[u] + dist(u, v);
          if (d[v] === undefined || nd < d[v]) { d[v] = nd; prev[v] = u; }
        }
      }
      if (d[to] === undefined) return [];
      const path = [to];
      while (path[0] !== from) path.unshift(prev[path[0]]);
      return path;
    }

    const occupied = {};                  // lugar → agente (reserva)
    let time = 0;
    const R = {};
    for (const id of agents) {
      const seat = "seat_" + id;
      R[id] = { id, seat, real: "working", realSince: 0, mode: "desk", pos: pt(seat).slice(), node: seat,
                path: [], target: null, activity: null, timer: 0, dir: 1, pose: "desk", wasWorking: true,
                game: null, partner: null, moving: [0, 0] };
    }

    const free = s => !occupied[s];
    function reserve(spot, id) { occupied[spot] = id; }
    function release(r) { for (const s in occupied) if (occupied[s] === r.id) delete occupied[s]; }

    function goTo(r, target, then) {
      const p = route(r.node, target);
      r.path = p.length ? p.slice(1) : []; r.target = target; r.then = then; r.mode = "walk";
      if (cfg.reducedMotion || !r.path.length) arrive(r, target);
    }
    function arrive(r, target) {
      r.pos = pt(target).slice(); r.node = target; r.path = [];
      const then = r.then; r.then = null;
      if (then) then(r);
    }

    function pick(min, max) { return min + rnd() * (max - min); }
    function chooseActivity(r) {
      // ¿alguien espera pareja en la mesa?
      const waiting = TABLE.map(s => occupied[s]).filter(a => a && a !== r.id && R[a].mode === "activity" && R[a].activity === "table_wait");
      if (waiting.length) {
        const host = R[waiting[0]], spot = TABLE.find(free);
        if (spot) {
          reserve(spot, r.id); r.game = host.game;
          host.invited = r.id; host.timer = Math.max(host.timer, cfg.waitTableS);     // espera a que llegue
          return goTo(r, spot, rr => startGame(rr, host));
        }
      }
      const options = [];
      for (const [k, w] of Object.entries(cfg.weights)) {
        if (k === "table") { if (TABLE.every(free)) options.push([k, w]); }
        else if (free(k)) options.push([k, w]);
      }
      if (!options.length) return goTo(r, "paseo_lounge", rr => startActivity(rr, "paseo_lounge"));
      let x = rnd() * options.reduce((s, o) => s + o[1], 0), choice = options[0][0];
      for (const [k, w] of options) { if ((x -= w) <= 0) { choice = k; break; } }
      if (choice === "table") {
        reserve("chess_a", r.id); r.game = rnd() < 0.5 ? "chess" : "uno";
        return goTo(r, "chess_a", rr => { rr.mode = "activity"; rr.activity = "table_wait"; rr.timer = cfg.waitTableS; rr.pose = rr.game; });
      }
      reserve(choice, r.id);
      goTo(r, choice, rr => startActivity(rr, choice));
    }
    function startActivity(r, act) {
      r.mode = "activity"; r.activity = act; r.pose = ACT_POSE[act] || "idle";
      r.timer = pick(...(cfg.durations[act] || [10, 20]));
    }
    function startGame(r, host) {
      // la pareja tiene que seguir esperando en la mesa; si se fue, el que llega pasa a esperar
      if (!(host.mode === "activity" && host.activity === "table_wait" && host.invited === r.id)) {
        r.mode = "activity"; r.activity = "table_wait"; r.timer = cfg.waitTableS; r.pose = r.game; return;
      }
      host.invited = null;
      const t = pick(...cfg.durations.game);
      for (const x of [r, host]) { x.mode = "activity"; x.activity = "game"; x.pose = r.game; x.timer = t; }
      r.partner = host.id; host.partner = r.id;
    }
    function leaveActivity(r) {
      release(r);
      if (r.partner) { const p = R[r.partner]; r.partner = null; if (p) { p.partner = null; if (p.mode === "activity") p.timer = Math.min(p.timer, 0.01); } }
      r.activity = null; r.game = null; r.invited = null;
    }
    function goToDesk(r) {
      leaveActivity(r);
      // si está caminando, primero termina el tramo actual (nunca corta camino por fuera del grafo)
      const startNode = r.mode === "walk" && r.path.length ? r.path[0] : r.node;
      const p = route(startNode, r.seat);
      r.path = r.mode === "walk" && r.path.length ? [r.path[0]].concat(p.slice(1)) : p.slice(1);
      r.target = r.seat; r.mode = "walk";
      r.then = rr => { rr.mode = "desk"; rr.pose = rr.real === "unknown" ? "unknown" : "desk"; };
      if (cfg.reducedMotion || !r.path.length) arrive(r, r.seat);
    }

    function setStates(states) {
      for (const [id, s] of Object.entries(states)) {
        const r = R[id]; if (!r) continue;
        const st = s === "working" || s === "idle" ? s : "unknown";
        if (st !== r.real) { r.real = st; r.realSince = time; }
      }
    }

    function update(r, dt) {
      const stable = time - r.realSince;
      // 1) trabajo real: vuelve de inmediato (interrumpe el ocio)
      if (r.real === "working") {
        if (r.mode === "desk") { r.pose = "desk"; r.wasWorking = true; }
        else if (r.target !== r.seat) goToDesk(r);
      } else if (r.real === "unknown") {
        if (r.mode === "desk") r.pose = stable >= cfg.unknownStableS ? "unknown" : r.pose;
        else if (stable >= cfg.unknownStableS && r.target !== r.seat) goToDesk(r);
      } else if (r.mode === "desk" && stable >= cfg.idleStableS) {
        // 2) inactivo estable: sale del box (festeja si venía de trabajar)
        const exit = NAV.nodes["walk_" + r.id] ? "walk_" + r.id : r.seat;   // festeja en la pasarela, a la vista
        const celebrate = r.wasWorking; r.wasWorking = false;
        goTo(r, exit, rr => {
          if (celebrate) { rr.mode = "activity"; rr.activity = "fist"; rr.pose = "fist"; rr.timer = cfg.fistS; }
          else chooseActivity(rr);
        });
        r.pose = "walk";
      } else if (r.mode === "desk") {
        r.pose = "desk_idle";               // inactivo, pero todavía dentro del margen de estabilidad
      }
      // 3) movimiento
      if (r.mode === "walk") {
        let step = cfg.speed * dt;
        while (step > 0 && r.path.length) {
          const [bx, by] = pt(r.path[0]), dx = bx - r.pos[0], dy = by - r.pos[1], d = Math.hypot(dx, dy);
          r.moving = [dx, dy];
          if (d <= step) { r.pos = [bx, by]; r.node = r.path.shift(); step -= d; }
          else { r.pos[0] += dx / d * step; r.pos[1] += dy / d * step; step = 0; }
        }
        if (!r.path.length && r.mode === "walk") arrive(r, r.target);
        if (r.mode === "walk") {
          const [mx, my] = r.moving;
          if (Math.abs(mx) >= Math.abs(my) * 0.8) { r.pose = "walk_side"; if (Math.abs(mx) > 0.3) r.dir = mx < 0 ? -1 : 1; }
          else r.pose = my > 0 ? "walk_down" : "walk_up";
        }
      }
      // 4) actividades con tiempo
      if (r.mode === "activity") {
        r.timer -= dt;
        if (r.activity === "game" || r.activity === "table_wait") r.dir = r.node === "chess_b" ? -1 : 1;
        if (r.timer <= 0) {
          const was = r.activity;
          leaveActivity(r);
          if (r.real === "idle") {
            if (was === "fist") chooseActivity(r);
            else chooseActivity(r);
          }
        }
      }
    }

    function step(dt) {
      time += dt;
      for (const id of agents) update(R[id], dt);
    }

    function size(r) { return r.pos[1] < cfg.floorSplitY ? "S" : "L"; }
    function snapshot() {
      return agents.map(id => {
        const r = R[id];
        return { agent: id, real: r.real, mode: r.mode, pose: r.pose, pos: r.pos.slice(), dir: r.dir, size: size(r),
                 activity: r.activity, node: r.node, target: r.target, partner: r.partner, game: r.game };
      });
    }
    function counts() {      // para el panel «Actividad Actual» (trabajando = real; el resto es decorativo)
      const c = { working: 0, playing: 0, break: 0, charging: 0, unknown: 0 };
      for (const id of agents) {
        const r = R[id];
        if (r.real === "unknown") c.unknown++;
        else if (r.real === "working") c.working++;
        else if (r.activity === "recarga") c.charging++;
        else if (["sofa", "game", "table_wait"].includes(r.activity)) c.playing++;
        else c.break++;
      }
      return c;
    }
    return { setStates, step, snapshot, counts, route, occupied, robots: R, cfg, get time() { return time; } };
  }

  return { createSim, DEFAULTS };
});
