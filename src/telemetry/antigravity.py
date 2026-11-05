"""
Antigravity — telemetría real desde dos fuentes locales (solo lectura, modo ro).

ESTADO (como antes): ~/.gemini/antigravity/conversation_summaries.db
  Da el estado (trabajando/reposo) y la lista de conversaciones. No tiene ni una
  columna de modelo o de tokens: se verificó en sus 21 columnas. Por eso el estado
  se sigue leyendo de acá, y los tokens de otro lado.

TOKENS: ~/.gemini/antigravity/brain/<conversation_id>/.system_generated/logs/transcript.jsonl
  Una línea JSON por paso de agente. Las que traen uso traen tres enteros:
    input_tokens, cache_read_tokens, output_tokens.
  Semántica (verificada con datos reales de esta laptop el 05-11-2026, 6064 pasos):
    · La caché va APARTE de la entrada, no dentro: `cache_read_tokens` supera a
      `input_tokens` en 5909 de 6064 pasos y es igual en 0. Si la entrada incluyera
      la caché, nunca podría superarla. Por eso, igual que Anthropic:
          Entrada = input_tokens + cache_read_tokens
          cached  = cache_read_tokens
    · La salida YA INCLUYE el razonamiento (en el protobuf de abajo se ve que
      output = razonamiento + salida visible, con 0 excepciones). Por eso:
          Salida   = output_tokens   (no se le suma nada más)
          reasoning = 0             (esta fuente no lo separa; ver la nota)
  Se lee SOLO transcript.jsonl. transcript_full.jsonl y chunks/ contienen el mismo
  dato: sumarlos duplicaría. Cada paso se cuenta una sola vez.

MODELO: ~/.gemini/antigravity/conversations/<conversation_id>.db → tabla gen_metadata
  La columna `data` es un protobuf sin esquema publicado: el modelo está en el
  campo .1.19 de la llamada. Se decodifica con un lector mínimo de varint/wire-format
  (biblioteca estándar, sin dependencias). De la llamada más reciente de la
  conversación más reciente que lo tenga. El nombre se muestra tal cual (son IDs
  internos de Antigravity, no nombres públicos de API): no se traduce ni se inventa.

DEDUPLICACIÓN: cada paso vive en una sola conversación. Los subagentes de Antigravity
tienen conversación propia (parent_conversation_id / nesting_depth) y el padre NO
repite sus pasos: se comprobó en los 13 pares padre-hijo de esta laptop (0 pasos
del hijo presentes en el padre, 0 pares de conversaciones con solapamiento).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Dict, Optional, Tuple

from .common import (FileCache, MEASURED, add_tokens, home, parse_ts, result, state_from,
                     tokens, unknown)

AGENT, NAME = "antigravity", "Antigravity"
_cache = FileCache()

# El nombre del modelo sale del disco como un identificador corto. Si el campo del
# protobuf cambiara de posición o de tipo, este filtro corta el valor dudoso en vez
# de mostrar basura en el HUD.
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{1,63}")


def base_dir() -> str:
    return os.path.join(home(), ".gemini", "antigravity")


def db_path() -> str:
    return os.path.join(base_dir(), "conversation_summaries.db")


# --------------------------------------------------------------------------- estado


def _summaries() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(datos de las conversaciones, error). Solo lectura."""
    db = db_path()
    try:
        con = sqlite3.connect(f"file:{db.replace(os.sep, '/')}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(conversation_summaries)")}
            sel = [c for c in ("last_modified_time", "status", "step_count") if c in cols]
            if "last_modified_time" not in sel:
                return None, "la tabla conversation_summaries no tiene last_modified_time"
            rows = con.execute(f"SELECT {','.join(sel)} FROM conversation_summaries "
                               f"ORDER BY last_modified_time DESC").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return None, f"no se pudo leer la base de Antigravity: {e}"
    if not rows:
        return None, "no hay conversaciones de Antigravity"
    out = []
    for r in rows:
        d = dict(zip(sel, r))
        d["last"] = parse_ts(d.get("last_modified_time"))
        out.append(d)
    return {"rows": out}, None


# --------------------------------------------------------------------------- tokens


def _transcript_path(conversation_id: str) -> Optional[str]:
    p = os.path.join(base_dir(), "brain", conversation_id, ".system_generated", "logs",
                     "transcript.jsonl")
    return p if os.path.isfile(p) else None


def parse_transcript(path: str) -> Dict[str, Any]:
    """Suma el uso de un transcript. Solo lee enteros; no guarda ni devuelve texto."""
    total, steps, first, last = tokens(), 0, None, None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or '"input_tokens"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or "input_tokens" not in rec:
                continue
            inp = rec.get("input_tokens") or 0
            cached = rec.get("cache_read_tokens") or 0
            out = rec.get("output_tokens") or 0
            if not (isinstance(inp, int) and isinstance(cached, int) and isinstance(out, int)):
                continue
            steps += 1
            # Entrada = input + caché (aquí la caché viene aparte, no dentro del input).
            total = add_tokens(total, tokens(inp + cached, out, cached, 0))
            ts = parse_ts(rec.get("created_at"))
            if ts:
                first = first if first is not None else ts
                last = ts if last is None else max(last, ts)
    return {"tokens": total, "steps": steps, "first": first, "last": last}


def _conversations() -> Dict[str, Dict[str, Any]]:
    """Cada conversación con su transcript: id → uso, pasos y último momento."""
    brain = os.path.join(base_dir(), "brain")
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(brain):
        return out
    for entry in sorted(os.listdir(brain)):
        path = _transcript_path(entry)
        if not path:
            continue
        try:
            parsed = _cache.get(path, parse_transcript)
        except OSError:
            continue
        out[entry] = parsed
    return out


# --------------------------------------------------------------------------- modelo


def _read_varint(buf: bytes, i: int) -> Tuple[int, int]:
    shift = 0
    val = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint truncado")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError("varint demasiado largo")


def _iter_fields(buf: bytes):
    """Recorre los campos protobuf (wire format) sin conocer el esquema."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        fn, wt = key >> 3, key & 7
        if fn == 0:
            return
        if wt == 0:
            _, i = _read_varint(buf, i)
            yield fn, 0, b""
        elif wt == 1:
            yield fn, 1, buf[i:i + 8]
            i += 8
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            yield fn, 2, buf[i:i + ln]
            i += ln
        elif wt == 5:
            yield fn, 5, buf[i:i + 4]
            i += 4
        else:
            return


def model_from_blob(blob: Optional[bytes]) -> Optional[str]:
    """Nombre del modelo en el campo .1.19 del protobuf de gen_metadata.

    Estructura real (verificada): root.1 = envoltorio; root.1.19 = nombre del modelo.
    Si el esquema cambia, devuelve None en vez de romper nada.
    """
    if not blob:
        return None
    try:
        for fn, wt, val in _iter_fields(blob):
            if fn != 1 or wt != 2:
                continue
            for f2, w2, v2 in _iter_fields(val):
                if f2 != 19 or w2 != 2:
                    continue
                s = v2.decode("utf-8", "replace").strip()
                if s and _MODEL_RE.fullmatch(s):
                    return s
    except (ValueError, IndexError, UnicodeDecodeError):
        return None
    return None


def _parse_models(path: str) -> Optional[str]:
    """Modelo de la llamada más reciente de esta conversación."""
    try:
        con = sqlite3.connect(f"file:{path.replace(os.sep, '/')}?mode=ro", uri=True)
        try:
            cur = con.execute("SELECT data FROM gen_metadata ORDER BY idx DESC")
            for (blob,) in cur:
                m = model_from_blob(blob)
                if m:
                    return m
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return None


def _model_from_conversation(conversation_id: str) -> Optional[str]:
    path = os.path.join(base_dir(), "conversations", f"{conversation_id}.db")
    if not os.path.isfile(path):
        return None
    try:
        return _cache.get(path, _parse_models)
    except OSError:
        return None


def latest_model(convs: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Modelo de la conversación más reciente que lo tenga (por su último uso)."""
    ordered = sorted(convs.items(), key=lambda kv: kv[1].get("last") or 0, reverse=True)
    for cid, _ in ordered:
        m = _model_from_conversation(cid)
        if m:
            return m
    return None


# --------------------------------------------------------------------------- públicas


def telemetry() -> Dict[str, Any]:
    db = db_path()
    if not os.path.isfile(db):
        return unknown(AGENT, NAME, db, "no se encontró la base de Antigravity")

    summaries, err = _summaries()
    if summaries is None:
        return unknown(AGENT, NAME, db, err or "no se pudo leer la base de Antigravity")

    rows = summaries["rows"]
    r0 = rows[0]
    running = "RUNNING" in str(r0.get("status", "")).upper()
    last_event = r0.get("last")
    state, reason, fr = state_from(last_event, running_flag=True if running else None)

    convs = _conversations()
    total, steps, sessions, since = tokens(), 0, 0, None
    for parsed in convs.values():
        if not parsed["steps"]:
            continue
        total = add_tokens(total, parsed["tokens"])
        steps += parsed["steps"]
        sessions += 1
        if parsed["first"] is not None:
            since = parsed["first"] if since is None else min(since, parsed["first"])

    model_id = latest_model(convs)

    notes = [
        "los tokens salen de brain/<id>/.../transcript.jsonl (una vez por conversación): "
        "transcript_full.jsonl y chunks/ traen el mismo dato y no se suman",
        "Entrada = input_tokens + cache_read_tokens (esta fuente da la caché aparte, no dentro del input)",
        "Salida = output_tokens, que ya incluye el razonamiento: no se le suma nada más",
        "el modelo sale del protobuf de conversations/<id>.db (campo .1.19 de la llamada más "
        "reciente de la conversación más reciente que lo tenga); es el ID interno de Antigravity, sin traducir",
        "cada paso se cuenta una sola vez: los subagentes tienen conversación propia y el "
        "padre no repite sus pasos",
    ]
    if not sessions:
        return result(AGENT, NAME, state=state, state_reason=reason, freshness_s=fr,
                      last_event=last_event, model_id=model_id, tok=None,
                      measurement=MEASURED, sessions=0, since=None, source=db,
                      notes=["no hay transcripts de Antigravity con uso registrado"] + notes)

    notes.insert(0, f"{steps} pasos de agente con uso en {sessions} conversaciones")
    if model_id is None:
        notes.append("no se encontró un modelo legible en las conversaciones de esta laptop")
    return result(AGENT, NAME, state=state, state_reason=reason, freshness_s=fr,
                  last_event=last_event, model_id=model_id, tok=total, measurement=MEASURED,
                  sessions=sessions, since=since, source=db, notes=notes)
