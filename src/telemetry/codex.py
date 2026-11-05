"""
Codex — lee ~/.codex/sessions/**/rollout-*.jsonl (solo lectura).

Tokens (semántica OpenAI): input_tokens YA incluye cached_input_tokens; output_tokens YA incluye
reasoning_output_tokens. Por eso: Entrada = input_tokens, Salida = output_tokens.

Dos formatos de registro de uso, según la versión:
  a) event_msg / payload.type == "token_count" con info.total_token_usage  → acumulado de la sesión:
     se toma el ÚLTIMO.
  b) token_usage_record con payload.usage → visto en la versión 0.166.0-alpha.6 del usuario.
     No está documentado si es acumulado o por turno. Se decide por los datos: si los totales de
     la sesión nunca bajan, se trata como acumulado (se toma el último); si bajan, son por turno y
     se suman. `notes` informa qué modo se usó para que se pueda verificar.
Estado: "working" si el último turno empezó y no terminó y hubo eventos hace < 30 s.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict

from .common import (ESTIMATED, FileCache, MEASURED, home, parse_ts, result, state_from, tokens, add_tokens, unknown)

AGENT, NAME = "codex", "Codex"
_cache = FileCache()


def sessions_dir() -> str:
    return os.path.join(home(), ".codex", "sessions")


def _usage(u: Dict[str, Any]) -> Dict[str, int]:
    return tokens(u.get("input_tokens", 0), u.get("output_tokens", 0),
                  u.get("cached_input_tokens", 0), u.get("reasoning_output_tokens", 0))


def parse_rollout(path: str) -> Dict[str, Any]:
    model = None
    first_ts = last_ts = None
    open_turn = False
    cumulative = None                    # formato a)
    records = []                         # formato b)
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
                first_ts = first_ts or ts
                last_ts = ts
            typ = rec.get("type")
            p = rec.get("payload") or {}
            if typ == "turn_context" and p.get("model"):
                model = p["model"]
            elif typ == "token_usage_record" and isinstance(p.get("usage"), dict):
                records.append(_usage(p["usage"]))
            elif typ == "event_msg":
                et = p.get("type")
                if et == "token_count":
                    info = p.get("info") or {}
                    if isinstance(info.get("total_token_usage"), dict):
                        cumulative = _usage(info["total_token_usage"])
                elif et in ("task_started", "task_start", "turn_started"):
                    open_turn = True
                elif et in ("task_complete", "turn_complete", "turn_aborted"):
                    open_turn = False
    mode = None
    if cumulative is not None:
        tok, mode = cumulative, "token_count acumulado"
    elif records:
        totals = [r["total"] for r in records]
        if len(records) == 1:
            tok, mode = records[0], "token_usage_record (un solo registro)"
        elif all(b >= a for a, b in zip(totals, totals[1:])):
            # Ambiguo: puede ser acumulado o turnos que crecen. Se toma el último, pero se marca como
            # ESTIMADO (no entra al total) hasta confirmar la semántica con datos reales. Auditoría PRE-02.
            tok, mode = records[-1], "token_usage_record ambiguo (no decrece): se toma el último, ESTIMADO"
        else:
            tok = tokens()
            for r in records:
                tok = add_tokens(tok, r)
            mode = "token_usage_record sumado por turno (decrece)"
    else:
        tok = None
    return {"model": model, "first": first_ts, "last": last_ts or os.path.getmtime(path),
            "open_turn": open_turn, "tokens": tok, "mode": mode}


def telemetry() -> Dict[str, Any]:
    base = sessions_dir()
    if not os.path.isdir(base):
        return unknown(AGENT, NAME, base, "no existe la carpeta de sesiones de Codex")
    files = glob.glob(os.path.join(base, "**", "rollout-*.jsonl"), recursive=True)
    if not files:
        return unknown(AGENT, NAME, base, "no hay sesiones de Codex")
    parsed = []
    for fp in files:
        try:
            parsed.append(_cache.get(fp, parse_rollout))
        except OSError:
            continue
    parsed.sort(key=lambda s: s["last"] or 0)
    latest = parsed[-1]
    total, n_tok, modes = tokens(), 0, set()
    for s in parsed:
        if s["tokens"] is not None:
            total = add_tokens(total, s["tokens"]); n_tok += 1; modes.add(s["mode"])
    state, reason, fr = state_from(latest["last"])
    if state == "working" and not latest["open_turn"]:
        state, reason = "idle", f"último turno terminado (evento hace {fr} s)"
    model = next((s["model"] for s in reversed(parsed) if s["model"]), None)
    since = min((s["first"] for s in parsed if s["first"]), default=None)
    notes = [f"modo de conteo: {m}" for m in sorted(modes)]
    if n_tok < len(parsed):
        notes.append(f"{len(parsed) - n_tok} sesiones sin registros de uso")
    ambiguous = any("ESTIMADO" in (m or "") for m in modes)
    if ambiguous:
        notes.append("hay sesiones con conteo ambiguo: el total de Codex queda como estimado y fuera del total general")
    return result(AGENT, NAME, state=state, state_reason=reason, freshness_s=fr, last_event=latest["last"],
                  model_id=model, tok=total if n_tok else None, measurement=ESTIMATED if ambiguous else MEASURED,
                  sessions=len(parsed), since=since, source=base, notes=notes)
