"""Corre la prueba de punta a punta del HUD (tests/test_hud_e2e.js) si Node y Playwright están instalados."""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestHUD(unittest.TestCase):
    def test_hud_de_punta_a_punta(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node no está instalado")
        probe = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=ROOT)
        if probe.returncode != 0:
            self.skipTest("Playwright no está instalado (npm i playwright)")
        r = subprocess.run([node, os.path.join(ROOT, "tests", "test_hud_e2e.js")], capture_output=True, text=True, cwd=ROOT, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
