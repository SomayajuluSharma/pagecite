# PageCite

Ask questions about a PDF and get answers that cite their pages. Click a page chip and the viewer jumps to that page with the cited sentence highlighted.

## What it does

- Upload a PDF. Text is extracted page by page with PyMuPDF and split into sentence-based chunks that never cross a page.
- Retrieval is BM25 written from scratch (no vector DB). Numbers are normalised, so `50000` matches `Rs 50,000`.
- **AI agent mode (tool calling).** An LLM gets two tools, `search_document` and `read_page`, and decides what to search and which pages to read. It must answer only from tool results and cite `[p.N]`. Plain code enforces the rules: a citation must point to a page the agent actually retrieved or read, an answer without a valid citation is rejected (one correction attempt, then fallback), and any model/network failure falls back to the extractive answer. The quote shown under each citation is picked by code from the cited page, not written by the model; if no sentence matches well (for example a fact that lives in a table) the UI says so instead of showing a misleading quote.
- **Extractive mode (no model, no network).** Returns the best sentences from the document, each tagged `[p.N]`. Abstains when the best passage covers too few of the question's terms.
- Page viewer with highlighting of the cited sentences and next/previous navigation. The tool calls the agent made are shown under each answer.

## Run

```bash
pip install -r requirements.txt
python samples/make_sample.py samples/expense_policy.pdf   # optional fictional demo PDF
python -m pagecite.server 8765          # open http://127.0.0.1:8765  (extractive mode works with no key)
```

AI agent mode (any OpenAI-compatible chat endpoint with function calling; tested with Groq `openai/gpt-oss-120b`):

```bash
export PAGECITE_LLM_URL=https://api.groq.com/openai/v1/chat/completions
export PAGECITE_LLM_MODEL=openai/gpt-oss-120b
export PAGECITE_LLM_KEY_FILE=$HOME/.groq_key     # or PAGECITE_LLM_KEY; never commit keys
```

## Tests

```bash
python -m unittest discover -s tests -v     # 27 offline tests, no network
```

The agent-logic tests use a scripted fake model (clearly named in the test file) to check the guard code: citation normalisation, rejection of citations to unseen pages, fallback paths, NOT_FOUND handling, bad tool arguments. Retrieval, verbatim-quote, rendering and HTTP API tests run on a generated fictional policy PDF (`samples/make_sample.py`).

## Live evaluation (real model, real paper)

`evals/live_eval.py` runs 10 questions (8 answerable, 2 unanswerable) on the 15-page paper "Attention Is All You Need" through both modes. Results from the real run (`evals/results.json`, model `openai/gpt-oss-120b` on Groq):

| Mode | Answer contains the right fact | Cites a correct page | Fell back |
|---|---|---|---|
| Extractive baseline | 6/10 | 7/10 | n/a |
| AI agent (tool calling) | 10/10 | 10/10 | 0 |

Small sample, questions written by the author, page correctness checked by me against the PDF; treat it as a smoke evaluation, not a benchmark. The free Groq tier rate-limits (HTTP 429); the app retries briefly, then falls back to extractive and says so in the UI.

## Limits

- No OCR: scanned or image-only pages are reported and skipped.
- Retrieval is keyword-based (BM25), so it can miss paraphrases; the agent can compensate by rephrasing searches or reading pages, but there are no embeddings. Facts in tables are cited by page only.
- Documents are held in memory for the life of the server; one process, no auth. Meant for local use.
- Tested on a small fictional PDF, not a benchmark.

## Public demo deployment (Render free web service)

`render.yaml` describes a free Render web service. Set `PAGECITE_LLM_KEY` in the Render dashboard (never in the repo). The model key is held server-side, so visitors need no key; their question and the PDF passages the agent reads are sent to the model provider (Groq).

Hosting and model quota are separate. Render's free instance sleeps after 15 minutes idle and takes about a minute to wake; the model uses a free Groq tier whose limits are shared across the whole Groq organisation. Safeguards (in `pagecite/guard.py`, tested in `tests/test_guard.py`):

- The provider's reported remaining quota is the real budget (`x-ratelimit-remaining-requests`), so it survives host restarts. Below a reserve, AI answers stop and the app falls back to labelled extractive answers.
- A local daily call cap, a provider 429 pause, a per-document hourly cap and a per-IP hourly cap. The per-IP key uses `X-Forwarded-For` counted from the right with `PAGECITE_TRUSTED_HOPS`, because the left side is client-controlled. Per-document and global limits do not depend on the IP.
- 5 MB upload cap, 20 documents held in memory, 500-character questions. PDFs and questions are not stored or logged.

## Browser demo (docs/)

`docs/` is a static, browser-only port (JavaScript + PDF.js) that can be served from GitHub Pages. The PDF never leaves the tab.

- Default mode is **extractive (no AI)**: BM25 keyword search that returns the best sentences with page citations.
- Optional **AI agent mode**: paste your own Groq API key. It is kept in tab memory only (no localStorage, cookies or telemetry). In this mode your question and the PDF passages the agent reads are sent to api.groq.com. No key is ever embedded in the site.
- Answers cite pages; clicking a citation jumps to the page and highlights the quoted text.
- PDF text is rendered with `textContent` only and the page has a Content-Security-Policy. Tested with a PDF containing HTML/script text and an instruction-style sentence: nothing executed, and the text is treated as plain document content.
- Browser port tests: 16 offline Node tests (`node --test docs/tests/pagecite.test.mjs`, Node 18+) cover extractive retrieval, unanswerable questions, citation normalisation and the agent's citation guards, using a scripted fake model (no network, so they test the guards, not model quality). They are separate from the Python tests above.
- Also checked by hand in headless Chrome on the sample PDF and the Attention paper (extractive and AI mode, real Groq call). The UI, PDF.js rendering and highlighting have no automated tests.
