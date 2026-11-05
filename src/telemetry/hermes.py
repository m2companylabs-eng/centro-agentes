"""
Hermes Agent — base SQLite `state.db` (solo lectura, modo ro).

  Ultron = Hermes LOCAL en la laptop  → telemetry_local()
  Vision = Hermes en la VPS           → ver vision_remote.py (por SSH, etapa D3)

Ubicación local: ~/.hermes/state.db (documentación de Hermes); si no existe, ~/AppData/Local/hermes/state.db
(ruta que encontró el diagnóstico E01).
Las columnas se descubren con PRAGMA table_info, porque la documentación no las detalla.
Semántica de tokens VERIFICADA con datos reales (05-11-2026): input_tokens NO incluye la caché
(suma de input_tokens ≈ 11 M < cache_read_tokens ≈ 440 M). Por eso:
  Entrada = input_tokens + cache_read_tokens + cache_write_tokens (si existen)
  cached  = cache_read_tokens · Salida = output_tokens · reasoning = reasoning_tokens (detalle)
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .common import ESTIMATED, MEASURED, home, parse_ts, result, state_from, tokens, unknown


REQUIRED = ("input_tokens", "output_tokens")              # obligatorias
OPTIONAL = ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens")   # si faltan, se avisa en notes


def local_db() -> Optional[str]:
    for p in (os.path.join(home(), ".hermes", "state.db"),
              os.path.join(home(), "AppData", "Local", "hermes", "state.db")):
        if os.path.isfile(p):
            return p
    return None


def read_db(path: str) -> Dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        if not cols:
            raise ValueError("la base no tiene tabla 'sessions'")
        missing = [c for c in REQUIRED if c not in cols]
        if missing:       # sin estas columnas no hay medición: nunca se informan ceros como medidos. Auditoría PRE-03
            raise ValueError(f"faltan columnas de tokens: {', '.join(missing)}")
        s = lambda c: f"COALESCE(SUM({c}),0)" if c in cols else "0"
        inp, out, cr, cw, rs, n = con.execute(
            f"SELECT {s('input_tokens')},{s('output_tokens')},{s('cache_read_tokens')},"
            f"{s('cache_write_tokens')},{s('reasoning_tokens')},COUNT(*) FROM sessions").fetchone()
        time_cols = [c for c in ("last_activity_at", "updated_at", "ended_at", "started_at") if c in cols]
        last = first = None
        model = None
        running = None
        if time_cols:
            order = f"COALESCE({','.join(time_cols)})" if len(time_cols) > 1 else time_cols[0]   # COALESCE exige 2+ argumentos
            row = con.execute(f"SELECT {order}{', model' if 'model' in cols else ''}"
                              f"{', ended_at' if 'ended_at' in cols else ''} FROM sessions "
                              f"ORDER BY {order} DESC LIMIT 1").fetchone()
            if row:
                last = parse_ts(row[0])
                model = row[1] if "model" in cols and row[1] else None
            if "started_at" in cols:
                first = parse_ts(con.execute("SELECT MIN(started_at) FROM sessions").fetchone()[0])
        return {"tokens": tokens(inp + cr + cw, out, cr, rs), "sessions": n, "last": last, "first": first,
                "model": model, "columns": sorted(cols), "running": running,
                "missing_optional": [c for c in OPTIONAL if c not in cols]}
    finally:
        con.close()


def telemetry_local() -> Dict[str, Any]:
    agent, name = "ultron", "Ultron"
    db = local_db()
    if not db:
        return unknown(agent, name, "~/.hermes/state.db", "no se encontró la base local de Hermes")
    try:
        d = read_db(db)
    except (sqlite3.Error, ValueError) as e:
        return unknown(agent, name, db, f"no se pudo leer la base de Hermes: {e}")
    state, reason, fr = state_from(d["last"])
    meas, extra = measurement_for(d["missing_optional"])
    return result(agent, name, state=state, state_reason=reason, freshness_s=fr, last_event=d["last"],
                  model_id=d["model"], tok=d["tokens"] if d["sessions"] else None, measurement=meas,
                  sessions=d["sessions"], since=d["first"], source=db,
                  notes=["Entrada = input + caché (semántica de Hermes verificada 05-11-2026)"] + extra)


def measurement_for(missing_optional) -> Tuple[str, List[str]]:
    """Sin columnas de caché, la Entrada queda incompleta → «estimado» (fuera del total general). Sin la de
    razonamiento no cambia nada sumado (es un detalle de la Salida). Reauditoría PRE-03."""
    missing = [str(c) for c in (missing_optional or [])]
    cache = [c for c in missing if c.startswith("cache_")]
    notes = []
    if cache:
        notes.append(f"faltan columnas de caché ({', '.join(cache)}): Entrada incompleta, se informa como estimado")
    if "reasoning_tokens" in missing:
        notes.append("falta la columna de razonamiento (detalle de la Salida)")
    return (ESTIMATED if cache else MEASURED), notes


def telemetry_vps() -> Dict[str, Any]:
    return unknown("vision", "Vision", "VPS (Hermes remoto)", "VPS no conectada todavía (etapa D3)")
