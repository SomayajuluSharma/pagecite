// PageCite core, browser version: BM25 retrieval, extractive answers, tool-calling agent with citation guards.
// Port of the Python implementation in ../pagecite/. No server: everything runs in the page.

const STOP = new Set(`a an and are as at be by for from has have how i in is it its of on or that the this to was were what when where which who whom whose why will with does do did can could should would about into than then there their they them we you your our not no yes`.split(/\s+/));
const TOKEN = /[a-z0-9][a-z0-9.%,₹$-]*[a-z0-9%]|[a-z0-9]/g;

export function stem(t) {
  for (const suf of ["ing", "edly", "ed", "es", "s"]) {
    if (t.endsWith(suf) && t.length - suf.length >= 3 && !/^\d+$/.test(t)) {
      let r = t.slice(0, -suf.length);
      if (r.length > 3 && r.at(-1) === r.at(-2) && !"lsz".includes(r.at(-1))) r = r.slice(0, -1);
      return r;
    }
  }
  return t;
}
export function tokenize(text) {
  text = text.replace(/(?<=\d),(?=\d)/g, "").toLowerCase();
  const out = [];
  for (let t of text.match(TOKEN) || []) {
    t = t.replace(/^[.,-]+|[.,-]+$/g, "");
    if (t && !STOP.has(t)) out.push(stem(t));
  }
  return out;
}
export function splitSentences(text) {
  if (!text.includes("\n\n")) text = text.replace(/[ \t]*\n[ \t]*/g, " ");
  return text.split(/(?<=[.!?])\s+(?=[A-Z0-9(])|\n{2,}/).map(p => p.replace(/\s+/g, " ").trim()).filter(p => p.length > 3);
}
function isHeading(block) {
  const t = block.split(/\s+/).join(" ");
  return t.split(" ").length <= 8 && !/[.!?:;,]$/.test(t);
}
function chunkBlock(text, target = 350) {
  const out = []; let cur = "";
  for (const s of splitSentences(text)) {
    if (cur && cur.length + s.length > target) { out.push(cur); cur = ""; }
    cur = (cur + " " + s).trim();
  }
  if (cur) out.push(cur);
  return out;
}

// Build blocks (paragraphs) from pdf.js text items using line breaks and vertical gaps.
export function pageBlocks(items) {
  const lines = []; let cur = null;
  for (const it of items) {
    if (!("str" in it)) continue;
    const y = it.transform[5], h = it.height || 10;
    if (!cur || Math.abs(cur.y - y) > h * 0.5) { cur = { y, h, text: "" }; lines.push(cur); }
    cur.text += it.str;
  }
  const blocks = []; let b = null, prev = null;
  for (const l of lines) {
    const t = l.text.trim(); if (!t) continue;
    if (b && prev && Math.abs(prev.y - l.y) > prev.h * 1.6) { blocks.push(b); b = null; }
    b = b ? b + " " + t : t; prev = l;
  }
  if (b) blocks.push(b);
  return blocks;
}

export class DocIndex {
  constructor(name, pageBlocksList) {
    this.name = name; this.pageCount = pageBlocksList.length; this.chunks = []; this.emptyPages = [];
    pageBlocksList.forEach((blocks, i) => {
      if (!blocks.length) { this.emptyPages.push(i + 1); return; }
      let heading = "";
      for (const blk of blocks) {
        if (isHeading(blk)) { heading = blk; continue; }
        for (const c of chunkBlock(blk)) this.chunks.push({ page: i + 1, text: c, heading, tokens: tokenize(heading + " " + c) });
      }
    });
    const n = this.chunks.length;
    this.avgdl = n ? this.chunks.reduce((s, c) => s + c.tokens.length, 0) / n : 0;
    const df = new Map();
    for (const c of this.chunks) for (const t of new Set(c.tokens)) df.set(t, (df.get(t) || 0) + 1);
    this.idf = new Map([...df].map(([t, d]) => [t, Math.log(1 + (n - d + 0.5) / (d + 0.5))]));
  }
  score(q, chunk, k1 = 1.5, b = 0.75) {
    const tf = new Map(); for (const t of chunk.tokens) tf.set(t, (tf.get(t) || 0) + 1);
    const dl = chunk.tokens.length || 1; let s = 0;
    for (const t of new Set(q)) if (tf.has(t)) { const f = tf.get(t); s += (this.idf.get(t) || 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (this.avgdl || 1))); }
    return s;
  }
  search(question, k = 4) {
    const q = tokenize(question); if (!q.length) return { hits: [], q };
    const hits = this.chunks.map(c => [this.score(q, c), c]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, k);
    return { hits, q };
  }
}

function coverage(q, textTokens, idf) {
  const qs = new Set(q); if (!qs.size) return 0;
  const w = t => idf ? (idf.get(t) ?? 1) : 1; const ts = new Set(textTokens);
  let num = 0, den = 0; for (const t of qs) { den += w(t); if (ts.has(t)) num += w(t); }
  return num / den;
}
const notFound = why => ({ answer: "I could not find this in the document. " + why, citations: [], mode: "extractive", found: false, trace: [] });

export function answerExtractive(index, question) {
  const { hits, q } = index.search(question, 5);
  if (!hits.length) return notFound("No passage shares terms with the question.");
  if (Math.max(...hits.map(([, c]) => coverage(q, c.tokens))) < 0.5) return notFound("Closest passages cover too few of the question's terms.");
  let cands = [];
  for (const [score, chunk] of hits) for (const s of splitSentences(chunk.text)) {
    const cov = coverage(q, tokenize(chunk.heading + " " + s), index.idf);
    if (cov > 0) cands.push([cov + 0.01 * score, s, chunk.page]);
  }
  cands.sort((a, b) => b[0] - a[0]);
  const floor = cands[0][0] * 0.8, picked = [], seen = new Set();
  for (const [sc, s, page] of cands) {
    if (seen.has(s) || sc < floor) continue; seen.add(s); picked.push({ page, quote: s }); if (picked.length === 3) break;
  }
  picked.sort((a, b) => a.page - b.page);
  return { answer: picked.map(c => `${c.quote} [p.${c.page}]`).join(" "), citations: picked, mode: "extractive", found: true, trace: [] };
}

// ---- tool-calling agent (OpenAI-compatible endpoint, called straight from the browser with the visitor's own key)
const SYSTEM = "You answer questions about one PDF using tools. Use search_document first; use read_page only when a passage looks relevant but cut off. Answer ONLY from tool results. Cite every claim as [p.N] using the page numbers the tools returned. If the document does not contain the answer, reply exactly: NOT_FOUND. Be concise (1-4 sentences). Do not use outside knowledge.";
const TOOLS = [
  { type: "function", function: { name: "search_document", description: "Keyword search over the PDF. Returns top passages with page numbers.", parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] } } },
  { type: "function", function: { name: "read_page", description: "Return the full text of one page (1-based).", parameters: { type: "object", properties: { page: { type: "integer" } }, required: ["page"] } } },
];
export function normalizeCitations(text) {
  text = text.replace(/\u202f/g, " ").replace(/\u2011/g, "-").replace(/[\[【(]\s*(?:p|pp|page)\.?\s*(\d+)\s*[\]】)]/gi, "[p.$1]");
  return text.replace(/(?<=\S)\s*(\[p\.\d+\])/g, " $1");
}
const MIN_QUOTE_MATCH = 0.4;
export function quotesFor(index, text, pages) {
  const out = [];
  for (const sent of splitSentences(text)) for (const p of new Set([...sent.matchAll(/\[p\.(\d+)\]/g)].map(m => +m[1]))) {
    if (!pages.has(p)) continue;
    const a = new Set(tokenize(sent.replace(/\[p\.\d+\]/g, "")));
    let best = null, score = 0;
    for (const c of index.chunks.filter(c => c.page === p)) for (const s of splitSentences(c.text)) {
      const t = new Set(tokenize(s)); let inter = 0; for (const x of a) if (t.has(x)) inter++;
      const sc = 2 * inter / ((a.size + t.size) || 1); if (sc > score) { best = s; score = sc; }
    }
    if (score < MIN_QUOTE_MATCH) best = "";
    if (best !== null && !out.some(o => o.page === p && o.quote === best)) out.push({ page: p, quote: best });
  }
  return out.sort((a, b) => a.page - b.page);
}
function runTool(index, name, args, seen, pageText) {
  if (name === "search_document") {
    const { hits } = index.search(String(args.query || ""), 4);
    hits.forEach(([, c]) => seen.add(c.page));
    return hits.length ? JSON.stringify(hits.map(([, c]) => ({ page: c.page, text: c.text }))) : "no matches";
  }
  if (name === "read_page") {
    const n = parseInt(args.page, 10);
    if (!(n >= 1 && n <= index.pageCount)) return "page out of range";
    seen.add(n); return pageText(n).slice(0, 6000);
  }
  return "unknown tool";
}
export async function httpChat(cfg, messages) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await fetch(cfg.url, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${cfg.key}` },
      body: JSON.stringify({ model: cfg.model, temperature: 0, messages, tools: TOOLS, tool_choice: "auto" }) });
    if (r.status === 429 && attempt < 2) { await new Promise(res => setTimeout(res, Math.min(parseFloat(r.headers.get("retry-after") || 3), 15) * 1000)); continue; }
    if (!r.ok) throw new Error("HTTP " + r.status);
    return (await r.json()).choices[0].message;
  }
}
export async function runAgent(index, question, chat, pageText) {
  const msgs = [{ role: "system", content: SYSTEM }, { role: "user", content: question }];
  const seen = new Set(), trace = []; let corrections = 0;
  try {
    for (let step = 0; step < 6; step++) {
      const m = await chat(msgs); const calls = m.tool_calls || [];
      if (!calls.length) {
        const text = normalizeCitations((m.content || "").trim());
        if (text.startsWith("NOT_FOUND")) return { answer: "I could not find this in the document.", citations: [], mode: "agent", found: false, trace };
        const cited = [...text.matchAll(/\[p\.(\d+)\]/g)].map(x => +x[1]);
        if (cited.length && cited.every(p => seen.has(p))) return { answer: text, citations: quotesFor(index, text, seen), mode: "agent", found: true, trace };
        if (corrections < 1) { corrections++; msgs.push({ role: "assistant", content: text }, { role: "user", content: "Rejected: cite each claim as [p.N] (square brackets) using only pages returned by your tools. Answer again, or reply NOT_FOUND." }); continue; }
        throw new Error("answer rejected: missing or unverified citation");
      }
      msgs.push({ role: "assistant", content: m.content || "", tool_calls: calls });
      for (const c of calls) {
        let res;
        try { const args = JSON.parse(c.function.arguments || "{}"); res = runTool(index, c.function.name, args, seen, pageText); trace.push({ tool: c.function.name, args }); }
        catch (e) { res = "tool error: " + e.message; }
        msgs.push({ role: "tool", tool_call_id: c.id, content: res });
      }
    }
    throw new Error("step limit reached");
  } catch (e) {
    const base = answerExtractive(index, question); base.fallback_reason = String(e.message).slice(0, 200); base.trace = trace; return base;
  }
                                            }
