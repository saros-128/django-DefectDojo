"""Internal-only HTTP fault injection: forward upstream, drop one committed response."""
import hashlib
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

lock = threading.Lock()
dropped = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def proxy(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "connection", "content-length"}}
        response = httpx.request(self.command, os.environ["UPSTREAM"] + self.path, content=body, headers=headers, timeout=120)
        with lock:
            lose = not dropped.is_set() and self.command == "POST" and self.path == os.environ["DROP_PATH"] and response.status_code in {200, 201}
            if lose:
                dropped.set()
                Path("/evidence/" + os.environ["EVIDENCE_FILE"]).write_text(json.dumps({
                    "upstream_status": response.status_code, "upstream_result": response.json(),
                    "request_body_sha256": hashlib.sha256(body).hexdigest(), "response_intentionally_lost": True}, indent=2), encoding="utf-8")
        if lose:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            self.close_connection = True
            return
        self.send_response(response.status_code)
        self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(response.content)))
        self.end_headers()
        self.wfile.write(response.content)

    do_GET = proxy
    do_POST = proxy
    do_PUT = proxy
    do_DELETE = proxy


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104 -- internal Docker network, no published port
