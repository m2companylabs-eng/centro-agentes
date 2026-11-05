"""
Pruebas de la telemetría con datos de ejemplo (un HOME ficticio con el formato de cada herramienta).
Correr desde la raíz del proyecto:   python -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.telemetry.common import unknown  # noqa: E402

SECRET = "TEXTO-PRIVADO-DEL-PROMPT"          # nunca debe aparecer en la salida
TITLE = "TITULO-PRIVADO-DE-CONVERSACION"


def iso_ago(s):
    return datetime.fromtimestamp(time.time() - s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.environ["CENTRO_HOME"] = self.home
        for m in [m for m in list(sys.modules) if m.startswith("src.telemetry")]:
            del sys.modules[m]                          # cachés limpias en cada prueba

    def tearDown(self):
        os.environ.pop("CENTRO_HOME", None)
        self.tmp.cleanup()


class TestCodex(Base):
    def codex_file(self, name, rows):
        write_jsonl(os.path.join(self.home, ".codex", "sessions", "2026", "11", "05", name), rows)

    def test_token_count_acumulado_toma_el_ultimo(self):
        u = lambda i, c, o, r: {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o,
                                "reasoning_output_tokens": r, "total_tokens": i + o}
        self.codex_file("rollout-a.jsonl", [
            {"timestamp": iso_ago(600), "type": "session_meta", "payload": {"id": "s1", "cwd": "C:/secreto"}},
            {"timestamp": iso_ago(590), "type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
            {"timestamp": iso_ago(580), "type": "response_item", "payload": {"text": SECRET}},
            {"timestamp": iso_ago(570), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": u(1000, 400, 50, 10)}}},
            {"timestamp": iso_ago(560), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": u(3000, 1200, 150, 30)}}},
            {"timestamp": iso_ago(550), "type": "event_msg", "payload": {"type": "task_complete"}},
        ])
        from src.telemetry import codex
        r = codex.telemetry()
        self.assertEqual(r["tokens"], {"input": 3000, "output": 150, "cached": 1200, "reasoning": 30, "total": 3150})
        self.assertEqual(r["model_id"], "gpt-6.1-sol")
        self.assertEqual(r["state"], "idle")
        out = json.dumps(r)
        self.assertNotIn(SECRET, out); self.assertNotIn("C:/secreto", out)

    def test_token_usage_record_por_turno_se_suma(self):
        rec = lambda i, o: {"timestamp": iso_ago(100), "type": "token_usage_record",
                            "payload": {"usage": {"input_tokens": i, "cached_input_tokens": 0, "output_tokens": o, "total_tokens": i + o}}}
        self.codex_file("rollout-b.jsonl", [rec(500, 50), rec(300, 20), rec(400, 10)])   # baja → por turno
        from src.telemetry import codex
        r = codex.telemetry()
        self.assertEqual(r["tokens"]["total"], 1280)
        self.assertTrue(any("sumado por turno" in n for n in r["notes"]))

    def test_token_usage_record_creciente_se_toma_ultimo(self):
        rec = lambda i, o: {"timestamp": iso_ago(100), "type": "token_usage_record",
                            "payload": {"usage": {"input_tokens": i, "output_tokens": o}}}
        self.codex_file("rollout-c.jsonl", [rec(500, 50), rec(900, 70)])
        from src.telemetry import codex
        r = codex.telemetry()
        self.assertEqual(r["tokens"]["total"], 970)
        self.assertEqual(r["measurement"], "estimado")          # ambiguo: no entra como medido (PRE-02)
        from src.telemetry import aggregate
        self.assertIn("codex", aggregate.collect()["missing"])

    def test_turno_abierto_y_reciente_es_working(self):
        self.codex_file("rollout-d.jsonl", [
            {"timestamp": iso_ago(5), "type": "event_msg", "payload": {"type": "task_started"}}])
        from src.telemetry import codex
        r = codex.telemetry()
        self.assertEqual(r["state"], "working")
        self.assertIsNone(r["tokens"])                  # sin registros de uso: no se inventa
        self.assertEqual(r["measurement"], "no_disponible")

    def test_sin_carpeta_es_unknown(self):
        from src.telemetry import codex
        r = codex.telemetry()
        self.assertEqual(r["state"], "unknown"); self.assertIsNone(r["model_id"]); self.assertIsNone(r["tokens"])


class TestClaude(Base):
    def test_dedup_cache_y_synthetic(self):
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 50}
        line = lambda: {"timestamp": iso_ago(300), "type": "assistant", "requestId": "req_1",
                        "message": {"id": "msg_1", "model": "claude-opus-5-5", "usage": usage, "content": [{"text": SECRET}]}}
        write_jsonl(os.path.join(self.home, ".claude", "projects", "C--proyecto", "s.jsonl"), [
            {"timestamp": iso_ago(310), "type": "user", "message": {"role": "user", "content": SECRET}},
            line(), line(), line(),                                         # 3 bloques, 1 sola llamada
            {"timestamp": iso_ago(290), "type": "assistant", "message": {"id": "msg_2", "model": "<synthetic>", "usage": usage}},
        ])
        from src.telemetry import claude
        r = claude.telemetry()
        self.assertEqual(r["tokens"], {"input": 1110, "output": 50, "cached": 1000, "reasoning": 0, "total": 1160})
        self.assertEqual(r["model_id"], "claude-opus-5-5")
        self.assertIn("2 líneas repetidas descartadas", r["notes"][0])
        self.assertNotIn(SECRET, json.dumps(r))

    def test_status_de_proceso_manda(self):
        write_jsonl(os.path.join(self.home, ".claude", "projects", "p", "s.jsonl"), [
            {"timestamp": iso_ago(3600), "type": "assistant", "requestId": "r", "message": {"id": "m", "model": "x", "usage": {"input_tokens": 1, "output_tokens": 1}}}])
        os.makedirs(os.path.join(self.home, ".claude", "sessions"))
        with open(os.path.join(self.home, ".claude", "sessions", "123.json"), "w") as f:
            json.dump({"pid": os.getpid(), "status": "busy"}, f)       # proceso vivo (este)
        from src.telemetry import claude
        r = claude.telemetry()
        self.assertEqual(r["state"], "working"); self.assertIn("proceso vivo", r["state_reason"])

    def test_status_viejo_no_deja_trabajando_para_siempre(self):      # PRE-10
        write_jsonl(os.path.join(self.home, ".claude", "projects", "p", "s.jsonl"), [
            {"timestamp": iso_ago(3600), "type": "assistant", "requestId": "r", "message": {"id": "m", "model": "x", "usage": {"input_tokens": 1, "output_tokens": 1}}}])
        os.makedirs(os.path.join(self.home, ".claude", "sessions"))
        with open(os.path.join(self.home, ".claude", "sessions", "123.json"), "w") as f:
            json.dump({"status": "busy"}, f)                           # sin pid: no se puede verificar
        with open(os.path.join(self.home, ".claude", "sessions", "2.json"), "w") as f:
            json.dump({"pid": 2 ** 22 + 12345, "status": "busy"}, f)  # proceso que no existe
        from src.telemetry import claude
        r = claude.telemetry()
        self.assertEqual(r["state"], "idle"); self.assertIn("señal vieja", r["state_reason"])

    def test_proceso_muerto_no_cuenta(self):                            # PRE-10 (reauditoría)
        write_jsonl(os.path.join(self.home, ".claude", "projects", "p", "s.jsonl"), [
            {"timestamp": iso_ago(120), "type": "assistant", "requestId": "r", "message": {"id": "m", "model": "x", "usage": {"input_tokens": 1, "output_tokens": 1}}}])
        os.makedirs(os.path.join(self.home, ".claude", "sessions"))
        with open(os.path.join(self.home, ".claude", "sessions", "1.json"), "w") as f:
            json.dump({"pid": 2 ** 22 + 12345, "status": "busy"}, f)
        from src.telemetry import claude
        self.assertEqual(claude.telemetry()["state"], "idle")

    def test_llamadas_sin_identificadores_se_cuentan(self):            # PRE-04
        T200 = iso_ago(200)
        write_jsonl(os.path.join(self.home, ".claude", "projects", "p", "s.jsonl"), [
            {"timestamp": iso_ago(300), "type": "assistant", "message": {"model": "x", "usage": {"input_tokens": 10, "output_tokens": 5}}},
            {"timestamp": T200, "type": "assistant", "message": {"model": "x", "usage": {"input_tokens": 20, "output_tokens": 5}}},
            {"timestamp": T200, "type": "assistant", "message": {"model": "x", "usage": {"input_tokens": 20, "output_tokens": 5}}}])  # repetida (PRE-13)
        from src.telemetry import claude
        r = claude.telemetry()
        self.assertEqual(r["tokens"]["total"], 40)
        self.assertIn("2 llamadas contadas", r["notes"][0])

    def test_sin_modelo_no_inventa(self):
        from src.telemetry import claude
        self.assertIsNone(claude.telemetry()["model_id"])


class TestHermes(Base):
    def make_db(self, rel):
        p = os.path.join(self.home, *rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE sessions (id TEXT, model TEXT, started_at REAL, last_activity_at REAL, ended_at REAL,"
                    " input_tokens INT, output_tokens INT, cache_read_tokens INT, reasoning_tokens INT, title TEXT)")
        now = time.time()
        con.execute("INSERT INTO sessions VALUES ('a','minimax-m3',?,?,?,100,20,300,5,?)", (now - 900, now - 800, now - 790, TITLE))
        con.execute("INSERT INTO sessions VALUES ('b','qwen-4',?,?,NULL,50,10,0,0,?)", (now - 100, now - 10, TITLE))
        con.commit(); con.close()

    def test_hermes_local_es_ultron_y_vision_unknown(self):
        self.make_db([".hermes", "state.db"])
        from src.telemetry import hermes
        u = hermes.telemetry_local()
        self.assertEqual(u["agent_id"], "ultron")
        self.assertEqual(u["tokens"], {"input": 450, "output": 30, "cached": 300, "reasoning": 5, "total": 480})
        self.assertEqual(u["model_id"], "qwen-4")
        self.assertEqual(u["state"], "working")
        self.assertNotIn(TITLE, json.dumps(u))
        v = hermes.telemetry_vps()
        self.assertEqual((v["agent_id"], v["state"], v["tokens"]), ("vision", "unknown", None))

    def test_sin_columnas_de_tokens_no_da_ceros(self):                  # PRE-03
        p = os.path.join(self.home, ".hermes", "state.db"); os.makedirs(os.path.dirname(p))
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE sessions (id TEXT, model TEXT, started_at REAL, total_tokens INT)")
        con.execute("INSERT INTO sessions VALUES ('a','m',?,500)", (time.time(),)); con.commit(); con.close()
        from src.telemetry import hermes
        u = hermes.telemetry_local()
        self.assertEqual((u["state"], u["tokens"]), ("unknown", None))
        self.assertIn("faltan columnas", u["state_reason"])

    def test_sin_columnas_de_cache_es_estimado(self):                  # PRE-03 (reauditoría)
        p = os.path.join(self.home, ".hermes", "state.db"); os.makedirs(os.path.dirname(p))
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE sessions (id TEXT, started_at REAL, input_tokens INT, output_tokens INT)")
        con.execute("INSERT INTO sessions VALUES ('a',?,100,10)", (time.time() - 500,)); con.commit(); con.close()
        from src.telemetry import hermes, aggregate
        u = hermes.telemetry_local()
        self.assertEqual(u["measurement"], "estimado"); self.assertEqual(u["tokens"]["total"], 110)
        a = aggregate.collect()
        self.assertIn("ultron", a["missing"])
        self.assertIsNone(next(x for x in a["agents"] if x["agent_id"] == "ultron")["share_pct"])   # PRE-11

    def test_ruta_appdata(self):
        self.make_db(["AppData", "Local", "hermes", "state.db"])
        from src.telemetry import hermes
        self.assertEqual(hermes.telemetry_local()["coverage"]["sessions"], 2)


class TestGentle(Base):
    def test_uso_pi(self):
        write_jsonl(os.path.join(self.home, ".gentle-shell", "agent", "sessions", "--proj--", "x.jsonl"), [
            {"type": "model_change", "timestamp": iso_ago(500), "modelId": "minimax-m3"},
            {"type": "message", "timestamp": iso_ago(490), "message": {"role": "user", "content": SECRET}},
            {"type": "message", "timestamp": iso_ago(480), "message": {"role": "assistant", "model": "glm-5",
             "usage": {"input": 200, "output": 40, "cacheRead": 800, "cacheWrite": 0}}},
        ])
        from src.telemetry import gentle
        r = gentle.telemetry()
        self.assertEqual(r["tokens"], {"input": 1000, "output": 40, "cached": 800, "reasoning": 0, "total": 1040})
        self.assertEqual(r["model_id"], "glm-5")
        self.assertNotIn(SECRET, json.dumps(r))


class TestAntigravity(Base):
    def make_summaries(self, rows=1, status="CASCADE_RUN_STATUS_IDLE"):
        p = os.path.join(self.home, ".gemini", "antigravity", "conversation_summaries.db")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, step_count INT,"
                    " last_modified_time REAL, status TEXT, last_user_input_time REAL)")
        for i in range(rows):
            con.execute("INSERT INTO conversation_summaries VALUES (?,?,42,?,?,0)",
                        (f"c{i}", TITLE, time.time() - 300, status))
        con.commit(); con.close()
        return p

    def make_transcript(self, cid, rows, extra_files=()):
        """Crea brain/<cid>/.system_generated/logs/transcript.jsonl con el formato real."""
        d = os.path.join(self.home, ".gemini", "antigravity", "brain", cid,
                         ".system_generated", "logs")
        os.makedirs(d, exist_ok=True)
        write_jsonl(os.path.join(d, "transcript.jsonl"), rows)
        for rel in extra_files:                      # transcript_full.jsonl y chunks/ (mismo dato)
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            write_jsonl(p, rows)
        return os.path.join(d, "transcript.jsonl")

    def make_conv_db(self, cid, blobs):
        """conversations/<cid>.db con gen_metadata.data (protobuf crudo)."""
        p = os.path.join(self.home, ".gemini", "antigravity", "conversations", f"{cid}.db")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB, size INTEGER)")
        for i, b in enumerate(blobs):
            con.execute("INSERT INTO gen_metadata VALUES (?,?,?)", (i, b, len(b)))
        con.commit(); con.close()
        return p

    @staticmethod
    def _varint(n):
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            out.append(b | (0x80 if n else 0))
            if not n:
                return bytes(out)

    @staticmethod
    def proto_with_model(model):
        """Construye el protobuf real: root.1 = { .19 = nombre del modelo }."""
        def field(no, payload):
            tag = TestAntigravity._varint((no << 3) | 2)
            return tag + TestAntigravity._varint(len(payload)) + payload
        return field(1, field(19, model.encode("utf-8")))

    @staticmethod
    def step(i, cache, out, at=None, extra=None):
        r = {"step_index": i, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "created_at": at or iso_ago(600 - i * 10),
             "input_tokens": 1000, "cache_read_tokens": cache, "output_tokens": out}
        if extra:
            r.update(extra)
        return r

    def test_no_inventa_tokens_ni_titulo(self):
        p = os.path.join(self.home, ".gemini", "antigravity", "conversation_summaries.db")
        os.makedirs(os.path.dirname(p))
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, step_count INT,"
                    " last_modified_time REAL, status TEXT, last_user_input_time REAL)")
        con.execute("INSERT INTO conversation_summaries VALUES ('c1',?,42,?,'CASCADE_RUN_STATUS_RUNNING',0)", (TITLE, time.time() - 300))
        con.commit(); con.close()
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertIsNone(r["tokens"]); self.assertEqual(r["measurement"], "no_disponible")
        self.assertIsNone(r["model_id"])
        self.assertEqual(r["state"], "working")
        self.assertNotIn(TITLE, json.dumps(r))

    def test_tokens_desde_transcript_entrada_incluye_cache(self):
        """Entrada = input + caché; la salida ya trae el razonamiento (no se suma de nuevo)."""
        self.make_summaries()
        self.make_transcript("c1", [
            self.step(1, 5000, 100),
            self.step(2, 7000, 200),
            {"step_index": 3, "type": "GENERIC", "content": SECRET},      # sin uso: no cuenta
        ])
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        # 2 pasos: input 1000 + 1000, caché 5000 + 7000 → Entrada = 2000 + 12000
        self.assertEqual(r["tokens"], {"input": 2000 + 12000, "output": 300, "cached": 12000,
                                       "reasoning": 0, "total": 14300})
        self.assertEqual(r["measurement"], "medido")
        self.assertEqual(r["coverage"]["sessions"], 1)
        self.assertNotIn(SECRET, json.dumps(r))

    def test_no_duplica_transcript_full_ni_chunks(self):
        """transcript_full.jsonl y chunks/ traen el MISMO dato: sumarlos duplicaría."""
        self.make_summaries()
        rows = [self.step(1, 100, 10), self.step(2, 200, 20)]
        self.make_transcript("c1", rows, extra_files=("transcript_full.jsonl",
                                                      "chunks/transcript/00000000.jsonl",
                                                      "chunks/transcript_full/00000000.jsonl"))
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertEqual(r["tokens"]["total"], 2000 + 300 + 30)      # una sola vez
        self.assertEqual(r["tokens"]["cached"], 300)

    def test_subagente_no_repite_pasos_del_padre(self):
        """El padre NO repite los pasos del hijo: cada paso se cuenta una sola vez."""
        self.make_summaries(rows=2)
        padre = [self.step(1, 1000, 50), self.step(2, 2000, 60)]
        hijo = [self.step(1, 3000, 70)]
        self.make_transcript("padre", padre)
        self.make_transcript("hijo", hijo)          # su propio transcript, sin repetir los del padre
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertEqual(r["tokens"]["cached"], 1000 + 2000 + 3000)
        self.assertEqual(r["tokens"]["output"], 50 + 60 + 70)
        self.assertEqual(r["coverage"]["sessions"], 2)

    def test_modelo_desde_el_protobuf(self):
        self.make_summaries()
        self.make_transcript("c1", [self.step(1, 100, 10)])
        self.make_conv_db("c1", [self.proto_with_model("gemini-3.8-flash-n")])
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertEqual(r["model_id"], "gemini-3.8-flash-n")

    def test_modelo_toma_la_conversacion_mas_reciente_que_lo_tenga(self):
        """La más reciente que tenga modelo; si la última no lo tiene, se usa la anterior."""
        self.make_summaries(rows=3)
        self.make_transcript("vieja", [self.step(1, 10, 1, at=iso_ago(9000))])
        self.make_transcript("media", [self.step(1, 10, 1, at=iso_ago(5000))])
        self.make_transcript("nueva", [self.step(1, 10, 1, at=iso_ago(1000))])
        self.make_conv_db("vieja", [self.proto_with_model("modelo-viejo")])
        self.make_conv_db("media", [self.proto_with_model("modelo-medio")])
        self.make_conv_db("nueva", [])               # sin modelo
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertEqual(r["model_id"], "modelo-medio")

    def test_modelo_de_la_llamada_mas_reciente(self):
        self.make_summaries()
        self.make_transcript("c1", [self.step(1, 10, 1)])
        self.make_conv_db("c1", [self.proto_with_model("modelo-viejo"),
                                 self.proto_with_model("modelo-nuevo")])
        from src.telemetry import antigravity
        self.assertEqual(antigravity.telemetry()["model_id"], "modelo-nuevo")

    def test_proto_roto_no_rompe_los_tokens(self):
        """Si el protobuf no se puede leer: model_id None, pero los tokens siguen."""
        self.make_summaries()
        self.make_transcript("c1", [self.step(1, 100, 10)])
        self.make_conv_db("c1", [b"\xff\xff\xff\xff\xff\xff"])        # basura
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertIsNone(r["model_id"])
        self.assertEqual(r["tokens"]["total"], 1100 + 10)
        self.assertEqual(r["measurement"], "medido")

    def test_no_escribe_en_los_archivos_de_antigravity(self):
        """Solo lectura: los archivos de Antigravity no cambian (ni tamaño ni fecha)."""
        self.make_summaries()
        self.make_transcript("c1", [self.step(1, 100, 10)])
        db = self.make_conv_db("c1", [self.proto_with_model("m")])
        tr = os.path.join(self.home, ".gemini", "antigravity", "brain", "c1",
                          ".system_generated", "logs", "transcript.jsonl")
        before = [(os.stat(p).st_mtime_ns, os.path.getsize(p)) for p in (db, tr)]
        from src.telemetry import antigravity
        antigravity.telemetry()
        antigravity.telemetry()
        after = [(os.stat(p).st_mtime_ns, os.path.getsize(p)) for p in (db, tr)]
        self.assertEqual(before, after)

    def test_sin_transcripts_no_hay_tokens_pero_hay_estado(self):
        self.make_summaries(status="CASCADE_RUN_STATUS_RUNNING")
        from src.telemetry import antigravity
        r = antigravity.telemetry()
        self.assertIsNone(r["tokens"]); self.assertEqual(r["measurement"], "no_disponible")
        self.assertEqual(r["state"], "working")



class TestAggregate(Base):
    def isolate_vision(self):
        """Vision se lee por SSH de la VPS: sin aislarla, el test depende de la red y
        trae datos reales. Se reemplaza por una fuente vacía durante la prueba."""
        from src.telemetry import aggregate
        original = aggregate.SOURCES["vision"]
        aggregate.SOURCES["vision"] = lambda: unknown("vision", "Vision", "VPS", "aislado en la prueba")
        self.addCleanup(lambda: aggregate.SOURCES.__setitem__("vision", original))

    def test_totales_parciales_y_promedio(self):
        self.isolate_vision()
        write_jsonl(os.path.join(self.home, ".gentle-shell", "agent", "sessions", "p", "x.jsonl"), [
            {"type": "message", "timestamp": iso_ago(480), "message": {"role": "assistant", "usage": {"input": 100, "output": 100}}}])
        write_jsonl(os.path.join(self.home, ".claude", "projects", "p", "s.jsonl"), [
            {"timestamp": iso_ago(300), "type": "assistant", "requestId": "r", "message": {"id": "m", "model": "x", "usage": {"input_tokens": 300, "output_tokens": 100}}}])
        from src.telemetry import aggregate
        d = aggregate.collect()
        self.assertEqual(d["totals"]["total"], 600)
        self.assertEqual(d["average_per_agent"], 300)           # / 2 agentes con datos, no / 6
        self.assertTrue(d["partial"])
        self.assertEqual(sorted(d["missing"]), ["antigravity", "codex", "ultron", "vision"])
        self.assertEqual([a["agent_id"] for a in d["agents"]], aggregate.ORDER)
        claude = next(a for a in d["agents"] if a["agent_id"] == "claude")
        self.assertEqual(claude["share_pct"], 66.7)

    def test_un_adaptador_roto_no_rompe_el_resto(self):
        self.isolate_vision()
        from src.telemetry import aggregate
        def boom():
            raise RuntimeError("x")
        original = aggregate.SOURCES["codex"]
        aggregate.SOURCES["codex"] = boom
        self.addCleanup(lambda: aggregate.SOURCES.__setitem__("codex", original))
        d = aggregate.collect()
        codex = d["agents"][0]
        self.assertEqual((codex["agent_id"], codex["state"]), ("codex", "unknown"))
        self.assertIsNone(d["totals"])


if __name__ == "__main__":
    unittest.main()
