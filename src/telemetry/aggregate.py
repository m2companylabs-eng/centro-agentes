"""
Agregado para el dashboard (solo lectura). Un adaptador que falla nunca tira abajo a los demás:
ese agente queda "unknown".

Totales: solo sobre agentes con tokens medidos. Si falta alguno, `partial` = True y se lista en
`missing` (el HUD debe decir «total parcial»). Promedio por agente = total / agentes con datos
(no / 6: dividir por agentes sin datos inventaría un promedio).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from . import antigravity, claude, codex, gentle, hermes, vision_remote
from .common import MEASURED, add_tokens, iso, tokens, unknown

ORDER = ["codex", "claude", "vision", "ultron", "gentle", "antigravity"]
NAMES = {"codex": "Codex", "claude": "Claude Code", "vision": "Vision", "ultron": "Ultron",
         "gentle": "Gentle Shell", "antigravity": "Antigravity"}
SOURCES = {
    "codex": codex.telemetry,
    "claude": claude.telemetry,
    "vision": vision_remote.telemetry,      # Hermes en la VPS, por SSH (D3)
    "ultron": hermes.telemetry_local,
    "gentle": gentle.telemetry,
    "antigravity": antigravity.telemetry,
}


def collect() -> Dict[str, Any]:
    agents: List[Dict[str, Any]] = []
    for aid in ORDER:
        try:
            agents.append(SOURCES[aid]())
        except Exception as e:                         # aislamiento: un error no rompe el resto
            agents.append(unknown(aid, NAMES[aid], "?", f"error del adaptador: {type(e).__name__}"))
    total, with_data, missing = tokens(), [], []
    for a in agents:
        if a["tokens"] is not None and a["measurement"] == MEASURED:
            total = add_tokens(total, a["tokens"]); with_data.append(a["agent_id"])
        else:
            missing.append(a["agent_id"])
    for a in agents:
        t = a["tokens"]
        # solo sobre el MISMO total: un agente estimado no entra al total, así que no tiene porcentaje (PRE-11)
        a["share_pct"] = (round(100 * t["total"] / total["total"], 1)
                          if t and total["total"] and a["agent_id"] in with_data else None)
    count = lambda s: sum(1 for a in agents if a["state"] == s)
    return {
        "generated_at": iso(time.time()),
        "totals": total if with_data else None,
        "average_per_agent": (total["total"] // len(with_data)) if with_data else None,
        "agents_with_data": with_data,
        "missing": missing,
        "partial": bool(missing),
        "activity": {"working": count("working"), "idle": count("idle"), "unknown": count("unknown")},
        "agents": agents,
        "notes": ["Entrada incluye tokens en caché; Total = Entrada + Salida",
                  "deduplicación Gentle Shell ↔ Codex/Claude pendiente (D2)"],
    }
