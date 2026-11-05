"""
Gentle Shell — sesiones del agente Pi en ~/.gentle-shell/agent/sessions/**/*.jsonl (solo lectura).

Tokens (semántica de pi-ai, que normaliza todos los proveedores): `input` NO incluye la caché.
  Entrada = input + cacheRead + cacheWrite · cached = cacheRead · Salida = output
Modelo: `message.model` del último mensaje del asistente; si no está, el último `model_change`.
Deduplicación con Codex/Claude: PENDIENTE (etapa D2). Mientras tanto se avisa en `notes`.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict

from .common import (FileCache, MEASURED, home, parse_ts, result, state_from, tokens, add_tokens, unknown)

AGENT, NAME = "gentle", "Gentle Shell"
_cache = FileCache()


def sessions_dir() -> str:
    return os.path.join(home(), ".gentle-shell", "agent", "sessions")


def parse_session(path: str) -> Dict[str, Any]:
    tok, model, calls = tokens(), None, 0
    first = last = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = parse_ts(rec.get("timestamp"))
            if ts:
                first = first or ts
                last = ts
            if rec.get("type") == "model_change" and rec.get("modelId"):
                model = rec["modelId"]
            elif rec.get("type") == "message":
                msg = rec.get("message") or {}
                u = msg.get("usage")
                if msg.get("role") == "assistant" and isinstance(u, dict):
                    cr, cw = u.get("cacheRead", 0) or 0, u.get("cacheWrite", 0) or 0
                    tok = add_tokens(tok, tokens((u.get("input", 0) or 0) + cr + cw, u.get("output", 0), cr, 0))
                    calls += 1
                    if msg.get("model"):
                        model = msg["model"]
    return {"tokens": tok, "model": model, "calls": calls, "first": first, "last": last or os.path.getmtime(path)}


def telemetry() -> Dict[str, Any]:
    base = sessions_dir()
    if not os.path.isdir(base):
        return unknown(AGENT, NAME, base, "no existe la carpeta de sesiones de Gentle Shell")
    files = glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True)
    if not files:
        return unknown(AGENT, NAME, base, "no hay sesiones de Gentle Shell")
    parsed = []
    for fp in files:
        try:
            parsed.append(_cache.get(fp, parse_session))
        except OSError:
            continue
    parsed.sort(key=lambda s: s["last"] or 0)
    total, calls = tokens(), 0
    for s in parsed:
        total = add_tokens(total, s["tokens"]); calls += s["calls"]
    state, reason, fr = state_from(parsed[-1]["last"])
    model = next((s["model"] for s in reversed(parsed) if s["model"]), None)
    since = min((s["first"] for s in parsed if s["first"]), default=None)
    return result(AGENT, NAME, state=state, state_reason=reason, freshness_s=fr, last_event=parsed[-1]["last"],
                  model_id=model, tok=total if calls else None, measurement=MEASURED,
                  sessions=len(parsed), since=since, source=base,
                  notes=[f"{calls} llamadas", "deduplicación con Codex/Claude pendiente (D2)"])
