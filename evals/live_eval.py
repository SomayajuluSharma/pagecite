"""Live evaluation on a real 15-page paper ("Attention Is All You Need", arXiv 1706.03762).

Runs each question through the extractive baseline and the real tool-calling agent, and scores:
  - answer contains the expected fact (substring, case-insensitive)
  - a citation points to the expected page
  - unanswerable questions are refused (found == False)

Usage (needs a Groq/OpenAI-compatible endpoint; the key is read from a file, never printed):
  python evals/live_eval.py /path/to/paper.pdf [key_file]
Download the PDF yourself:  curl -L -o attention.pdf https://arxiv.org/pdf/1706.03762
"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PAGECITE_LLM_URL", "https://api.groq.com/openai/v1/chat/completions")
os.environ.setdefault("PAGECITE_LLM_MODEL", "openai/gpt-oss-120b")
if len(sys.argv) > 2:
    os.environ["PAGECITE_LLM_KEY_FILE"] = sys.argv[2]
from pagecite.index import DocIndex
from pagecite.qa import answer

# (question, expected substring, expected page or None if unanswerable)
CASES = [
    ("How many attention heads does the base Transformer model use?", "8", 5),
    ("What BLEU score did the big model get on English-to-German translation?", "28.4", 8),
    ("What optimizer was used and what were its beta values?", "0.98", 7),
    ("How many GPUs were used for training and which type?", "P100", (7, 8)),  # P100 appears on pages 2, 7, 8; 7 and 8 both state training hardware
    ("What dropout rate was used for the base model?", "0.1", 8),
    ("What is the dimensionality of the model outputs, d_model, in the base model?", "512", 5),
    ("How many layers does the encoder stack have?", "6", 3),
    ("Why are the dot products scaled by the square root of d_k?", "gradient", 4),
    ("Who won the 2018 football world cup?", None, None),
    ("What is the capital of France?", None, None),
]

def main():
    idx = DocIndex(Path(sys.argv[1]).read_bytes(), "attention.pdf")
    rows = []
    for mode in ("extractive", "agent"):
        for q, fact, page in CASES:
            r = answer(idx, q, mode)
            if fact is None:
                ok_fact = ok_page = not r["found"]
            else:
                ok_fact = r["found"] and fact.lower() in r["answer"].lower()
                ok_page = any(c["page"] in (page if isinstance(page, tuple) else (page,)) for c in r["citations"])
            rows.append({"mode": mode, "question": q, "answer": r["answer"], "actual_mode": r["mode"],
                         "fallback_reason": r.get("fallback_reason"), "cited_pages": sorted({c["page"] for c in r["citations"]}),
                         "tool_calls": [t["tool"] + str(list(t["args"].values())) for t in r.get("trace", [])],
                         "fact_ok": bool(ok_fact), "page_ok": bool(ok_page)})
            if mode == "agent":
                time.sleep(5)  # stay under free-tier rate limits
    for mode in ("extractive", "agent"):
        rs = [r for r in rows if r["mode"] == mode]
        fb = sum(1 for r in rs if r["actual_mode"] != mode)
        print(f"{mode}: fact {sum(r['fact_ok'] for r in rs)}/{len(rs)}, page {sum(r['page_ok'] for r in rs)}/{len(rs)}, fell back {fb}")
    out = Path(__file__).with_name("results.json")
    out.write_text(json.dumps({"model": os.environ["PAGECITE_LLM_MODEL"], "rows": rows}, indent=1, ensure_ascii=False))

main()
