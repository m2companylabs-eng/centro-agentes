// Escenarios del motor de comportamiento (prototype/escena/sim.js).  Correr:  node tests/test_sim.js
// (también lo corre tests/test_sim_runner.py dentro de: python -m unittest discover -s tests)
const assert = require("assert");
const path = require("path");
const fs = require("fs");
const ROOT = path.join(__dirname, "..");
const { createSim } = require(path.join(ROOT, "prototype", "escena", "sim.js"));
const NAV = JSON.parse(fs.readFileSync(path.join(ROOT, "prototype", "escena", "assets", "nav.json"), "utf8"));
const AG = ["codex", "claude", "vision", "ultron", "gentle", "antigravity"];
const all = s => Object.fromEntries(AG.map(a => [a, s]));
const run = (sim, secs, check) => { for (let t = 0; t < secs; t += 0.1) { sim.step(0.1); if (check) check(sim); } };
let n = 0;
const test = (name, fn) => { fn(); n++; console.log("ok -", name); };

// ¿el robot está sobre el grafo? (en un nodo/lugar o sobre una arista, a menos de 1 px)
const pts = Object.assign({}, NAV.nodes, NAV.spots);
function onGraph(p) {
  for (const [a, b] of NAV.edges) {
    const [x0, y0] = pts[a], [x1, y1] = pts[b], dx = x1 - x0, dy = y1 - y0, L = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((p[0] - x0) * dx + (p[1] - y0) * dy) / L));
    if (Math.hypot(p[0] - (x0 + t * dx), p[1] - (y0 + t * dy)) < 1) return true;
  }
  return false;
}
const offGraphCheck = sim => { for (const r of sim.snapshot()) assert.ok(onGraph(r.pos), `${r.agent} fuera del grafo en ${r.pos}`); };

test("todos trabajando: quedan en su box", () => {
  const sim = createSim(NAV, AG); sim.setStates(all("working")); run(sim, 60);
  for (const r of sim.snapshot()) { assert.equal(r.mode, "desk"); assert.equal(r.pose, "desk"); }
});

test("inactivo: respeta el margen de estabilidad y después sale a una actividad", () => {
  const sim = createSim(NAV, AG); sim.setStates(all("working")); run(sim, 1);
  sim.setStates({ codex: "idle" }); run(sim, 10);
  assert.equal(sim.robots.codex.mode, "desk", "salió antes del margen");
  assert.equal(sim.robots.codex.pose, "desk_idle");
  run(sim, 120, offGraphCheck);
  assert.notEqual(sim.robots.codex.mode, "desk");
});

test("festeja con el puño al terminar de trabajar", () => {
  const sim = createSim(NAV, AG); sim.setStates(all("working")); run(sim, 1);
  sim.setStates({ claude: "idle" });
  let fist = false; run(sim, 40, s => { if (s.robots.claude.pose === "fist") fist = true; });
  assert.ok(fist);
});

test("vuelve el trabajo: interrumpe el ocio y regresa por el grafo", () => {
  const sim = createSim(NAV, AG, { seed: 7 }); sim.setStates(all("idle")); run(sim, 90, offGraphCheck);
  sim.setStates(all("working"));
  run(sim, 60, offGraphCheck);
  for (const r of sim.snapshot()) assert.equal(r.mode, "desk", `${r.agent} no volvió (${r.mode} ${r.activity})`);
  assert.equal(Object.keys(sim.occupied).length, 0, "quedaron reservas");
});

test("ocupación: nunca dos robots en el mismo lugar", () => {
  const sim = createSim(NAV, AG, { seed: 3 }); sim.setStates(all("idle"));
  run(sim, 900, s => {
    const at = {};
    for (const r of s.snapshot()) if (r.mode === "activity") {
      assert.ok(!at[r.node], `${r.agent} y ${at[r.node]} en ${r.node}`); at[r.node] = r.agent;
    }
  });
});

test("social: dos robots juegan juntos (mismo juego, en los dos sillones)", () => {
  let found = false;
  for (const seed of [1, 2, 3, 4, 5]) {
    const sim = createSim(NAV, AG, { seed }); sim.setStates(all("idle"));
    run(sim, 900, s => {
      const g = s.snapshot().filter(r => r.activity === "game");
      if (g.length === 2) { assert.equal(g[0].game, g[1].game); assert.deepEqual(g.map(r => r.node).sort(), ["chess_a", "chess_b"]); found = true; }
    });
    if (found) break;
  }
  assert.ok(found, "nunca jugaron juntos");
});

test("sin datos: vuelve al box y muestra el visor apagado", () => {
  const sim = createSim(NAV, AG, { seed: 9 }); sim.setStates(all("idle")); run(sim, 60);
  sim.setStates({ vision: "unknown" }); run(sim, 90);
  assert.equal(sim.robots.vision.mode, "desk"); assert.equal(sim.robots.vision.pose, "unknown");
});

test("sin parpadeo: alternar idle/working cada 5 s no lo saca del box", () => {
  const sim = createSim(NAV, AG); sim.setStates(all("working"));
  for (let i = 0; i < 20; i++) { sim.setStates({ ultron: i % 2 ? "working" : "idle" }); run(sim, 5, s => assert.equal(s.robots.ultron.mode, "desk")); }
});

test("movimiento reducido: llegan sin caminar", () => {
  const sim = createSim(NAV, AG, { reducedMotion: true, seed: 2 }); sim.setStates(all("idle")); run(sim, 20);
  for (const r of sim.snapshot()) assert.notEqual(r.mode, "walk");
});

test("panel de actividad: trabajando = estado real", () => {
  const sim = createSim(NAV, AG); sim.setStates({ codex: "working", claude: "working", vision: "unknown", ultron: "idle", gentle: "idle", antigravity: "working" });
  run(sim, 5);
  const c = sim.counts(); assert.equal(c.working, 3); assert.equal(c.unknown, 1);
  assert.equal(c.working + c.playing + c.break + c.charging + c.unknown, 6);
});

console.log(`\n${n} escenarios OK`);
