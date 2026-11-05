"""
Pruebas del generador de robots (tools/robots/robot.py): hojas completas, cuadros no vacíos,
pies dentro del cuadro, tamaños parecidos a la referencia y colores dentro de la paleta común.
Correr: python -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAL = os.path.join(ROOT, "prototype", "escena", "assets", "palette.json")


def load():
    spec = importlib.util.spec_from_file_location("robot", os.path.join(ROOT, "tools", "robots", "robot.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class TestRobots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.R = load()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pal = [cls.R.HEX(h) for h in json.load(open(PAL))] if os.path.isfile(PAL) else None
        cls.idx = cls.R.build(cls.tmp.name, cls.pal)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_doce_hojas_y_todas_las_poses(self):
        self.assertEqual(len(self.idx["sheets"]), 12)
        for key, sh in self.idx["sheets"].items():
            size = key.split("_")[1]
            for pose, (n, sizes, _ms) in self.R.POSES.items():
                if size in sizes:
                    self.assertEqual(len(sh["poses"][pose]), n, f"{key} {pose}")

    def test_cuadros_no_vacios_y_pies_dentro(self):
        for key, sh in self.idx["sheets"].items():
            img = np.array(Image.open(os.path.join(self.tmp.name, sh["file"])))
            for pose, frs in sh["poses"].items():
                for f in frs:
                    crop = img[f["y"]:f["y"] + f["h"], f["x"]:f["x"] + f["w"]]
                    self.assertGreater((crop[..., 3] > 0).sum(), 150, f"{key} {pose} vacío")
                    # en «type» las piernas no se dibujan (las tapa el escritorio): los pies quedan debajo del cuadro
                    extra = 12 if pose == "type" else 1
                    self.assertTrue(0 <= f["fx"] <= f["w"] and 0 <= f["fy"] <= f["h"] + extra, f"{key} {pose} pies")

    def test_tamano_como_la_referencia(self):
        # referencia: cabeza ~27-28 px lógicos de ancho; robot de pie del piso inferior ~50 de alto
        for key, sh in self.idx["sheets"].items():
            f = sh["poses"]["idle"][0]
            self.assertTrue(26 <= f["w"] <= 40, f"{key} ancho {f['w']}")
            if key.endswith("_L"):
                self.assertTrue(42 <= f["h"] <= 56, f"{key} alto {f['h']}")

    def test_colores_en_la_paleta(self):
        if not self.pal:
            self.skipTest("sin paleta")
        pal = {tuple(c) for c in self.pal}
        for sh in self.idx["sheets"].values():
            img = np.array(Image.open(os.path.join(self.tmp.name, sh["file"])).convert("RGBA"))
            cols = {tuple(p[:3]) for p in img.reshape(-1, 4) if p[3] > 0}
            self.assertTrue(cols <= pal, f"{sh['file']}: {len(cols - pal)} colores fuera de la paleta")


if __name__ == "__main__":
    unittest.main()
