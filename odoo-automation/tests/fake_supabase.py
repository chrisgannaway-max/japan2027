"""A stand-in for Supabase Storage: enough of the object API to test the adapter for real.

Supabase Storage is a plain HTTP object store behind a bearer token:

    POST   /storage/v1/object/<bucket>/<key>   body = the file       (x-upsert: true to replace)
    GET    /storage/v1/object/<bucket>/<key>   -> the file
    DELETE /storage/v1/object/<bucket>/<key>

That is the whole surface the portal uses, so a twenty-line server exercises the same code
paths a real project would, without an account or a network.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: shaped like a real legacy service_role key, which is a JWT.  The shape matters: the
#: adapter decides how to authenticate from it.
TOKEN = "eyJhbGciOiJIUzI1NiJ9.c2VydmljZV9yb2xl.sig"
PREFIX = "/storage/v1/object/"


class FakeSupabase:
    def __init__(self):
        self.objects: dict[str, bytes] = {}          # "bucket/key" -> bytes
        self.requests: list[tuple[str, str]] = []    # (method, bucket/key), so tests can count them
        self.auth_headers: list[dict] = []           # what each request authenticated with
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):               # keep pytest output clean
                pass

            def _path(self):
                if not self.path.startswith(PREFIX):
                    return None
                return self.path[len(PREFIX):]

            def _auth_ok(self):
                """Supabase's gateway reads `apikey`; a legacy JWT may also arrive as a bearer
                token.  Accept either, and record which arrived so a test can check it."""
                outer.auth_headers.append(dict(self.headers))
                return (self.headers.get("apikey") == TOKEN
                        or self.headers.get("Authorization") == f"Bearer {TOKEN}")

            def _send(self, code, body=b"", content_type="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                key = self._path()
                if key is None:
                    return self._send(404, b'{"error":"no such route"}')
                outer.requests.append((method, key))
                if not self._auth_ok():
                    return self._send(401, b'{"error":"invalid token"}')
                if method == "POST":
                    length = int(self.headers.get("Content-Length", 0))
                    data = self.rfile.read(length)
                    if key in outer.objects and self.headers.get("x-upsert") != "true":
                        return self._send(409, b'{"error":"Duplicate"}')
                    outer.objects[key] = data
                    return self._send(200, b'{"Key":"%s"}' % key.encode())
                if method == "GET":
                    if key not in outer.objects:
                        return self._send(400, b'{"error":"Object not found"}')
                    return self._send(200, outer.objects[key], "application/octet-stream")
                if method == "DELETE":
                    outer.objects.pop(key, None)
                    return self._send(200, b'{"message":"Successfully deleted"}')
                return self._send(405)

            def do_POST(self):
                self._handle("POST")

            def do_GET(self):
                self._handle("GET")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
