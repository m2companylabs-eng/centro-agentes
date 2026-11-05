"""
Formato común de telemetría (PRODUCT.md §6) y utilidades compartidas.

Reglas que este módulo hace cumplir:
- Nada inventado: si un dato no se puede leer, va None y `measurement` lo dice.
- Tokens Totales = Entrada + Salida.
  · Entrada = TODOS los tokens de entrada procesados, incluida la caché (así se puede comparar
    entre proveedores: OpenAI ya cuenta la caché dentro de input; Anthropic y Pi la cuentan aparte).
  · `cached` es la parte de la Entrada que vino de caché (detalle, no se suma de nuevo).
  · `reasoning` es la parte de la Salida usada en razonamiento (detalle, no se suma de nuevo).
- Estado: "working" | "idle" | "unknown", siempre con `state_reason` y frescura.
- Privacidad: nunca se devuelven prompts, textos, títulos de conversaciones ni rutas de proyectos.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

WORKING_WINDOW_S = 30          # actividad registrada hace menos de esto = trabajando
RUNNING_STALE_S = 600          # «en ejecución» sin ningún evento en 10 min = señal vieja (cierre inesperado). Auditoría PRE-10
MEASURED, ESTIMATED, UNAVAILABLE = "medido", "estimado", "no_disponible"


def home() -> str:
    return os.environ.get("CENTRO_HOME") or os.path.expanduser("~")


def iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def parse_ts(value: Any) -> Optional[float]:
    """Acepta epoch (s o ms) o texto ISO 8601. Devuelve epoch en segundos o None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    try:
        s = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        try:
            return parse_ts(float(value))
        except ValueError:
            return None


def tokens(inp: int = 0, out: int = 0, cached: int = 0, reasoning: int = 0) -> Dict[str, int]:
    inp, out, cached, reasoning = int(inp or 0), int(out or 0), int(cached or 0), int(reasoning or 0)
    return {"input": inp, "output": out, "cached": cached, "reasoning": reasoning, "total": inp + out}


def add_tokens(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    return tokens(a["input"] + b["input"], a["output"] + b["output"], a["cached"] + b["cached"], a["reasoning"] + b["reasoning"])


def pid_alive(pid: Any) -> Optional[bool]:
    """¿Sigue vivo el proceso? True/False, o None si no se puede saber. Solo consulta, no toca el proceso."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return True if k32.GetLastError() == 5 else False   # 5 = acceso denegado: existe
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return bool(ok) and code.value == 259             # STILL_ACTIVE
        except (AttributeError, OSError):
            return None
    try:
        os.kill(pid, 0)                                       # señal 0: solo comprueba que exista
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def state_from(last_event: Optional[float], running_flag: Optional[bool] = None, now: Optional[float] = None,
               running_verified: bool = False):
    """Devuelve (state, reason, freshness_s) con un criterio explícito y documentado.

    running_verified=True: la ejecución se comprobó con un proceso vivo → trabajando aunque no haya eventos
    recientes (tareas largas). Sin verificar, una señal «en ejecución» vale solo RUNNING_STALE_S."""
    now = now or time.time()
    if running_flag is True and running_verified:
        fr = max(0, int(now - last_event)) if last_event else None
        return "working", "la herramienta informa una ejecución en curso (proceso vivo)", fr
    if running_flag is True:
        fr = max(0, int(now - last_event)) if last_event else None
        if fr is None or fr <= RUNNING_STALE_S:
            return "working", "la herramienta informa una ejecución en curso", fr
        return "idle", (f"la herramienta dice «en ejecución», pero no hay eventos hace {fr} s "
                        f"(señal vieja, posible cierre inesperado)"), fr
    if last_event is None:
        return "unknown", "no hay eventos legibles", None
    fr = max(0, int(now - last_event))
    if fr < WORKING_WINDOW_S:
        return "working", f"último evento hace {fr} s (< {WORKING_WINDOW_S} s)", fr
    return "idle", f"último evento hace {fr} s", fr


def result(agent_id: str, name: str, *, state: str, state_reason: str, freshness_s: Optional[int],
           last_event: Optional[float], model_id: Optional[str], tok: Optional[Dict[str, int]],
           measurement: str, sessions: int, since: Optional[float], source: str,
           notes: Iterable[str] = ()) -> Dict[str, Any]:
    return {
        "agent_id": agent_id,
        "name": name,
        "state": state,
        "state_reason": state_reason,
        "last_event_at": iso(last_event),
        "freshness_s": freshness_s,
        "model_id": model_id,
        "tokens": tok,
        "measurement": measurement if tok is not None else UNAVAILABLE,
        "coverage": {"sessions": sessions, "since": iso(since), "source": source},
        "notes": list(notes),
    }


def unknown(agent_id: str, name: str, source: str, reason: str) -> Dict[str, Any]:
    return result(agent_id, name, state="unknown", state_reason=reason, freshness_s=None, last_event=None,
                  model_id=None, tok=None, measurement=UNAVAILABLE, sessions=0, since=None, source=source)


class FileCache:
    """Evita reprocesar archivos que no cambiaron (clave: ruta + mtime + tamaño)."""

    def __init__(self):
        self._d: Dict[str, Any] = {}

    def get(self, path: str, parse):
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
        hit = self._d.get(path)
        if hit and hit[0] == key:
            return hit[1]
        val = parse(path)
        self._d[path] = (key, val)
        return val
