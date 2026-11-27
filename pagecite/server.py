"""Tiny stdlib HTTP server: static UI + JSON API."""
import json
import mimetypes
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .index import DocIndex
from .agent import llm_configured
from .qa import answer
from .render import render_page

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD = 25 * 1024 * 1024
DOCS = {}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        m = re.fullmatch(r"/api/doc/([a-f0-9]+)/page/(\d+)\.png", u.path)
        if m:
            doc = DOCS.get(m.group(1))
            if not doc:
                return self._json({"error": "unknown document"}, 404)
            quotes = parse_qs(u.query).get("q", [])
            try:
                with LOCK:
                    data, _ = render_page(doc, int(m.group(2)), quotes)
            except IndexError:
                return self._json({"error": "page out of range"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        rel = "index.html" if u.path == "/" else u.path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC in f.parents and f.is_file():
            b = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            return self._json({"error": "file too large (25 MB max)"}, 413)
        body = self.rfile.read(n)
        if self.path == "/api/upload":
            if not body.startswith(b"%PDF"):
                return self._json({"error": "not a PDF file"}, 400)
            try:
                idx = DocIndex(body, self.headers.get("X-Filename", "document.pdf"))
            except Exception:
                return self._json({"error": "could not read this PDF"}, 400)
            did = uuid.uuid4().hex[:12]
            DOCS[did] = idx
            return self._json({"id": did, "name": idx.name, "pages": idx.page_count,
                               "chunks": len(idx.chunks), "agent_available": llm_configured(), "empty_pages": idx.empty_pages})
        if self.path == "/api/ask":
            try:
                req = json.loads(body)
                doc = DOCS[req["doc"]]
                q = str(req["question"]).strip()
            except Exception:
                return self._json({"error": "need doc and question"}, 400)
            if not q:
                return self._json({"error": "empty question"}, 400)
            return self._json(answer(doc, q, req.get("mode", "auto")))
        self._json({"error": "not found"}, 404)


def main(port=8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PageCite running at http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
