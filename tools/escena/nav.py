"""
Mapa de navegación de la escena (preparación de V4). Fuente única de verdad de:
  - zonas CAMINABLES (polígonos de piso),
  - HUELLAS BLOQUEADAS (la base de cada mueble u objeto: no se pueden atravesar),
  - NODOS y ARISTAS del grafo de rutas,
  - PUNTOS DE ACTIVIDAD (asiento de cada box, aceite, sofá, ajedrez, recarga, paseo).
Coordenadas en px de la referencia (1672×942). La posición de un robot es la de sus PIES.

Uso:
    python tools/escena/nav.py <carpeta_assets>          → valida y escribe nav.json / nav.js / nav_mask.png
    python tools/escena/nav.py <carpeta_assets> --check  → solo valida (código de salida 1 si falla)

Validación (PRODUCT.md §4, «nunca atraviesa paredes ni muebles»):
  1. cada arista, muestreada cada 2 px, queda dentro de lo caminable y fuera de toda huella bloqueada;
  2. desde el asiento de cada agente se llega a todos los puntos de actividad, y se puede volver.
"""
import json
import os
import sys
from collections import deque

import numpy as np
import cv2

W, H = 1672, 942

# ---------------------------------------------------------------- piso caminable (polígonos)
WALKABLE = {
    # interior de cada box, detrás del escritorio (donde está la silla)
    **{f"box_{a}": [(x0, 384), (x1, 384), (x1, 400), (x0, 400)] for a, (x0, x1) in {
        "codex": (150, 258), "claude": (300, 492), "vision": (580, 708), "ultron": (782, 900),
        "gentle": (1004, 1110), "antigravity": (1260, 1420)}.items()},
    "pasarela": [(40, 398), (1432, 398), (1432, 454), (40, 454)],                 # piso superior, frente a los boxes
    "recarga": [(1420, 400), (1540, 400), (1540, 452), (1420, 452)],
    "pasillo": [(696, 446), (784, 446), (915, 942), (566, 942)],                  # entre las dos líneas de luces
    "oil": [(508, 652), (640, 640), (632, 724), (508, 724)],                      # piso del surtidor, a su derecha
    "lounge": [(832, 646), (1330, 668), (1330, 690), (1298, 694), (1298, 842), (872, 842)],
}

# ---------------------------------------------------------------- huellas bloqueadas (base de cada objeto)
BLOCKED = {
    **{f"desk_{a}": (x0, 402, x1, 414) for a, (x0, x1) in {
        "codex": (80, 242), "claude": (315, 482), "vision": (540, 692), "ultron": (800, 962),
        "gentle": (1015, 1182), "antigravity": (1240, 1398)}.items()},
    "plant_u1": (24, 400, 76, 428), "plant_u2": (262, 400, 300, 428), "plant_u3": (494, 400, 532, 428),
    "plant_u4": (964, 400, 1002, 428), "plant_u5": (1188, 400, 1226, 428),
    "bollard_ul": (656, 446, 690, 476), "bollard_ur": (786, 446, 812, 476),
    "oil_machine": (398, 650, 506, 722), "bollard_l": (500, 762, 560, 804), "plant_c1": (438, 878, 512, 942),
    "sofa": (864, 598, 1160, 656), "popcorn": (1104, 608, 1216, 650), "tv_stand": (1204, 606, 1442, 664),
    "armchair_a": (910, 726, 1014, 806), "armchair_b": (1178, 720, 1268, 796), "table": (998, 772, 1252, 834),
    "puff": (1334, 678, 1442, 738), "bollard_r": (946, 800, 996, 846), "plant_c2": (974, 878, 1050, 942),
    "bench": (1084, 848, 1302, 904),
}
MARGIN = 6   # holgura alrededor de cada huella (px de referencia)

# ---------------------------------------------------------------- puntos de actividad (pies del robot)
SPOTS = {
    **{f"seat_{a}": (x, 392) for a, x in {"codex": 185, "claude": 368, "vision": 607, "ultron": 848,
                                          "gentle": 1063, "antigravity": 1298}.items()},
    "oil": (560, 700),          # tomar aceite junto al surtidor
    "sofa": (1000, 640),        # recostado en el sofá con el joystick (pose especial: sobre el mueble)
    "chess_a": (962, 766),      # sillón izquierdo (pose especial)
    "chess_b": (1222, 762),     # sillón derecho (pose especial)
    "recarga": (1500, 440),     # cápsula (pose especial)
    "paseo_holo": (735, 845),   # paseo: holograma del cerebro
    "paseo_lounge": (1110, 712),
}
# los puntos especiales están SOBRE un mueble: se llega por su punto de acceso y se «salta» a la pose
SPECIAL = {"sofa": "acc_sofa", "chess_a": "acc_chess_a", "chess_b": "acc_chess_b", "recarga": "acc_recarga",
           **{f"seat_{a}": f"exit_{a}" for a in ("codex", "claude", "vision", "ultron", "gentle", "antigravity")}}

# ---------------------------------------------------------------- nodos del grafo
NODES = {
    # salida lateral de cada box (entre el escritorio y la planta) y su punto en la pasarela
    "exit_codex": (252, 392), "walk_codex": (252, 436),
    "exit_claude": (307, 392), "walk_claude": (307, 436),   # sale por la izquierda: a la derecha la planta no deja paso
    "exit_vision": (700, 392), "walk_vision": (700, 436),
    "exit_ultron": (790, 392), "walk_ultron": (790, 436),
    "exit_gentle": (1008, 392), "walk_gentle": (1008, 436),
    "exit_antigravity": (1414, 392), "walk_antigravity": (1414, 436),
    "pasillo_0": (740, 440), "pasillo_1": (740, 500), "pasillo_2": (722, 600), "pasillo_3": (700, 690),
    "pasillo_4": (700, 790), "pasillo_5": (720, 880),
    "acc_oil": (620, 690),
    "lounge_in": (860, 690), "acc_sofa": (1000, 680), "lounge_mid": (1110, 712), "lounge_e": (1290, 712),
    "acc_chess_a": (892, 772), "acc_chess_b": (1286, 760),
    "acc_recarga": (1450, 440),
}
EDGES = [
    *[(f"exit_{a}", f"walk_{a}") for a in ("codex", "claude", "vision", "ultron", "gentle", "antigravity")],
    ("walk_codex", "walk_claude"), ("walk_claude", "walk_vision"), ("walk_vision", "pasillo_0"),
    ("pasillo_0", "walk_ultron"), ("walk_ultron", "walk_gentle"), ("walk_gentle", "walk_antigravity"),
    ("walk_antigravity", "acc_recarga"),
    ("pasillo_0", "pasillo_1"), ("pasillo_1", "pasillo_2"), ("pasillo_2", "pasillo_3"),
    ("pasillo_3", "pasillo_4"), ("pasillo_4", "pasillo_5"),
    ("pasillo_3", "acc_oil"), ("acc_oil", "oil"),
    ("pasillo_3", "lounge_in"), ("lounge_in", "acc_sofa"), ("acc_sofa", "lounge_mid"), ("lounge_mid", "lounge_e"),
    ("lounge_in", "acc_chess_a"), ("lounge_e", "acc_chess_b"),
    ("pasillo_4", "paseo_holo"), ("pasillo_5", "paseo_holo"), ("lounge_mid", "paseo_lounge"),
]


def masks():
    walk = np.zeros((H, W), np.uint8)
    for poly in WALKABLE.values():
        cv2.fillPoly(walk, [np.array(poly, np.int32)], 255)
    block = np.zeros((H, W), np.uint8)
    for (x0, y0, x1, y1) in BLOCKED.values():
        block[max(0, y0 - MARGIN):y1 + MARGIN, max(0, x0 - MARGIN):x1 + MARGIN] = 255
    return walk, block


def all_points():
    pts = dict(NODES)
    pts.update({k: v for k, v in SPOTS.items()})
    return pts


def validate():
    walk, block = masks()
    pts = all_points()
    errors = []
    graph = {k: set() for k in pts}
    edges = list(EDGES) + [(spot, acc) for spot, acc in SPECIAL.items()]
    for a, b in edges:
        if a not in pts or b not in pts:
            errors.append(f"arista con nodo desconocido: {a} – {b}"); continue
        graph[a].add(b); graph[b].add(a)
        if (a, b) in SPECIAL.items() or (b, a) in SPECIAL.items():
            continue                                   # salto a una pose sobre un mueble: no se camina
        (x0, y0), (x1, y1) = pts[a], pts[b]
        n = max(2, int(max(abs(x1 - x0), abs(y1 - y0)) / 2))
        for t in np.linspace(0, 1, n):
            x, y = int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t))
            if not walk[y, x]:
                errors.append(f"{a} → {b} sale del piso caminable en ({x},{y})"); break
            if block[y, x]:
                hit = next(k for k, (bx0, by0, bx1, by1) in BLOCKED.items()
                           if bx0 - MARGIN <= x < bx1 + MARGIN and by0 - MARGIN <= y < by1 + MARGIN)
                errors.append(f"{a} → {b} atraviesa «{hit}» en ({x},{y})"); break
    # alcance: de cada asiento a cada actividad y vuelta
    def reach(src):
        seen, q = {src}, deque([src])
        while q:
            u = q.popleft()
            for v in graph[u]:
                if v not in seen:
                    seen.add(v); q.append(v)
        return seen
    targets = [s for s in SPOTS if not s.startswith("seat_")]
    for seat in [s for s in SPOTS if s.startswith("seat_")]:
        r = reach(seat)
        for t in targets:
            if t not in r:
                errors.append(f"desde {seat} no se llega a {t}")
    return errors, edges


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    errors, edges = validate()
    if errors:
        print("NAVEGACIÓN CON ERRORES:"); [print("  -", e) for e in errors]
        sys.exit(1)
    print(f"navegación OK: {len(all_points())} puntos, {len(edges)} aristas, 0 cruces con muebles o paredes; "
          f"los 6 asientos llegan a las {len([s for s in SPOTS if not s.startswith('seat_')])} actividades")
    if "--check" in sys.argv:
        return
    data = {"walkable": WALKABLE, "blocked": BLOCKED, "margin": MARGIN, "nodes": NODES, "spots": SPOTS,
            "special": SPECIAL, "edges": edges}
    with open(os.path.join(out, "nav.json"), "w") as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(out, "nav.js"), "w") as f:
        f.write("window.NAV = " + json.dumps(data) + ";\n")
    walk, block = masks()
    over = np.zeros((H, W, 4), np.uint8)
    over[walk > 0] = (60, 255, 120, 70)
    over[block > 0] = (255, 60, 60, 110)
    cv2.imwrite(os.path.join(out, "nav_mask.png"), cv2.cvtColor(over, cv2.COLOR_RGBA2BGRA))


if __name__ == "__main__":
    main()
