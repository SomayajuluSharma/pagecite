import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from pagecite import guard


class GuardUnit(unittest.TestCase):
    def test_rate_limiter_window(self):
        r = guard.RateLimiter(2, 60)
        self.assertTrue(r.check("a", 0)); self.assertTrue(r.check("a", 1))
        self.assertFalse(r.check("a", 2)); self.assertTrue(r.check("b", 2))
        self.assertTrue(r.check("a", 61))

    def test_client_ip_ignores_spoofed_left_side(self):
        # client sends "1.1.1.1"; Cloudflare appends the real client; Render's balancer appends Cloudflare's edge
        xff = "1.1.1.1, 203.0.113.9, 172.70.0.5"
        self.assertEqual(guard.client_ip(xff, "10.0.0.1", trusted_hops=2), "203.0.113.9")
        self.assertEqual(guard.client_ip("9.9.9.9, " + xff, "10.0.0.1", trusted_hops=2), "203.0.113.9")

    def test_client_ip_without_proxy_ignores_header(self):
        self.assertEqual(guard.client_ip("1.2.3.4", "10.0.0.1", trusted_hops=0), "10.0.0.1")

    def test_client_ip_short_chain_falls_back_to_peer(self):
        self.assertEqual(guard.client_ip("1.2.3.4", "10.0.0.1", trusted_hops=2), "10.0.0.1")

    def test_budget_uses_provider_reported_quota_after_restart(self):
        b = guard.Budget()                      # fresh process: nothing known yet, so allowed
        self.assertTrue(b.allow()[0])
        b.record_call({"x-ratelimit-remaining-requests": "120"})   # provider says 120 left, reserve is 150
        ok, why = b.allow()
        self.assertFalse(ok); self.assertIn("nearly used up", why)

    def test_budget_daily_cap_and_429_block(self):
        os.environ["PAGECITE_DAILY_AI_CALLS"] = "2"
        try:
            b = guard.Budget(); b.record_call(); b.record_call()
            self.assertFalse(b.allow()[0])
        finally:
            del os.environ["PAGECITE_DAILY_AI_CALLS"]
        b = guard.Budget(); b.record_429(30)
        self.assertFalse(b.allow()[0])


class FakeLLM(BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *a): pass
    def do_POST(self):
        FakeLLM.calls += 1
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "NOT_FOUND"}}]}).encode()
        self.send_response(200)
        self.send_header("x-ratelimit-remaining-requests", "100")   # below the reserve: next call must be refused
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class ServerGuards(unittest.TestCase):
    def setUp(self):
        self.llm = HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=self.llm.serve_forever, daemon=True).start()
        os.environ.update(PAGECITE_LLM_URL=f"http://127.0.0.1:{self.llm.server_port}/", PAGECITE_LLM_MODEL="m", PAGECITE_LLM_KEY="test-key")
        guard.BUDGET.__init__(); FakeLLM.calls = 0
        from pagecite import server
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.doc = json.load(urlopen(self.base + "/api/sample"))["id"]

    def tearDown(self):
        self.srv.shutdown(); self.llm.shutdown()
        for k in ("PAGECITE_LLM_URL", "PAGECITE_LLM_MODEL", "PAGECITE_LLM_KEY"): os.environ.pop(k, None)

    def ask(self, q="Who approves expenses above 50000?"):
        r = Request(self.base + "/api/ask", json.dumps({"doc": self.doc, "question": q}).encode())
        return json.load(urlopen(r))

    def test_ai_stops_when_provider_quota_low_and_falls_back(self):
        first = self.ask()
        self.assertEqual(first["mode"], "agent")
        second = self.ask()                      # provider reported 100 left (< reserve 150)
        self.assertEqual(second["mode"], "extractive"); self.assertIn("nearly used up", second["fallback_reason"])
        self.assertEqual(FakeLLM.calls, 1)       # the refused question never reached the model
        self.assertTrue(second["found"])         # and still got a cited extractive answer

    def test_bad_content_length_rejected(self):
        r = Request(self.base + "/api/upload", b"x", {"Content-Length": "-5"})
        with self.assertRaises((HTTPError, Exception)):
            urlopen(r)


if __name__ == "__main__":
    unittest.main()
