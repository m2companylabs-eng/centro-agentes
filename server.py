"""
Servidor local del Centro de Agentes (solo lectura).

    python server.py            →  http://127.0.0.1:8088/

- Sirve la escena (prototype/escena/) y la API GET /api/telemetry (formato de src/telemetry/aggregate.py).
- Escucha SOLO en 127.0.0.1: la telemetría no se expone a la red local ni a internet (PRODUCT.md §6, §8).
  (La versión anterior escuchaba en todas las interfaces.)
- Cachea el resultado 3 s para no releer las fuentes en cada pedido.
- El dashboard anterior (web/) queda como legado y ya no se sirve.
"""
import json
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
from src.telemetry import aggregate, vision_remote  # noqa: E402

# Vision (VPS) se consulta en segundo plano: una VPS lenta no frena la respuesta del tablero. Auditoría PRE-01.
vision_remote.BACKGROUND = True

HOST, PORT = "127.0.0.1", int(os.environ.get("CENTRO_PORT", "8088"))
WEB_DIR = os.path.join(ROOT, "prototype", "escena")
TTL_S = 3
_lock, _cache = threading.Lock(), {"t": 0.0, "data": None}


def telemetry():
    with _lock:
        if _cache["data"] is None or time.time() - _cache["t"] > TTL_S:
            _cache["data"], _cache["t"] = aggregate.collect(), time.time()
        return _cache["data"]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/telemetry":
            try:
                body, code = json.dumps(telemetry(), ensure_ascii=False).encode("utf-8"), 200
            except Exception as e:  # noqa: BLE001
                body, code = json.dumps({"error": type(e).__name__}).encode("utf-8"), 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):          # solo lectura: no se acepta ninguna escritura
        self.send_error(405)

    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Centro de Agentes en http://{HOST}:{PORT}/  (Ctrl+C para salir)")
    srv.serve_forever()
