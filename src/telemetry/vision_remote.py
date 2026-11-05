"""
Vision — Hermes en la VPS (etapa D3). Solo lectura, sin puertos abiertos.

Cómo funciona: la laptop ejecuta por SSH, en la VPS, el script `tools/vision/vision_resumen.py`
(instalado allá en ~/centro-agentes/). El script devuelve UNA línea JSON con números agregados y acá se
convierte al formato común. Instrucciones de instalación: INSTRUCCIONES_D3_VISION.md.

Configuración (sin secretos en el repositorio):
  - `config.local.json` en la raíz del proyecto (está en .gitignore):
        {"vision": {"ssh_host": "vision-vps", "remote_python": "python3",
                    "remote_script": "~/centro-agentes/vision_resumen.py"}}
    `ssh_host` es un alias de ~/.ssh/config con autenticación por CLAVE (nunca contraseña).
  - o la variable CENTRO_VISION_CMD con el comando completo (se usa en las pruebas).
Seguridad: BatchMode=yes (si la clave no funciona, falla en vez de pedir contraseña), timeout de 12 s,
se leen como máximo 64 KB mientras llegan (stderr se descarta), resultado cacheado 30 s.
Si algo falla → «unknown», nunca números inventados.

Modo de fondo (server.py lo activa con BACKGROUND = True): la consulta a la VPS corre en un hilo aparte y el
tablero recibe al instante el último valor conocido; así una VPS lenta no frena a los demás agentes (PRE-01).
CENTRO_VISION_CMD=off desactiva Vision (lo usan las pruebas de punta a punta).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Union

from .common import result, state_from, tokens, unknown
from .hermes import measurement_for

AGENT, NAME = "vision", "Vision"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_S, TIMEOUT_S, MAX_BYTES = 30, 12, 64 * 1024
BACKGROUND = False
_lock = threading.Lock()
_cache: Dict[str, Any] = {"t": 0.0, "val": None, "running": False}


def command() -> Optional[Union[str, List[str]]]:
    env = (os.environ.get("CENTRO_VISION_CMD") or "").strip()
    if env:
        if env.lower() == "off":
            return None
        # En Windows el texto va tal cual a CreateProcess, que entiende las comillas de las rutas con espacios;
        # partirlo con shlex dejaba las comillas pegadas al ejecutable. Auditoría PRE-06.
        return env if os.name == "nt" else shlex.split(env)
    cfg_path = os.path.join(ROOT, "config.local.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            v = (json.load(f) or {}).get("vision") or {}
    except (OSError, ValueError):
        return None
    host = v.get("ssh_host")
    if not host:
        return None
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host,
            f"{v.get('remote_python', 'python3')} {v.get('remote_script', '~/centro-agentes/vision_resumen.py')}"]


def _run(cmd: Union[str, List[str]]) -> bytes:
    """Ejecuta el comando leyendo como máximo MAX_BYTES mientras llegan (nunca todo a memoria). Auditoría PRE-05."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    timed_out = []
    timer = threading.Timer(TIMEOUT_S, lambda: (timed_out.append(1), p.kill()))
    timer.start()
    try:
        out = p.stdout.read(MAX_BYTES + 1)
        if len(out) > MAX_BYTES:
            p.kill(); p.wait()
            raise RuntimeError("la VPS devolvió demasiados datos")
        p.wait()
    finally:
        timer.cancel()
        p.stdout.close()
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, TIMEOUT_S)
    if p.returncode != 0:
        raise RuntimeError(f"el comando remoto terminó con código {p.returncode}")
    return out


def _fetch(cmd: Union[str, List[str]]) -> Dict[str, Any]:
    out = _run(cmd).decode("utf-8", "replace").strip().splitlines()
    if not out:
        raise RuntimeError("la VPS no devolvió datos")
    d = json.loads(out[-1])
    if not isinstance(d, dict) or d.get("schema") != 1:
        raise RuntimeError("versión de resumen desconocida")
    return d


def _int(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise ValueError(f"campo «{name}» inválido")
    return int(v)


def _convert(d: Dict[str, Any], now: float) -> Dict[str, Any]:
    """Valida estructura y tipos antes de usar nada (reauditoría PRE-03 y PRE-12)."""
    if not d.get("ok"):
        return unknown(AGENT, NAME, "VPS", f"la VPS respondió sin datos: {str(d.get('error', 'sin detalle'))[:120]}")
    t = d.get("tokens")
    if not isinstance(t, dict) or "input" not in t or "output" not in t:
        raise ValueError("faltan los tokens de entrada o salida")
    cr, cw = _int(t.get("cache_read", 0), "cache_read"), _int(t.get("cache_write", 0), "cache_write")
    tok = tokens(_int(t["input"], "input") + cr + cw, _int(t["output"], "output"), cr, _int(t.get("reasoning", 0), "reasoning"))
    last = d.get("last") if isinstance(d.get("last"), (int, float)) else None
    first = d.get("first") if isinstance(d.get("first"), (int, float)) else None
    state, reason, fr = state_from(last, now=now)
    n = _int(d.get("sessions", 0), "sessions")
    missing = d.get("missing_optional") if isinstance(d.get("missing_optional"), list) else []
    meas, extra = measurement_for(missing)
    model = d.get("model")
    return result(AGENT, NAME, state=state, state_reason=reason, freshness_s=fr, last_event=last,
                  model_id=str(model)[:80] if model else None, tok=tok if n else None, measurement=meas, sessions=n,
                  since=first, source="VPS (Hermes remoto)",
                  notes=["datos de la VPS por SSH, cada 30 s",
                         "Entrada = input + caché (semántica de Hermes verificada 05-11-2026)"] + extra)


def _refresh(cmd: Union[str, List[str]]) -> Dict[str, Any]:
    val = None
    try:
        val = _convert(_fetch(cmd), time.time())
    except Exception as e:  # noqa: BLE001  cualquier respuesta rara → «sin datos», nunca un hilo colgado (PRE-12)
        val = unknown(AGENT, NAME, "VPS", f"no se pudo consultar la VPS ({type(e).__name__})")
    finally:
        with _lock:
            _cache.update(t=time.time(), val=val, running=False)
    return val


def _aged(val: Dict[str, Any], age: float) -> Dict[str, Any]:
    """Último valor conocido mientras se actualiza: se marca su antigüedad (PRE-14)."""
    v = dict(val)
    v["notes"] = list(val.get("notes") or []) + [f"dato de hace {int(age)} s (actualizando)"]
    v["data_age_s"] = int(age)
    return v


def telemetry() -> Dict[str, Any]:
    cmd = command()
    if not cmd:
        return unknown(AGENT, NAME, "VPS", "VPS no configurada (ver INSTRUCCIONES_D3_VISION.md)")
    with _lock:
        if _cache["val"] is not None and time.time() - _cache["t"] < CACHE_S:
            return _cache["val"]
        if BACKGROUND:
            if not _cache["running"]:
                _cache["running"] = True
                threading.Thread(target=_refresh, args=(cmd,), daemon=True).start()
            if _cache["val"] is None:
                return unknown(AGENT, NAME, "VPS", "consultando la VPS…")
            return _aged(_cache["val"], time.time() - _cache["t"])
    return _refresh(cmd)
