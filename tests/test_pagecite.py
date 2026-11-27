import json, sys, threading, unittest, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))
import make_sample
import pymupdf
from pagecite import server
from pagecite.index import DocIndex, tokenize
from pagecite.agent import normalize_citations, run_agent
from pagecite.qa import answer_extractive
from pagecite.render import render_page

SAMPLE = ROOT / "samples" / "expense_policy.pdf"
if not SAMPLE.exists():
    make_sample.build(str(SAMPLE))
IDX = DocIndex(SAMPLE.read_bytes(), "expense_policy.pdf")


class Retrieval(unittest.TestCase):
    def test_pages_and_chunks(self):
        self.assertEqual(IDX.page_count, 3)
        self.assertEqual({c.page for c in IDX.chunks}, {1, 2, 3})

    def test_number_normalised(self):
        self.assertEqual(tokenize("Rs 50,000"), tokenize("rs 50000"))

    def test_answer_cites_right_page(self):
        r = answer_extractive(IDX, "Who approves expenses above 50000?")
        self.assertTrue(r["found"])
        self.assertIn(2, {c["page"] for c in r["citations"]})
        self.assertIn("finance director", r["answer"])

    def test_each_fact_maps_to_page(self):
        cases = {"How many days to submit travel claims?": (1, "30 days"),
                 "cash advance limit": (2, "20,000"),
                 "how fast are claims reimbursed": (3, "10 working days"),
                 "what percent of claims does audit sample": (3, "5 percent")}
        for q, (page, text) in cases.items():
            r = answer_extractive(IDX, q)
            hit = [c for c in r["citations"] if text in c["quote"]]
            self.assertTrue(hit, q)
            self.assertEqual(hit[0]["page"], page, q)

    def test_quotes_are_verbatim_on_cited_page(self):
        for q in ["cash advance limit", "when are claims reimbursed", "economy class flights"]:
            for c in answer_extractive(IDX, q)["citations"]:
                page_text = " ".join(IDX.doc[c["page"] - 1].get_text().split())
                self.assertIn(c["quote"], page_text)

    def test_abstains_when_absent(self):
        for q in ["What is the CEO salary?", "dress code for the office party", "???"]:
            r = answer_extractive(IDX, q)
            self.assertFalse(r["found"], q)
            self.assertEqual(r["citations"], [])


def scripted(*msgs):
    """Mock chat function: replays scripted model messages. Used ONLY in unit tests."""
    it = iter(msgs)
    return lambda messages: next(it)


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class AgentLogic(unittest.TestCase):
    """Tests the code that guards the model (mock model, no network)."""

    def test_citation_normalisation(self):
        for raw in ["x\u3010p.2\u3011.", "x (p. 2).", "x [page 2]."]:
            self.assertIn("[p.2]", normalize_citations(raw))

    def test_happy_path_with_tool_calls(self):
        chat = scripted(call("search_document", query="cash advance"),
                        {"role": "assistant", "content": "Advances are capped at Rs 20,000 [p.2]."})
        r = run_agent(IDX, "cash advance limit?", chat)
        self.assertEqual((r["mode"], r["found"]), ("agent", True))
        self.assertEqual(r["trace"][0]["tool"], "search_document")
        self.assertEqual(r["citations"][0]["page"], 2)
        self.assertIn("20,000", r["citations"][0]["quote"])

    def test_weak_match_cites_page_without_misleading_quote(self):
        chat = scripted(call("read_page", page=2),
                        {"role": "assistant", "content": "The zebra count is 77 [p.2]."})
        r = run_agent(IDX, "zebras?", chat)
        self.assertEqual(r["citations"], [{"page": 2, "quote": ""}])

    def test_citation_to_unseen_page_is_rejected_then_corrected(self):
        chat = scripted(call("search_document", query="cash advance"),
                        {"role": "assistant", "content": "It is 20,000 [p.3]."},      # p.3 never retrieved
                        {"role": "assistant", "content": "It is 20,000 [p.2]."})
        r = run_agent(IDX, "cash advance limit?", chat)
        self.assertEqual(r["mode"], "agent")
        self.assertEqual(r["citations"][0]["page"], 2)

    def test_uncitable_answer_falls_back_to_extractive(self):
        chat = scripted(call("search_document", query="cash advance"),
                        {"role": "assistant", "content": "No cites."}, {"role": "assistant", "content": "Still none."})
        r = run_agent(IDX, "cash advance limit", chat)
        self.assertEqual(r["mode"], "extractive")
        self.assertIn("fallback_reason", r)
        self.assertEqual(r["citations"][0]["page"], 2)

    def test_model_failure_falls_back(self):
        def boom(m): raise RuntimeError("429")
        r = run_agent(IDX, "cash advance limit", boom)
        self.assertEqual(r["mode"], "extractive")

    def test_not_found_passthrough(self):
        chat = scripted(call("search_document", query="ceo"), {"role": "assistant", "content": "NOT_FOUND"})
        r = run_agent(IDX, "CEO salary?", chat)
        self.assertFalse(r["found"]); self.assertEqual(r["citations"], [])

    def test_bad_tool_args_do_not_crash(self):
        chat = scripted(call("read_page", page=99), call("nope"),
                        {"role": "assistant", "content": "NOT_FOUND"})
        self.assertFalse(run_agent(IDX, "x?", chat)["found"])


class Guards(unittest.TestCase):
    def test_image_only_page_reported(self):
        d = pymupdf.open(); d.new_page(); b = d.tobytes()
        self.assertEqual(DocIndex(b).empty_pages, [1])

    def test_render_highlights_and_cleans(self):
        png, hits = render_page(IDX, 2, ["Cash advances are capped at Rs 20,000 per employee"])
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(hits, 0)
        self.assertEqual(len(list(IDX.doc[1].annots() or [])), 0)
        with self.assertRaises(IndexError):
            render_page(IDX, 9)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, data=None, headers=None):
        r = urllib.request.Request(self.base + path, data, headers or {})
        try:
            with urllib.request.urlopen(r) as f:
                return f.status, f.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow(self):
        code, body = self.req("/api/upload", SAMPLE.read_bytes(), {"X-Filename": "p.pdf"})
        self.assertEqual(code, 200); doc = json.loads(body)
        self.assertEqual(doc["pages"], 3)
        code, body = self.req("/api/ask", json.dumps({"doc": doc["id"], "question": "cash advance limit"}).encode())
        self.assertEqual(json.loads(body)["citations"][0]["page"], 2)
        code, body = self.req(f"/api/doc/{doc['id']}/page/2.png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(self.req(f"/api/doc/{doc['id']}/page/99.png")[0], 404)

    def test_sample_endpoint_and_doc_cap(self):
        code, body = self.req("/api/sample")
        self.assertEqual(code, 200)
        info = json.loads(body)
        self.assertEqual(info["pages"], 3)
        for _ in range(server.MAX_DOCS + 3):
            self.req("/api/sample")
        self.assertLessEqual(len(server.DOCS), server.MAX_DOCS)

    def test_rejects_bad_input(self):
        self.assertEqual(self.req("/api/upload", b"hello")[0], 400)
        self.assertEqual(self.req("/api/ask", b"{}")[0], 400)
        self.assertEqual(self.req("/../server.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
