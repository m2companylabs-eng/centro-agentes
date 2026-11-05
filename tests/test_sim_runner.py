"""Corre los escenarios del motor de comportamiento (tests/test_sim.js) con Node, si está instalado."""
import os
import shutil
import subprocess
import unittest


class TestMotor(unittest.TestCase):
    def test_escenarios_del_motor(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node no está instalado")
        js = os.path.join(os.path.dirname(__file__), "test_sim.js")
        r = subprocess.run([node, js], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
