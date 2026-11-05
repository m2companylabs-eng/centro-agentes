"""
Pruebas de Vision en la VPS (D3) sin VPS: el script de la VPS se corre localmente contra una base de prueba,
y el módulo de la laptop lo llama con CENTRO_VISION_CMD en lugar de SSH.
Correr: python -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
SCRIPT = os.path.join(ROOT, "tools", "vision", "vision_resumen.py")
TITLE = "TITULO-PRIVADO"


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (id TEXT, model TEXT, started_at REAL, last_activity_at REAL, ended_at REAL,"
                " input_tokens INT, output_tokens INT, cache_read_tokens INT, reasoning_tokens INT, title TEXT)")
    now = time.time()
    con.execute("INSERT INTO sessions VALUES ('a','glm-5',?,?,?,1000,200,3000,50,?)", (now - 900, now - 800, now - 790, TITLE))
    con.execute("INSERT INTO sessions VALUES ('b','kimi-k3',?,?,NULL,500,100,0,0,?)", (now - 100, now - 5, TITLE))
    con.commit(); con.close()


class TestVision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "state.db")
        make_db(self.db)
        os.environ["HERMES_STATE_DB"] = self.db
        for m in [m for m in list(sys.modules) if m.startswith("src.telemetry")]:
            del sys.modules[m]

    def tearDown(self):
        for k in ("HERMES_STATE_DB", "CENTRO_VISION_CMD"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def cmd(self, extra=""):
        return f'"{sys.executable}" "{SCRIPT}"{extra}'

    def test_script_de_la_vps_devuelve_solo_numeros(self):
        out = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=dict(os.environ)).stdout
        d = json.loads(out)
        self.assertTrue(d["ok"]); self.assertEqual(d["sessions"], 2)
        self.assertEqual(d["tokens"], {"input": 1500, "output": 300, "cache_read": 3000, "cache_write": 0, "reasoning": 50})
        self.assertEqual(d["model"], "kimi-k3")
        self.assertNotIn(TITLE, out)

    def test_laptop_convierte_igual_que_ultron(self):
        os.environ["CENTRO_VISION_CMD"] = self.cmd()
        from src.telemetry import vision_remote, hermes
        v = vision_remote.telemetry()
        self.assertEqual(v["agent_id"], "vision")
        self.assertEqual(v["tokens"], hermes.read_db(self.db)["tokens"])     # misma regla que Ultron
        self.assertEqual(v["state"], "working")                                # actividad hace 5 s
        self.assertEqual(v["model_id"], "kimi-k3")
        self.assertNotIn(TITLE, json.dumps(v))

    def test_sin_configuracion_es_unknown(self):
        from src.telemetry import vision_remote
        vision_remote.ROOT = self.tmp.name                                    # sin config.local.json
        v = vision_remote.telemetry()
        self.assertEqual((v["state"], v["tokens"]), ("unknown", None))
        self.assertIn("no configurada", v["state_reason"])

    def test_falla_del_comando_es_unknown(self):
        os.environ["CENTRO_VISION_CMD"] = f'"{sys.executable}" -c "import sys; sys.exit(255)"'
        from src.telemetry import vision_remote
        v = vision_remote.telemetry()
        self.assertEqual((v["state"], v["tokens"]), ("unknown", None))

    def test_salida_invalida_es_unknown(self):
        os.environ["CENTRO_VISION_CMD"] = f'"{sys.executable}" -c "print(\'hola\')"'
        from src.telemetry import vision_remote
        self.assertEqual(vision_remote.telemetry()["state"], "unknown")

    def test_vps_sin_base_es_unknown(self):
        os.environ["HERMES_STATE_DB"] = os.path.join(self.tmp.name, "no-existe.db")
        os.environ["CENTRO_VISION_CMD"] = self.cmd()
        from src.telemetry import vision_remote
        v = vision_remote.telemetry()
        self.assertEqual(v["state"], "unknown"); self.assertIn("sin datos", v["state_reason"])

    def test_off_desactiva(self):
        os.environ["CENTRO_VISION_CMD"] = "off"
        from src.telemetry import vision_remote
        vision_remote.ROOT = self.tmp.name
        self.assertEqual(vision_remote.telemetry()["state"], "unknown")

    def test_salida_enorme_se_corta(self):                              # PRE-05
        os.environ["CENTRO_VISION_CMD"] = f'"{sys.executable}" -c "import sys; sys.stdout.write(\'x\' * 10000000)"'
        from src.telemetry import vision_remote
        v = vision_remote.telemetry()
        self.assertEqual(v["state"], "unknown"); self.assertIn("RuntimeError", v["state_reason"])

    def test_timeout(self):
        os.environ["CENTRO_VISION_CMD"] = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        from src.telemetry import vision_remote
        vision_remote.TIMEOUT_S = 1
        t0 = time.time(); v = vision_remote.telemetry()
        self.assertLess(time.time() - t0, 5)
        self.assertIn("TimeoutExpired", v["state_reason"])

    def test_modo_de_fondo_no_bloquea(self):                            # PRE-01
        os.environ["CENTRO_VISION_CMD"] = self.cmd() if os.name == "nt" else f'sh -c "sleep 1.5; \\"{sys.executable}\\" \\"{SCRIPT}\\""'
        from src.telemetry import vision_remote
        vision_remote.BACKGROUND = True
        t0 = time.time(); v = vision_remote.telemetry()
        self.assertLess(time.time() - t0, 0.5)
        self.assertIn("consultando", v["state_reason"])
        for _ in range(60):
            time.sleep(0.1)
            v = vision_remote.telemetry()
            if v["tokens"]:
                break
        self.assertEqual(v["tokens"]["total"], 1500 + 3000 + 300)

    def fake_out(self, payload):
        p = os.path.join(self.tmp.name, "out.json")
        with open(p, "w") as f:
            f.write(json.dumps(payload))
        return f'"{sys.executable}" -c "import sys; sys.stdout.write(open(sys.argv[1]).read())" "{p}"'

    def test_respuesta_rara_no_cuelga(self):                           # PRE-12
        os.environ["CENTRO_VISION_CMD"] = self.fake_out({"schema": 1, "ok": True, "sessions": 3, "tokens": [1, 2]})
        from src.telemetry import vision_remote
        vision_remote.BACKGROUND = True
        vision_remote.telemetry()
        for _ in range(50):
            time.sleep(0.1)
            if not vision_remote._cache["running"]:
                break
        self.assertFalse(vision_remote._cache["running"])
        self.assertEqual(vision_remote.telemetry()["state"], "unknown")

    def test_faltan_tokens_no_da_ceros(self):                          # PRE-03
        os.environ["CENTRO_VISION_CMD"] = self.fake_out({"schema": 1, "ok": True, "sessions": 3, "tokens": {"output": 5}})
        from src.telemetry import vision_remote
        v = vision_remote.telemetry()
        self.assertEqual((v["state"], v["tokens"]), ("unknown", None))

    def test_sin_cache_en_la_vps_es_estimado(self):
        os.environ["CENTRO_VISION_CMD"] = self.fake_out({"schema": 1, "ok": True, "sessions": 3, "last": time.time(),
            "tokens": {"input": 10, "output": 5}, "missing_optional": ["cache_read_tokens", "cache_write_tokens"]})
        from src.telemetry import vision_remote
        self.assertEqual(vision_remote.telemetry()["measurement"], "estimado")

    def test_dato_viejo_se_marca(self):                                # PRE-14
        os.environ["CENTRO_VISION_CMD"] = self.cmd()
        from src.telemetry import vision_remote
        vision_remote.BACKGROUND = True
        vision_remote._refresh(vision_remote.command())
        vision_remote._cache["t"] -= 40                                     # vencido: se actualiza en segundo plano
        v = vision_remote.telemetry()
        self.assertTrue(any("actualizando" in n for n in v["notes"])); self.assertGreaterEqual(v["data_age_s"], 40)

    def test_config_local_arma_ssh_seguro(self):
        from src.telemetry import vision_remote
        vision_remote.ROOT = self.tmp.name
        with open(os.path.join(self.tmp.name, "config.local.json"), "w") as f:
            json.dump({"vision": {"ssh_host": "vision-vps"}}, f)
        c = vision_remote.command()
        self.assertEqual(c[0], "ssh"); self.assertIn("BatchMode=yes", c); self.assertIn("vision-vps", c)


if __name__ == "__main__":
    unittest.main()
