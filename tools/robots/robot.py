"""
Robot base de pixel art (V3) — se dibuja POR CÓDIGO sobre la grilla lógica, en dos tamaños enteros,
con paleta y accesorio por agente. Modelo: los robots calcados de la referencia (PRODUCT.md §3.3).

  Tamaño "S": piso superior (cabeza ~27 px lógicos; ~36 de alto de pie)
  Tamaño "L": piso inferior y pasillo (cabeza ~33 px; ~48 de alto de pie)
  (Escala por perspectiva SIN romper la grilla: dos tamaños, nunca un escalado continuo.)

Uso:  python tools/robots/robot.py <carpeta_assets> [paleta.json]
Genera assets/robots/<agente>_<S|L>.png (hoja de sprites) y assets/robots/robots.json
(por pose: cuadros, rectángulos y punto de los PIES de cada cuadro).
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image

AGENTS = ["codex", "claude", "vision", "ultron", "gentle", "antigravity"]
# colores tomados de la referencia (se ajustan a la paleta común al final)
ACCENT = {
    "codex": ("#2eb6f9", "#2e6cf0", "#9ff0ff"),        # (acento, acento oscuro, LED)
    "claude": ("#ff5a1a", "#c0280e", "#ff7ad9"),       # LED rosa como en la referencia
    "vision": ("#ffb21f", "#c77a10", "#ffb030"),
    "ultron": ("#36e35b", "#1a9a3a", "#5dff7a"),
    "gentle": ("#ff3fd0", "#b0209a", "#ff6be8"),
    "antigravity": ("#a86bff", "#6a3ad0", "#c9a8ff"),
}
BASE = {"white": "#f5f5f5", "light": "#e4e2f2", "shade": "#c79dea", "shade2": "#9567e5",
        "visor": "#060510", "rim": "#1b1830", "joint": "#666881", "outline": "#140c26", "dark": "#2a2440"}
GOLD = {"white": "#ffd23a", "light": "#ffc21f", "shade": "#e09a18", "shade2": "#a8650f"}   # Vision: traje dorado
# En la referencia la cabeza mide casi lo mismo en los dos pisos (~27-28 px lógicos de ancho):
# la diferencia de perspectiva es chica. L solo es un poco mayor y tiene piernas más largas.
SIZES = {"S": dict(ha=13, hb=10.8, tw=15, th=9, lh=8, arm=7, r=2.3),
         "L": dict(ha=14, hb=11.6, tw=16, th=10, lh=11, arm=8, r=2.6)}

HEX = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.t = np.full((h, w), "", dtype=object)          # tokens de color

    def put(self, x, y, tok):
        x, y = int(round(x)), int(round(y))
        if 0 <= x < self.w and 0 <= y < self.h:
            self.t[y, x] = tok

    def blob(self, cx, cy, a, b, n, tok_fn):
        """Superelipse |x/a|^n + |y/b|^n <= 1; tok_fn(u, v) decide el color (u, v en -1..1)."""
        for y in range(int(cy - b - 1), int(cy + b + 2)):
            for x in range(int(cx - a - 1), int(cx + a + 2)):
                u, v = (x + .5 - cx) / a, (y + .5 - cy) / b
                if abs(u) ** n + abs(v) ** n <= 1:
                    tk = tok_fn(u, v)
                    if tk:
                        self.put(x, y, tk)

    def rect(self, x0, y0, x1, y1, tok):
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                self.put(x, y, tok)

    def capsule(self, x0, y0, x1, y1, r, tok_fn):
        """Segmento grueso de (x0,y0) a (x1,y1) con radio r."""
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy or 1
        for y in range(int(min(y0, y1) - r - 1), int(max(y0, y1) + r + 2)):
            for x in range(int(min(x0, x1) - r - 1), int(max(x0, x1) + r + 2)):
                px, py = x + .5, y + .5
                t = max(0, min(1, ((px - x0) * dx + (py - y0) * dy) / L2))
                d = math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))
                if d <= r:
                    tk = tok_fn(t, (px - (x0 + t * dx)) / r if r else 0)
                    if tk:
                        self.put(x, y, tk)


def shade_tok(u, v):
    """Sombreado del casco/cuerpo: luz arriba-izquierda, sombra lavanda abajo-derecha (como la referencia)."""
    s = 0.55 * u + 0.75 * v
    if s > 0.78:
        return "shade2"
    if s > 0.38:
        return "shade"
    if s < -0.55:
        return "white"
    return "light" if s > 0.05 else "white"


# ------------------------------------------------------------------ partes
def head(c, cx, cy, P, agent, view="front", eyes="happy", tilt=0):
    a, b = P["ha"], P["hb"]
    side = {"front": 0, "left": -1, "right": 1, "back": 0}[view]
    # vincha (Gentle, Antigravity): arco sobre el casco, detrás
    if agent in ("gentle", "antigravity") and view != "back":
        c.blob(cx, cy - 1, a + 1.6, b + 1.6, 2, lambda u, v: "acc_d" if v < -0.2 and abs(u) > 0.15 or v < -0.93 else None)
    # orejas de gato (Claude)
    if agent == "claude":
        for sx in (-1, 1):
            ex = cx + sx * a * 0.55
            for k in range(int(b * 0.75)):
                half = max(0, (k * 0.55))
                for x in range(int(ex - half), int(ex + half + 1)):
                    inner = abs(x - ex) < half - 1.4 and k > 2
                    c.put(x, cy - b - b * 0.55 + k, "led" if inner else "acc")
    # casco: sombreado lavanda, franja inferior y brillo arriba a la izquierda (como la referencia)
    c.blob(cx, cy, a, b, 2.6, lambda u, v: "shade" if v > 0.72 else shade_tok(u, v))
    c.blob(cx - a * 0.42, cy - b * 0.62, a * 0.22, b * 0.10, 2, lambda u, v: "white")
    # costura de color arriba al centro
    for k in range(int(b * 0.45)):
        c.put(cx - 1 + side * 2, cy - b + 1 + k, "acc")
        c.put(cx + side * 2, cy - b + 1 + k, "acc" if k < b * 0.25 else "acc_d")
    if view == "back":
        c.blob(cx, cy + b * 0.35, a * 0.5, b * 0.18, 2, lambda u, v: "shade")
    else:
        # visor
        vx = cx + side * a * 0.16
        va, vb = a * (0.78 if side == 0 else 0.68), b * 0.58
        vy = cy + b * 0.10 + tilt
        c.blob(vx, vy, va + 1, vb + 1, 2.4, lambda u, v: "rim")
        c.blob(vx, vy, va, vb, 2.4, lambda u, v: "visor")
        c.blob(vx - va * 0.45, vy - vb * 0.55, va * 0.18, vb * 0.12, 2, lambda u, v: "rim")      # brillo
        draw_eyes(c, vx, vy, va, vb, agent, eyes)
    # auriculares (o piezas laterales doradas en Vision)
    for sx in ((-1, 1) if side == 0 else (-side,)):
        hx = cx + sx * (a - 0.5)
        r = b * 0.48
        c.blob(hx, cy + b * 0.10, r * 0.66, r, 2, lambda u, v: "acc_d" if u * sx > 0.45 else ("acc" if u * u + v * v > 0.22 else "led"))


def draw_eyes(c, vx, vy, va, vb, agent, eyes):
    ex = va * 0.46
    ey = vy - vb * 0.10
    if eyes == "off":
        for k in range(-2, 3):
            c.put(vx + k * 0.6, vy, "joint")
        c.put(vx, vy - 2, "joint"); c.put(vx + 1, vy - 3, "joint"); c.put(vx + 1, vy - 1, "joint"); c.put(vx, vy + 1, "joint")
        return
    for sx in (-1, 1):
        x0 = vx + sx * ex
        if eyes == "closed":
            for k in range(-2, 3):
                c.put(x0 + k, ey + 1, "led_d")
        elif agent == "vision" or eyes == "open":
            c.blob(x0, ey, 2.2, 2.2, 2, lambda u, v: "led" if u * u + v * v > 0.25 else "led_c")
        else:                                   # «^» feliz, trazo de 2 px como en la referencia
            w = 2 if va < 11 else 3
            for k in range(-w, w + 1):
                yy = ey - (w - abs(k)) * 0.75 + 0.5
                c.put(x0 + k, yy, "led"); c.put(x0 + k, yy + 1, "led")
        if agent == "ultron":                   # anteojos verdes
            c.blob(x0, ey, 3.6, 3.4, 2, lambda u, v: "acc" if u * u + v * v > 0.55 else None)
    if agent == "ultron":
        c.put(vx, ey - 1, "acc"); c.put(vx - 1, ey - 1, "acc")
    # sonrisa
    sy = vy + vb * 0.48
    sw = 2 if va < 11 else 3
    for k in range(-sw, sw + 1):
        c.put(vx + k, sy + (0 if abs(k) < sw else -1), "led" if eyes != "closed" else "led_d")


def torso(c, cx, top, P, view="front", lean=0):
    w, h = P["tw"], P["th"]
    if view == "right":
        w = w * 0.62
    c.blob(cx + lean, top + h / 2, w / 2 + 0.5, h / 2 + 0.5, 3, shade_tok)
    if view != "back":
        c.rect(cx - w * 0.28 + lean, top + 1, cx + w * 0.28 + lean, top + 2, "acc")       # cuello de color
        c.rect(cx - w * 0.16 + lean, top + h * 0.40, cx + w * 0.16 + lean, top + h * 0.72, "dark")   # panel del pecho
        c.put(cx + lean, top + h * 0.5, "acc")


def limb(c, x0, y0, x1, y1, r, end="acc_d", band=True):
    """Brazo o pierna: blanco con sombra lavanda, banda de color en la articulación y extremo de color."""
    def tok(t, s):
        if t > 0.72:
            return end
        if band and 0.38 < t < 0.5:
            return "acc"
        return "shade" if s > 0.25 else "white"
    c.capsule(x0, y0, x1, y1, r, tok)


def mug(c, x, y):
    c.rect(x, y, x + 4, y + 5, "white"); c.rect(x + 1, y + 1, x + 3, y + 2, "light")
    c.put(x + 4, y + 1, "white"); c.put(x + 5, y + 2, "white"); c.put(x + 4, y + 3, "white")
    c.put(x + 2, y + 3, "oil")


def cards(c, x, y, f):
    """Abanico de cartas de UNO (rojo, azul, amarillo, verde) con borde blanco."""
    for i, col in enumerate(("uno_r", "uno_b", "uno_y", "uno_g")):
        cx0, cy0 = x + i * 3, y - (1 if i in (1, 2) else 0) - (f if i == 3 else 0)
        c.rect(cx0, cy0, cx0 + 4, cy0 + 6, "white")
        c.rect(cx0 + 1, cy0 + 1, cx0 + 3, cy0 + 5, col)


def controller(c, x, y):
    c.rect(x, y, x + 7, y + 3, "dark"); c.put(x + 1, y + 1, "led"); c.put(x + 5, y + 1, "acc")


# ------------------------------------------------------------------ poses
def figure(agent, size, pose, f):
    """Devuelve (Canvas, (pie_x, pie_y)). f = número de cuadro."""
    P = SIZES[size]
    a, b = P["ha"], P["hb"]
    W, H = int(a * 2 + 22), int(b * 2 + P["th"] + P["lh"] + 22)
    c = Canvas(W, H)
    cx = W // 2
    feet = H - 4
    leg_h, th = P["lh"], P["th"]
    torso_top = feet - leg_h - th
    hy = torso_top - b + 2
    arm = P["arm"]
    r = P["r"]

    def legs(swing=0):
        for sx, ph in ((-1, swing), (1, -swing)):
            x = cx + sx * P["tw"] * 0.24
            limb(c, x, torso_top + th - 2, x + ph, feet - 2, r + 0.6)
            c.blob(x + ph + sx * 0.5, feet - 1.5, r + 1.4, 1.8, 2, lambda u, v: "acc_d")     # bota

    def arms(lx, ly, rx, ry):
        sh = torso_top + 2.5
        for sx, dx, dy in ((-1, lx, ly), (1, rx, ry)):
            x0 = cx + sx * (P["tw"] / 2 + 1)
            limb(c, x0, sh, x0 + dx, sh + dy, r, end="white")
            c.blob(x0 + dx, sh + dy + 0.5, r + 0.5, r + 0.3, 2, lambda u, v: "white" if v < 0.2 else "shade")   # mano

    if pose in ("walk_down", "walk_up", "walk_side"):
        ph = [0, 1, 0, -1][f % 4]
        bob = -1 if f % 2 else 0
        view = {"walk_down": "front", "walk_up": "back", "walk_side": "right"}[pose]
        if pose == "walk_side":
            legs(ph * 2)
            torso(c, cx, torso_top + bob, P, "right")
            limb(c, cx, torso_top + 2 + bob, cx - ph * 2.5, torso_top + arm + bob, r)
        else:
            for sx, s2 in ((-1, ph), (1, -ph)):
                x = cx + sx * P["tw"] * 0.22
                limb(c, x, torso_top + th - 1, x, feet - 1 - max(0, s2) * 1.5, r + 0.3)
            torso(c, cx, torso_top + bob, P, "front" if pose == "walk_down" else "back")
            arms(-2, arm - ph, 2, arm + ph)
        head(c, cx, hy + bob, P, agent, view)
    elif pose == "idle":
        bob = f % 2
        legs(); torso(c, cx, torso_top, P); arms(-2, arm, 2, arm)
        head(c, cx, hy + bob * 0.5, P, agent)
    elif pose == "wave":
        legs(); torso(c, cx, torso_top, P)
        arms(-2, arm, 3 + f, -arm - 1)
        c.blob(cx + P["tw"] / 2 + 3 + f, torso_top + 2 - arm - 2, 2.2, 2.2, 2, lambda u, v: "white")
        head(c, cx, hy, P, agent)
    elif pose == "type":           # sentado en el box (las piernas quedan detrás del escritorio)
        torso(c, cx, torso_top, P)
        up = [(0, 1), (1, 0)][f % 2]
        # brazos hacia el teclado: las manos quedan adelante, abajo del torso
        sh = torso_top + 3
        for sx, u in ((-1, up[0]), (1, up[1])):
            x0 = cx + sx * P["tw"] / 2
            limb(c, x0, sh, x0 - sx * 3, sh + arm - 1 - u, r, end="white")
            c.blob(x0 - sx * 4, sh + arm - u, r + 0.4, r, 2, lambda uu, vv: "white" if vv < 0.3 else "shade")
        head(c, cx, hy + (f % 2) * 0.4, P, agent)
    elif pose == "sit":            # sofá con joystick (sentado de frente, piernas hacia adelante)
        torso(c, cx, torso_top + 2, P)
        for sx in (-1, 1):
            x = cx + sx * P["tw"] * 0.25
            limb(c, x, torso_top + th + 1, x + sx * 1, feet - 1, r + 0.3)
        arms(1, arm - 3, -1, arm - 3)
        controller(c, cx - 3, torso_top + arm - 1 + (f % 2))
        head(c, cx, hy + 2, P, agent)
    elif pose == "fist":           # festejo con el puño en alto (como Vision en la referencia)
        legs(); torso(c, cx, torso_top, P)
        up = f % 2
        arms(-2, arm, 2, arm)                                   # brazo izquierdo abajo
        head(c, cx - 1, hy - up * 0.5, P, agent)
        sx0, sy0 = cx + P["tw"] / 2 + 1, torso_top + 3          # el derecho sube por fuera de la cabeza
        ex, ey = cx + P["ha"] + 3, hy - 2 - up * 2
        limb(c, sx0, sy0, ex, ey + 3, r, end="white")
        c.blob(ex, ey, r + 1.3, r + 1.1, 2, lambda u, v: "white" if v < 0.25 else "shade")      # puño
    elif pose == "uno":            # sentado de costado frente a la mesa, con las cartas en abanico
        limb(c, cx - 1, torso_top + th, cx + 5, torso_top + th + 1, r + 0.6, end="white", band=False)
        limb(c, cx + 5, torso_top + th + 1, cx + 6, feet - 2, r + 0.5)
        torso(c, cx - 1, torso_top + 1, P, "right")
        head(c, cx - 1, hy + 2, P, agent, "right")
        sh = torso_top + 4
        limb(c, cx, sh, cx + 7, sh + 1, r, end="white")         # brazo hacia adelante, por delante
        cards(c, cx + 7, sh - 5, f % 2)
        c.blob(cx + 8, sh + 1, r + 0.4, r, 2, lambda u, v: "white")
    elif pose == "chess":          # sentado de costado frente a la mesa; mueve una pieza
        # muslo hacia adelante y pierna hacia abajo (la mesa tapa esta parte en la escena)
        limb(c, cx - 1, torso_top + th, cx + 5, torso_top + th + 1, r + 0.6, end="white", band=False)
        limb(c, cx + 5, torso_top + th + 1, cx + 6, feet - 2, r + 0.5)
        torso(c, cx - 1, torso_top + 1, P, "right")
        reach = [4, 7][f % 2]
        sh = torso_top + 4
        limb(c, cx, sh, cx + reach, sh + 3 - (f % 2) * 2, r, end="white")
        c.blob(cx + reach + 1, sh + 3 - (f % 2) * 2, r + 0.4, r, 2, lambda u, v: "white")
        head(c, cx - 1, hy + 2, P, agent, "right")
    elif pose == "oil":            # de pie con la taza en la mano derecha; en el cuadro 1 toma un sorbo
        legs(); torso(c, cx, torso_top, P)
        sip = f % 2
        arms(-2, arm, 1, arm - 3 - sip * 4)
        head(c, cx - sip, hy, P, agent, eyes="closed" if sip else "happy")
        mx = cx + P["tw"] / 2 + 1 - sip * 3
        my = torso_top + arm - 6 - sip * 6
        mug(c, mx, my)
    elif pose == "sleep":          # recostado (cápsula), ojos cerrados
        c2 = Canvas(W + 10, H)
        c = c2
        cx2 = W // 2 + 2
        limb(c, cx2 + 2, feet - 6, cx2 + a + 8, feet - 3, r + 0.4)
        c.blob(cx2 + 4, feet - 8, P["th"] * 0.7, P["tw"] / 2, 2.4, shade_tok)
        head(c, cx2 - a + 4, feet - 8 - b * 0.6 + (f % 2) * 0.5, P, agent, eyes="closed")
    elif pose == "unknown":        # sin datos: quieto, visor apagado con «?»
        legs(); torso(c, cx, torso_top, P); arms(-2, arm, 2, arm)
        head(c, cx, hy, P, agent, eyes="off")
    else:
        raise ValueError(pose)
    return c, (cx, feet)


POSES = {  # pose: cuadros, tamaño(s), ms por cuadro
    "walk_down": (4, "SL", 160), "walk_up": (4, "SL", 160), "walk_side": (4, "SL", 160),
    "idle": (2, "SL", 600), "wave": (2, "SL", 300), "type": (2, "S", 220), "unknown": (1, "SL", 1000),
    "sit": (2, "L", 350), "chess": (2, "L", 900), "oil": (2, "L", 1200), "sleep": (2, "S", 1400),
    "fist": (2, "SL", 260), "uno": (2, "L", 700),
}


def render(c, agent, palette):
    acc, acc_d, led = ACCENT[agent]
    cols = dict(BASE)
    if agent == "vision":
        cols.update(GOLD)
    cols.update({"acc": acc, "acc_d": acc_d, "led": led, "led_c": "#fff2c0", "led_d": acc_d, "oil": "#ffb21f",
                 "uno_r": "#e8202a", "uno_b": "#2a6cf0", "uno_y": "#ffd21f", "uno_g": "#2ec44a"})
    rgb = {k: snap(HEX(v), palette) for k, v in cols.items()}
    h, w = c.t.shape
    img = np.zeros((h, w, 4), np.uint8)
    for y in range(h):
        for x in range(w):
            tk = c.t[y, x]
            if tk:
                img[y, x, :3] = rgb[tk]; img[y, x, 3] = 255
    # contorno de 1 px oscuro (los robots de la referencia tienen contorno casi negro)
    a = img[..., 3] > 0
    ring = np.zeros_like(a)
    ring[1:] |= a[:-1]; ring[:-1] |= a[1:]; ring[:, 1:] |= a[:, :-1]; ring[:, :-1] |= a[:, 1:]
    ring &= ~a
    img[ring, :3] = rgb["outline"]; img[ring, 3] = 255
    return img


def snap(c, palette):
    if palette is None:
        return c
    p = np.array(palette, np.int32)
    return tuple(int(v) for v in p[((p - np.array(c)) ** 2).sum(1).argmin()])


def build(out, palette=None):
    os.makedirs(os.path.join(out, "robots"), exist_ok=True)
    index = {"sizes": {}, "poses": {k: {"frames": v[0], "ms": v[2]} for k, v in POSES.items()}, "sheets": {}}
    for agent in AGENTS:
        for size in "SL":
            frames = []
            for pose, (n, sizes, ms) in POSES.items():
                if size not in sizes:
                    continue
                for f in range(n):
                    c, (fx, fy) = figure(agent, size, pose, f)
                    img = render(c, agent, palette)
                    ys, xs = np.nonzero(img[..., 3])
                    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
                    frames.append((pose, f, img[y0:y1, x0:x1], fx - x0, fy - y0))
            # hoja: una fila por pose
            rows, row = {}, []
            for fr in frames:
                rows.setdefault(fr[0], []).append(fr)
            sw = sum(max(f[2].shape[1] for f in r) * len(r) for r in rows.values())
            cw = max(f[2].shape[1] for f in frames)
            rh = max(f[2].shape[0] for f in frames)
            sheet = np.zeros((rh * len(rows), cw * max(len(r) for r in rows.values()), 4), np.uint8)
            meta = {}
            for ri, (pose, fl) in enumerate(rows.items()):
                meta[pose] = []
                for fi, (_, f, im, ax, ay) in enumerate(fl):
                    x, y = fi * cw, ri * rh
                    sheet[y:y + im.shape[0], x:x + im.shape[1]] = im
                    meta[pose].append({"x": int(x), "y": int(y), "w": int(im.shape[1]), "h": int(im.shape[0]),
                                       "fx": int(ax), "fy": int(ay)})
            name = f"{agent}_{size}.png"
            Image.fromarray(sheet, "RGBA").save(os.path.join(out, "robots", name))
            index["sheets"][f"{agent}_{size}"] = {"file": "robots/" + name, "poses": meta}
    with open(os.path.join(out, "robots", "robots.json"), "w") as f:
        json.dump(index, f)
    with open(os.path.join(out, "robots", "robots.js"), "w") as f:
        f.write("window.ROBOTS = " + json.dumps(index) + ";\n")
    return index


if __name__ == "__main__":
    out = sys.argv[1]
    pal = None
    pp = sys.argv[2] if len(sys.argv) > 2 else os.path.join(out, "palette.json")
    if os.path.isfile(pp):
        pal = [HEX(h) for h in json.load(open(pp))]
    idx = build(out, pal)
    print("hojas:", len(idx["sheets"]), "| poses:", ", ".join(idx["poses"]))
