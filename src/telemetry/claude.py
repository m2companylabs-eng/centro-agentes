"""
Claude Code — lee ~/.claude/projects/**/*.jsonl (solo lectura).

Tokens (semántica Anthropic): input_tokens NO incluye la caché. Por eso:
  Entrada = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
  cached  = cache_read_input_tokens
  Salida  = output_tokens
Deduplicación: Claude Code escribe una línea por cada bloque de contenido de una misma respuesta,
repitiendo el mismo `usage`. Se cuenta una sola vez por (message.id, requestId).
Se ignoran los mensajes con modelo "<synthetic>" (no son llamadas reales).
Estado: ~/.claude/sessions/<pid>.json informa `status`; si no hay, se usa la frescura del último evento.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, Optional, Tuple

from .common import (FileCache, MEASURED, home, parse_ts, pid_alive, result, state_from, tokens, add_tokens, unknown)

AGENT, NAME = "claude", "Claude Code"
_cache = FileCache()
RUNNING_STATUS = {"busy", "running", "working", "active", "thinking", "streaming"}


def base_dir() -> str:
    return os.path.join(home(), ".claude")


def parse_session(path: str) -> Dict[str, Any]:
    seen, tok, model = set(), tokens(), None
    first = last = None
    dup = noid = 0
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
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            m = msg.get("model")
            if not usage or m == "<synthetic>":
                continue
            key = (msg.get("id"), rec.get("requestId"))
            if key == (None, None):
                # Sin identificadores (PRE-04/PRE-13): se cuenta, y se deduplica por momento + uso idénticos
                # (una línea repetida trae exactamente el mismo instante y los mismos números).
                key = ("sin-id", rec.get("timestamp"), json.dumps(usage, sort_keys=True))
                if key not in seen:
                    noid += 1
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            if m:
                model = m
            ci, cr = usage.get("cache_creation_input_tokens", 0) or 0, usage.get("cache_read_input_tokens", 0) or 0
            tok = add_tokens(tok, tokens((usage.get("input_tokens", 0) or 0) + ci + cr, usage.get("output_tokens", 0), cr, 0))
    return {"tokens": tok, "model": model, "first": first, "last": last or os.path.getmtime(path),
            "calls": len(seen), "dup": dup, "noid": noid}


def _running_from_process_files() -> Tuple[Optional[bool], bool]:
    """(en ejecución, verificado). Una señal «en ejecución» cuenta solo si su proceso sigue vivo; si el archivo
    no trae pid, vale sin verificar (common.state_from la limita a 10 min). Reauditoría PRE-10."""
    sdir = os.path.join(base_dir(), "sessions")
    if not os.path.isdir(sdir):
        return None, False
    flag: Optional[bool] = None
    for fp in glob.glob(os.path.join(sdir, "*.json")):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            st = str(data.get("status", "")).lower() if isinstance(data, dict) else ""
        except (OSError, ValueError):
            continue
        if st in RUNNING_STATUS:
            alive = pid_alive(data.get("pid"))
            if alive is True:
                return True, True
            if alive is None:
                flag = True                     # sin pid comprobable: señal sin verificar
            elif flag is None:
                flag = False                    # proceso muerto: la señal es vieja
        elif st and flag is None:
            flag = False
    return flag, False


def telemetry() -> Dict[str, Any]:
    pdir = os.path.join(base_dir(), "projects")
    if not os.path.isdir(pdir):
        return unknown(AGENT, NAME, pdir, "no existe la carpeta de proyectos de Claude Code")
    files = glob.glob(os.path.join(pdir, "**", "*.jsonl"), recursive=True)
    if not files:
        return unknown(AGENT, NAME, pdir, "no hay sesiones de Claude Code")
    parsed = []
    for fp in files:
        try:
            parsed.append(_cache.get(fp, parse_session))
        except OSError:
            continue
    parsed.sort(key=lambda s: s["last"] or 0)
    total, calls, dup, noid = tokens(), 0, 0, 0
    for s in parsed:
        total = add_tokens(total, s["tokens"]); calls += s["calls"]; dup += s["dup"]; noid += s.get("noid", 0)
    running, verified = _running_from_process_files()
    state, reason, fr = state_from(parsed[-1]["last"], running_flag=running, running_verified=verified)
    model = next((s["model"] for s in reversed(parsed) if s["model"]), None)
    since = min((s["first"] for s in parsed if s["first"]), default=None)
    notes = [f"{calls} llamadas contadas; {dup} líneas repetidas descartadas"]
    notes.append("solo sesiones de Claude Code en esta computadora (las de claude.ai o en la nube no dejan registros acá)")
    if noid:
        notes.append(f"{noid} llamadas sin identificadores (contadas; repetidas descartadas por momento y uso idénticos)")
    return result(AGENT, NAME, state=state, state_reason=reason, freshness_s=fr, last_event=parsed[-1]["last"],
                  model_id=model, tok=total if calls else None, measurement=MEASURED,
                  sessions=len(parsed), since=since, source=pdir, notes=notes)
