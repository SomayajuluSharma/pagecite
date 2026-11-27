"""Answer building. Extractive by default; optional LLM mode must cite retrieved pages only."""
from .index import split_sentences, tokenize

MIN_COVERAGE = 0.5   # share of distinct question terms the best chunk must contain
MAX_SENTENCES = 3


def _coverage(q_tokens, text_tokens, idf=None):
    """Share of question terms found in the text, weighted by rarity when idf is given."""
    qs = set(q_tokens)
    if not qs:
        return 0.0
    w = (lambda t: idf.get(t, 1.0)) if idf else (lambda t: 1.0)
    return sum(w(t) for t in qs & set(text_tokens)) / sum(w(t) for t in qs)


def answer_extractive(index, question):
    hits, q = index.search(question, k=5)
    if not hits:
        return _not_found(index, "No passage shares terms with the question.")
    best_cov = max(_coverage(q, c.tokens) for _, c in hits)
    if best_cov < MIN_COVERAGE:
        return _not_found(index, "Closest passages cover too few of the question's terms.")
    cands = []
    for score, chunk in hits:
        for s in split_sentences(chunk.text):
            st = tokenize(chunk.heading + " " + s)
            cov = _coverage(q, st, index.idf)
            if cov > 0:
                cands.append((cov + 0.01 * score, s, chunk.page))
    cands.sort(key=lambda x: -x[0])
    floor = cands[0][0] * 0.8
    picked, seen = [], set()
    for sc, s, page in cands:
        if s in seen or sc < floor:
            continue
        seen.add(s)
        picked.append({"page": page, "quote": s})
        if len(picked) == MAX_SENTENCES:
            break
    picked.sort(key=lambda c: c["page"])
    text = " ".join(f'{c["quote"]} [p.{c["page"]}]' for c in picked)
    return {"answer": text, "citations": picked, "mode": "extractive", "found": True}


def _not_found(index, why):
    return {"answer": "I could not find this in the document. " + why,
            "citations": [], "mode": "extractive", "found": False}


def answer(index, question, mode="auto", chat=None):
    """mode: extractive | agent | auto (agent when an LLM is configured, else extractive)."""
    from .agent import llm_configured, run_agent
    if mode == "extractive" or (mode == "auto" and chat is None and not llm_configured()):
        return answer_extractive(index, question)
    return run_agent(index, question, chat=chat)
