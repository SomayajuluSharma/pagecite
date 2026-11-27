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
from .qa import answer, answer_extractive
from .guard import BUDGET, PER_DOC, PER_IP, client_ip
from .render import render_page

STATIC = Path(__file__).resolve().parent.parent / "static"
import os
DEMO = os.environ.get("PAGECITE_DEMO") == "1"
MAX_UPLOAD = (5 if DEMO else 25) * 1024 * 1024
MAX_DOCS = 20
MAX_QUESTION = 500
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "expense_policy.pdf"
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

    def _store(self, did, idx):
        with LOCK:
            DOCS[did] = idx
            while len(DOCS) > MAX_DOCS:          # bound memory on a shared demo
                DOCS.pop(next(iter(DOCS)))

    def _ai_denied(self, doc_id):
        """Reason the AI answer must be skipped (falls back to extractive), or ''."""
        ip = client_ip(self.headers.get("X-Forwarded-For"), self.client_address[0])
        if not PER_DOC.check(doc_id):
            return "this document reached its hourly AI limit"
        if not PER_IP.check(ip):
            return "you reached the hourly AI limit for this demo"
        ok, why = BUDGET.allow()
        return "" if ok else why

    def _doc_info(self, did, idx):
        return {"id": did, "name": idx.name, "pages": idx.page_count, "chunks": len(idx.chunks),
                "agent_available": llm_configured(), "empty_pages": idx.empty_pages, "demo": DEMO}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/sample":
            if not SAMPLE.exists():
                return self._json({"error": "no sample PDF on this server"}, 404)
            idx = DocIndex(SAMPLE.read_bytes(), "expense_policy_sample.pdf")
            did = uuid.uuid4().hex[:12]
            self._store(did, idx)
            return self._json(self._doc_info(did, idx))
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
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._json({"error": "bad Content-Length"}, 400)
        if n < 0:
            return self._json({"error": "bad Content-Length"}, 400)
        if n > MAX_UPLOAD:
            return self._json({"error": "file too large ({} MB max)".format(MAX_UPLOAD // 2**20) + ""}, 413)
        body = self.rfile.read(n)
        if self.path == "/api/upload":
            if not body.startswith(b"%PDF"):
                return self._json({"error": "not a PDF file"}, 400)
            try:
                idx = DocIndex(body, self.headers.get("X-Filename", "document.pdf"))
            except Exception:
                return self._json({"error": "could not read this PDF"}, 400)
            did = uuid.uuid4().hex[:12]
            self._store(did, idx)
            return self._json(self._doc_info(did, idx))
        if self.path == "/api/ask":
            try:
                req = json.loads(body)
                doc = DOCS[req["doc"]]
                q = str(req["question"]).strip()
            except Exception:
                return self._json({"error": "need doc and question"}, 400)
            if not q:
                return self._json({"error": "empty question"}, 400)
            q = q[:MAX_QUESTION]
            mode = req.get("mode", "auto")
            if mode != "extractive" and llm_configured():
                why = self._ai_denied(req["doc"])
                if why:
                    r = answer_extractive(doc, q)
                    r["fallback_reason"] = why
                    return self._json(r)
            return self._json(answer(doc, q, mode))
        self._json({"error": "not found"}, 404)


def main(port=8765):
    port = int(os.environ.get("PORT", port))        # hosts such as Render pass the port in $PORT
    host = os.environ.get("PAGECITE_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"PageCite listening on {host}:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
