// Prueba de punta a punta del HUD (V5): server.py + HOME ficticio + navegador (Playwright).
// Correr desde la raíz:  node tests/test_hud_e2e.js      (requiere Playwright; si no está, el runner la omite)
const assert = require("assert");
const fs = require("fs"), os = require("os"), path = require("path");
const { spawn } = require("child_process");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..");
const PORT = 8000 + Math.floor(Math.random() * 900);

// Texto malicioso en un dato de telemetría (auditoría URG-01): debe verse como texto, nunca ejecutarse.
const XSS = '<img src=x onerror="window.__xss=1">claude';

function fakeHome() {
  const h = fs.mkdtempSync(path.join(os.tmpdir(), "centro-"));
  const iso = s => new Date(Date.now() - s * 1000).toISOString();
  const cdir = path.join(h, ".codex", "sessions", "2026", "11", "05"); fs.mkdirSync(cdir, { recursive: true });
  const u = { input_tokens: 1700000000, cached_input_tokens: 1500000000, output_tokens: 29667599, reasoning_output_tokens: 3000000 };
  fs.writeFileSync(path.join(cdir, "rollout-a.jsonl"), [
    { timestamp: iso(4), type: "turn_context", payload: { model: "gpt-6.1-sol" } },
    { timestamp: iso(3), type: "event_msg", payload: { type: "task_started" } },
    { timestamp: iso(2), type: "event_msg", payload: { type: "token_count", info: { total_token_usage: u } } },
  ].map(r => JSON.stringify(r)).join("\n"));
  const pdir = path.join(h, ".claude", "projects", "p"); fs.mkdirSync(pdir, { recursive: true });
  fs.writeFileSync(path.join(pdir, "s.jsonl"), JSON.stringify({ timestamp: iso(900), type: "assistant", requestId: "r",
    message: { id: "m", model: XSS, usage: { input_tokens: 2000000, cache_read_input_tokens: 500000, output_tokens: 214990 } } }));
  return h;
}

(async () => {
  const home = fakeHome();
  const py = process.platform === "win32" ? "python" : "python3";
  const env = Object.assign({}, process.env, { CENTRO_HOME: home, CENTRO_PORT: String(PORT), CENTRO_VISION_CMD: "off" });
  const start = () => spawn(py, ["server.py"], { cwd: ROOT, env });
  let srv = start();
  await new Promise(r => setTimeout(r, 1500));
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1703, height: 1100 } });
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  let ok = 0; const check = (c, m) => { assert.ok(c, m); ok++; console.log("ok -", m); };
  try {
    await p.goto(`http://127.0.0.1:${PORT}/`); await p.waitForTimeout(2500);
    check(await p.evaluate(() => DATA.kind) === "api", "usa la API real");
    const total = 1700000000 + 29667599 + 2000000 + 500000 + 214990;
    const hud = await p.evaluate(() => [...document.querySelectorAll("[data-k]")].map(e => [e.dataset.k, e.textContent, e.title]));
    const t = Object.fromEntries(hud.map(([k, v, ti]) => [k, [v, ti]]));
    check(t.total[1] === total.toLocaleString("en-US"), `total exacto en el tooltip (${t.total[1]})`);
    check(/^1,73\d\.\d M$/.test(t.total[0]), `total grande abreviado en millones (${t.total[0]})`);
    check(t.avg[1] === Math.floor(total / 2).toLocaleString("en-US"), "promedio = total / agentes con datos (2)");
    check(await p.isVisible("#hudPartial"), "insignia PARCIAL visible (faltan agentes)");
    check(!(await p.isVisible(".demo")), "sin rótulo DEMO con datos reales");
    check((await p.textContent(".status")).trim() === "Operativo", "estado «Operativo»");
    const signs = await p.evaluate(() => Object.fromEntries([...document.querySelectorAll(".sign")].map(s => [s.dataset.agent, [s.querySelector(".tok").textContent, s.classList.contains("nodata")]])));
    check(signs.ultron[0] === "sin datos" && signs.ultron[1], "cartel de Ultron: «sin datos»");
    check(signs.claude[0] === "2,714,990 tokens", `cartel de Claude con su total (${signs.claude[0]})`);
    const pc = await p.evaluate(() => document.querySelector('.trow[data-agent="codex"] .pc').textContent);
    check(/^99\.\d%$/.test(pc), `participación de Codex (${pc})`);
    // tooltip y detalle
    await p.hover('.sign[data-agent="codex"]'); await p.waitForTimeout(200);
    const tipTxt = await p.textContent("#tip");
    check(await p.isVisible("#tip") && /Codex/.test(tipTxt) && /Trabajando/.test(tipTxt) && /gpt-6\.1-sol/.test(tipTxt), "tooltip de Codex: estado y modelo");
    await p.click('.sign[data-agent="codex"]'); await p.waitForTimeout(200);
    const det = await p.textContent("#detail");
    check(await p.isVisible("#detail") && /Entrada/.test(det) && /1,700,000,000/.test(det) && /caché 1,500,000,000/.test(det), "detalle de Codex con entrada y caché exactas");
    await p.waitForTimeout(2300);   // el detalle se refresca cada 2 s
    check(await p.evaluate(() => document.activeElement && document.activeElement.classList.contains("x")), "el foco sigue en «Cerrar» tras el refresco (PRE-08)");
    await p.keyboard.press("Escape"); await p.waitForTimeout(100);
    check(!(await p.isVisible("#detail")), "Esc cierra el detalle");
    await p.hover('.sign[data-agent="claude"]'); await p.waitForTimeout(200);
    await p.click('.sign[data-agent="claude"]'); await p.waitForTimeout(200);
    check(await p.evaluate(() => window.__xss === undefined), "un dato con HTML no se ejecuta (URG-01)");
    check((await p.textContent("#detail")).includes('<img src=x'), "el dato malicioso se muestra como texto");
    check(await p.evaluate(() => !document.querySelector("#detail img[src='x'], #tip img[src='x']")), "no se crea ningún elemento a partir del dato");
    await p.keyboard.press("Escape"); await p.waitForTimeout(100);
    await p.focus('.trow[data-agent="ultron"]'); await p.keyboard.press("Enter"); await p.waitForTimeout(150);
    check(/sin datos/i.test(await p.textContent("#detail")), "detalle por teclado (Enter en la fila de Ultron)");
    await p.keyboard.press("Escape");
    await p.screenshot({ path: path.join(os.tmpdir(), "hud_api.png") });
    // sonido (PRODUCT §5.4): apagado por defecto, cada sonido por separado, se recuerda
    check(await p.isVisible("#soundCtl") && (await p.getAttribute("#sndOn", "aria-pressed")) === "false", "sonido apagado por defecto");
    check(await p.evaluate(() => document.querySelector("#soundCtl fieldset").disabled), "con el sonido apagado, los sonidos individuales quedan deshabilitados");
    await p.click("#sndOn");
    const sc = await p.evaluate(() => sound.get());
    check(sc.on && sc.done && sc.offline && !sc.start && !sc.ambient, "al encender: «terminó» y «sin conexión» activos; «empezó» y ambiente no");
    const pick = v => p.selectOption('#liveCtl select[data-a="ultron"]', v);
    const last = () => p.evaluate(() => sound.log.filter(e => e.id === "ultron").slice(-1)[0] || null);
    await pick("working"); await p.waitForTimeout(300);
    let e = await last();
    check(e && e.kind === "start" && !e.played, "«empezó» desactivado: se detecta pero no suena");
    await p.waitForTimeout(5300); await pick("idle"); await p.waitForTimeout(300);
    e = await last();
    check(e && e.kind === "done" && e.played, "«terminó» activo: suena al pasar de trabajando a en reposo");
    await p.uncheck('[data-snd="done"]');
    await p.waitForTimeout(4200); await pick("working"); await p.waitForTimeout(5300); await pick("idle"); await p.waitForTimeout(300);
    e = await last();
    check(e && e.kind === "done" && !e.played, "«terminó» desactivado por separado: no suena");
    await pick("");
    await p.reload(); await p.waitForTimeout(2500);
    const sc2 = await p.evaluate(() => sound.get());
    check(sc2.on && !sc2.done && sc2.offline, "la configuración de sonido se recuerda al recargar");
    await p.click("#sndOn"); await p.click("#sndOn");   // gesto del usuario para habilitar el audio tras recargar
    // sin conexión
    srv.kill(); await p.waitForTimeout(6500);
    check((await p.textContent(".status")).trim() === "Sin conexión", "se corta el servidor → «Sin conexión»");
    check(await p.evaluate(() => sound.log.some(e => e.kind === "offline" && e.played)), "suena «sin conexión» al perder el servidor");
    check(await p.evaluate(() => Object.values(sim.robots).every(r => r.real === "unknown")), "sin conexión → todos los robots en «sin datos»");
    srv = start();
    await p.waitForFunction(() => document.querySelector(".status").textContent.trim() === "Operativo", null, { timeout: 30000 });
    check(true, "vuelve el servidor → se reconecta solo (URG-02)");
    // primer pedido con error → «Sin conexión» (no demo) y luego se recupera
    const p2 = await b.newPage({ viewport: { width: 1703, height: 1100 } });
    p2.on("pageerror", e => errs.push(e.message));
    let first = true;
    await p2.route("**/api/telemetry", route => { if (first) { first = false; route.fulfill({ status: 500, body: "{}" }); } else route.continue(); });
    await p2.goto(`http://127.0.0.1:${PORT}/`); await p2.waitForTimeout(800);
    check(await p2.evaluate(() => DATA.kind) === "offline", "error al abrir → «Sin conexión», no demo");
    check(await p2.evaluate(() => DATA.totals === null), "sin conexión inicial → sin números inventados");
    await p2.waitForFunction(() => DATA.kind === "api", null, { timeout: 15000 });
    check(true, "error al abrir → se recupera solo");
    // hosting sin API (404) → demo
    const p3 = await b.newPage({ viewport: { width: 1703, height: 1100 } });
    await p3.route("**/api/telemetry", route => route.fulfill({ status: 404, body: "" }));
    await p3.goto(`http://127.0.0.1:${PORT}/`); await p3.waitForTimeout(800);
    check(await p3.evaluate(() => DATA.kind) === "demo" && await p3.isVisible(".demo"), "sin API (404) → modo demo");
    // imagen faltante → aviso visible (PRE-07)
    const p4 = await b.newPage({ viewport: { width: 1703, height: 1100 } });
    await p4.route("**/assets/arch_front.png", route => route.fulfill({ status: 404, body: "" }));
    await p4.goto(`http://127.0.0.1:${PORT}/`); await p4.waitForTimeout(1200);
    check(/arch_front\.png/.test(await p4.textContent("#sceneError").catch(() => "")), "imagen faltante → aviso con el nombre (PRE-07)");
    // reducir movimiento (PRE-09)
    const ctx5 = await b.newContext({ viewport: { width: 1703, height: 1100 }, reducedMotion: "reduce" });
    const p5 = await ctx5.newPage();
    await p5.goto(`http://127.0.0.1:${PORT}/`); await p5.waitForTimeout(1500);
    const snap = () => p5.evaluate(() => document.getElementById("scene").toDataURL());
    const a1 = await snap(); await p5.waitForTimeout(1200); const a2 = await snap();
    check(await p5.evaluate(() => REDUCED && !state.typing), "reducir movimiento → efectos desactivados");
    check(a1 === a2, "reducir movimiento → la escena queda quieta");
    await ctx5.close();
    await p.setViewportSize({ width: 1280, height: 800 }); await p.waitForTimeout(300);
    check(await p.evaluate(() => document.documentElement.scrollWidth) <= 1280, "sin scroll horizontal a 1280 px");
    check(errs.length === 0, "sin errores de consola " + errs.join(" | "));
    console.log(`\n${ok} comprobaciones OK`);
  } finally { await b.close(); try { srv.kill(); } catch (e) {} }
})().catch(e => { console.error(e.message); process.exit(1); });
