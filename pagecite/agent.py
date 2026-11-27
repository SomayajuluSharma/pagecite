"""Tool-calling agent over the PDF index.

The model decides what to search and which pages to read (function calling), then answers
with [p.N] citations. Code, not the model, enforces the rules:
  * a citation must point to a page the agent actually retrieved or read,
  * an answer with no valid citation is rejected,
  * on any failure or rejection we fall back to the extractive answer.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .index import split_sentences, tokenize
from .qa import answer_extractive

MAX_STEPS = 6
MIN_QUOTE_MATCH = 0.4
CITE = re.compile(r"\[p\.(\d+)\]")

SYSTEM = ("You answer questions about one PDF using tools. Use search_document first; use read_page only "
          "when a passage looks relevant but cut off. Answer ONLY from tool results. Cite every claim as [p.N] "
          "using the page numbers the tools returned. If the document does not contain the answer, reply "
          "exactly: NOT_FOUND. Be concise (1-4 sentences). Do not use outside knowledge.")

TOOLS = [
    {"type": "function", "function": {
        "name": "search_document", "description": "Keyword search over the PDF. Returns top passages with page numbers.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_page", "description": "Return the full text of one page (1-based).",
        "parameters": {"type": "object", "properties": {"page": {"type": "integer"}}, "required": ["page"]}}},
]


ALT_CITE = re.compile(r"[\[【(]\s*(?:p|pp|page)\.?\s*(\d+)\s*[\]】)]", re.I)


def normalize_citations(text):
    """Models vary the bracket style; map 【p.7】, (p. 7), [page 7] to [p.7]."""
    text = ALT_CITE.sub(lambda m: f"[p.{m.group(1)}]", text.replace("\u202f", " ").replace("\u2011", "-"))
    return re.sub(r"(?<=\S)\s*(\[p\.\d+\])", r" \1", text)


def _key():
    k = os.environ.get("PAGECITE_LLM_KEY")
    f = os.environ.get("PAGECITE_LLM_KEY_FILE")
    if not k and f and Path(f).exists():
        k = Path(f).read_text().strip()
    return k


def llm_configured():
    return bool(os.environ.get("PAGECITE_LLM_URL") and os.environ.get("PAGECITE_LLM_MODEL") and _key())


def http_chat(messages):
    body = {"model": os.environ["PAGECITE_LLM_MODEL"], "temperature": 0, "messages": messages,
            "tools": TOOLS, "tool_choice": "auto"}
    req = urllib.request.Request(os.environ["PAGECITE_LLM_URL"], json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "pagecite/0.1", "Authorization": f"Bearer {_key()}"})
    for attempt in range(3):   # honour rate limits (free tiers) with a short bounded backoff
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            time.sleep(min(float(e.headers.get("Retry-After", 3)), 15))


def run_tool(index, name, args, seen_pages):
    if name == "search_document":
        hits, _ = index.search(str(args.get("query", "")), k=4)
        for _, c in hits:
            seen_pages.add(c.page)
        return json.dumps([{"page": c.page, "text": c.text} for _, c in hits]) if hits else "no matches"
    if name == "read_page":
        n = int(args.get("page", 0))
        if not 1 <= n <= index.page_count:
            return "page out of range"
        seen_pages.add(n)
        return " ".join(index.doc[n - 1].get_text().split())[:6000]
    return "unknown tool"


def _quotes_for(index, text, pages):
    """For each cited page, pick the page sentence that best matches the answer sentence citing it."""
    out = []
    for sent in split_sentences(CITE.sub(lambda m: f" [p.{m.group(1)}]", text)):
        for p in {int(x) for x in CITE.findall(sent)}:
            if p not in pages:
                continue
            a = set(tokenize(CITE.sub("", sent)))
            best, score = None, 0.0
            for c in (c for c in index.chunks if c.page == p):
                for s in split_sentences(c.text):
                    t = set(tokenize(s))
                    sc = 2 * len(a & t) / ((len(a) + len(t)) or 1)  # Dice overlap
                    if sc > score:
                        best, score = s, sc
            if score < MIN_QUOTE_MATCH:   # e.g. facts from tables: cite the page, show no misleading quote
                best = ""
            if best is not None and not any(o["page"] == p and o["quote"] == best for o in out):
                out.append({"page": p, "quote": best})
    return sorted(out, key=lambda c: c["page"])


def run_agent(index, question, chat=None):
    chat = chat or http_chat
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    seen, trace, corrections = set(), [], 0
    try:
        for _ in range(MAX_STEPS):
            m = chat(msgs)
            calls = m.get("tool_calls") or []
            if not calls:
                text = normalize_citations((m.get("content") or "").strip())
                if text == "NOT_FOUND" or text.startswith("NOT_FOUND"):
                    return {"answer": "I could not find this in the document.", "citations": [], "mode": "agent",
                            "found": False, "trace": trace}
                cited = {int(p) for p in CITE.findall(text)}
                if cited and cited <= seen and seen:
                    quotes = _quotes_for(index, text, seen)
                    return {"answer": text, "citations": quotes, "mode": "agent", "found": True, "trace": trace}
                if corrections < 1:   # one chance to fix the format / cite a page it actually saw
                    corrections += 1
                    msgs += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "Rejected: cite each claim as [p.N] (square brackets) "
                              "using only pages returned by your tools. Answer again, or reply NOT_FOUND."}]
                    continue
                raise ValueError("answer rejected: missing or unverified citation")
            msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
            for c in calls:
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                    res = run_tool(index, c["function"]["name"], args, seen)
                    trace.append({"tool": c["function"]["name"], "args": args})
                except Exception as e:
                    res = f"tool error: {e}"
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": res})
        raise ValueError("step limit reached")
    except Exception as e:
        base = answer_extractive(index, question)
        base["fallback_reason"] = str(e)[:200]
        base["trace"] = trace
        return base
