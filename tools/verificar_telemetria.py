"""
Verificación de la telemetría con los datos REALES de esta computadora (solo lectura).

Uso (desde la raíz del proyecto, en la laptop):
    python tools/verificar_telemetria.py

Muestra, por agente: estado y su motivo, modelo, tokens (entrada/salida/caché), cobertura y notas.
Además imprime datos de diagnóstico para cerrar dudas abiertas de D1:
  - columnas de la tabla `sessions` de Hermes (para confirmar la semántica de la caché);
  - modo de conteo de Codex (acumulado o por turno);
  - líneas repetidas que se descartaron en Claude Code.
NO imprime prompts, respuestas, títulos ni rutas de proyectos. Copiá la salida en PROGRESS.md §3.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.telemetry import aggregate, hermes  # noqa: E402


def fmt(n):
    return "—" if n is None else f"{n:,}"


def main():
    d = aggregate.collect()
    print(f"Generado: {d['generated_at']}\n")
    for a in d["agents"]:
        t = a["tokens"]
        print(f"■ {a['name']} ({a['agent_id']})")
        print(f"  estado: {a['state']} — {a['state_reason']}")
        print(f"  modelo: {a['model_id'] or '— (sin dato)'}")
        if t:
            print(f"  tokens: total {fmt(t['total'])} = entrada {fmt(t['input'])} + salida {fmt(t['output'])}"
                  f"   (de la entrada, caché {fmt(t['cached'])}; de la salida, razonamiento {fmt(t['reasoning'])})")
        else:
            print(f"  tokens: sin datos ({a['measurement']})")
        c = a["coverage"]
        print(f"  cobertura: {c['sessions']} sesiones desde {c['since'] or '—'}")
        for n in a["notes"]:
            print(f"  nota: {n}")
        print()
    tot = d["totals"]
    print("TOTAL" + (" PARCIAL" if d["partial"] else "") + f": {fmt(tot['total'] if tot else None)}"
          f"   agentes con datos: {', '.join(d['agents_with_data']) or '—'}   sin datos: {', '.join(d['missing']) or '—'}")
    db = hermes.local_db()
    if db:
        try:
            print("\nDiagnóstico Hermes (Ultron): columnas de 'sessions':", ", ".join(hermes.read_db(db)["columns"]))
        except Exception as e:  # noqa: BLE001
            print("\nDiagnóstico Hermes: no se pudo leer:", e)
    if "--json" in sys.argv:
        print(json.dumps(d, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
