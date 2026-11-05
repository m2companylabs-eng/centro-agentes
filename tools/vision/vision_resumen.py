#!/usr/bin/env python3
"""
Resumen de telemetría de Vision (Hermes en la VPS) — SE INSTALA EN LA VPS. Solo lectura.

Uso en la VPS:   python3 ~/centro-agentes/vision_resumen.py
Salida: UNA línea JSON con números agregados. Nunca prompts, respuestas, títulos ni rutas de proyectos.

Solo usa la biblioteca estándar de Python 3 (sin instalar nada). Abre `state.db` en modo solo lectura
(`mode=ro`): no puede modificar nada de Hermes.

Ubicación de la base: $HERMES_STATE_DB si está definida; si no, ~/.hermes/state.db.
Misma lógica que src/telemetry/hermes.py (Ultron) para que Vision y Ultron se cuenten igual.
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

SCHEMA = 1


def parse_ts(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        v = float(v)
        return v / 1000.0 if v > 1e11 else v
    try:
        return datetime.fromisoformat(str(v).strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return parse_ts(float(v))
        except ValueError:
            return None


def main():
    db = os.environ.get("HERMES_STATE_DB") or os.path.join(os.path.expanduser("~"), ".hermes", "state.db")
    out = {"schema": SCHEMA, "generated_at": time.time(), "ok": False}
    if not os.path.isfile(db):
        out["error"] = "no se encontró state.db de Hermes"
        print(json.dumps(out)); return 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
            if not cols:
                raise ValueError("la base no tiene tabla 'sessions'")
            missing = [c for c in ("input_tokens", "output_tokens") if c not in cols]
            if missing:   # sin estas columnas no hay medición (nunca ceros como medidos)
                raise ValueError("faltan columnas de tokens: " + ", ".join(missing))
            s = lambda c: f"COALESCE(SUM({c}),0)" if c in cols else "0"
            inp, outp, cr, cw, rs, n = con.execute(
                f"SELECT {s('input_tokens')},{s('output_tokens')},{s('cache_read_tokens')},"
                f"{s('cache_write_tokens')},{s('reasoning_tokens')},COUNT(*) FROM sessions").fetchone()
            last = first = model = None
            tcols = [c for c in ("last_activity_at", "updated_at", "ended_at", "started_at") if c in cols]
            if tcols:
                order = f"COALESCE({','.join(tcols)})" if len(tcols) > 1 else tcols[0]   # COALESCE exige 2+ argumentos
                row = con.execute(f"SELECT {order}{', model' if 'model' in cols else ''} FROM sessions "
                                  f"ORDER BY {order} DESC LIMIT 1").fetchone()
                if row:
                    last = parse_ts(row[0])
                    model = row[1] if "model" in cols and row[1] else None
                if "started_at" in cols:
                    first = parse_ts(con.execute("SELECT MIN(started_at) FROM sessions").fetchone()[0])
        finally:
            con.close()
    except (sqlite3.Error, ValueError) as e:
        out["error"] = f"no se pudo leer state.db: {e if isinstance(e, ValueError) else type(e).__name__}"
        print(json.dumps(out)); return 0
    out.update({"ok": True, "sessions": int(n), "columns": sorted(cols),
                "missing_optional": [c for c in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens") if c not in cols], "model": model, "first": first, "last": last,
                "tokens": {"input": int(inp), "output": int(outp), "cache_read": int(cr), "cache_write": int(cw),
                           "reasoning": int(rs)}})
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
