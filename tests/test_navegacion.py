"""
Pruebas del mapa de navegación (tools/escena/nav.py): ninguna ruta atraviesa muebles ni sale del piso,
y desde cada asiento se llega a cada actividad.  Correr: python -m unittest discover -s tests -v
"""
import importlib.util
import os
import unittest

try:                                   # OpenCV solo hace falta para las herramientas de arte, no para usar el tablero
    import cv2  # noqa: F401
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

NAV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools", "escena", "nav.py"))


def load():
    spec = importlib.util.spec_from_file_location("nav", NAV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAS_CV2, "OpenCV (cv2) no instalado: solo se usa para generar el arte")
class TestNavegacion(unittest.TestCase):
    def test_rutas_validas(self):
        errors, _ = load().validate()
        self.assertEqual(errors, [])

    def test_detecta_un_cruce(self):
        nav = load()
        nav.EDGES.append(("lounge_in", "lounge_e"))          # recta que cruzaría la mesa de ajedrez? (control)
        nav.BLOCKED["muro_prueba"] = (1000, 680, 1010, 720)   # bloqueo artificial sobre esa recta
        errors, _ = nav.validate()
        self.assertTrue(any("muro_prueba" in e for e in errors))

    def test_todos_los_agentes_tienen_asiento(self):
        nav = load()
        for a in ("codex", "claude", "vision", "ultron", "gentle", "antigravity"):
            self.assertIn(f"seat_{a}", nav.SPOTS)


if __name__ == "__main__":
    unittest.main()
