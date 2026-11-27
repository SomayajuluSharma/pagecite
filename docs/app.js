import * as pdfjs from "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.min.mjs";
import { DocIndex, pageBlocks, answerExtractive, runAgent, httpChat } from "./pagecite.js";
import { SAMPLE_PDF_BASE64 } from "./sample.js";
pdfjs.GlobalWorkerOptions.workerSrc = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.worker.min.mjs";

const $ = id => document.getElementById(id);
const LLM = { url: "https://api.groq.com/openai/v1/chat/completions", model: "openai/gpt-oss-120b" };
let pdf = null, index = null, texts = [], page = 1, quotes = [], renderTok = 0;

const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
function add(cls, ...nodes) { const d = el("div", "msg " + cls); d.append(...nodes); $("log").append(d); $("log").scrollTop = 1e9; return d; }
$("key").oninput = () => { $("keystate").textContent = $("key").value.trim() ? "AI agent mode on" : ""; };

async function load(data, name) {
  pdf = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;   // untrusted PDF: no eval-based font paths
  const blocksPerPage = []; texts = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const tc = await (await pdf.getPage(i)).getTextContent();
    blocksPerPage.push(pageBlocks(tc.items));
    texts.push(tc.items.map(it => ("str" in it ? it.str + (it.hasEOL ? " " : "") : "")).join(""));
  }
  index = new DocIndex(name, blocksPerPage);
  $("docname").textContent = name; $("q").disabled = $("send").disabled = false;
  let note = `Loaded "${name}" (${pdf.numPages} pages).`;
  if (index.emptyPages.length) note += ` No text on page(s) ${index.emptyPages.join(", ")} (scanned images are not supported).`;
  add("bot", document.createTextNode(note)); show(1, []);
}

async function show(p, q) {
  if (!pdf) return;
  page = Math.min(Math.max(1, p), pdf.numPages); if (q !== undefined) quotes = q;
  const tok = ++renderTok;
  const pg = await pdf.getPage(page), vp = pg.getViewport({ scale: 1.5 });
  const cv = $("canvas"); cv.width = vp.width; cv.height = vp.height;
  await pg.render({ canvasContext: cv.getContext("2d"), viewport: vp }).promise;
  if (tok !== renderTok) return;
  $("pagewrap").hidden = false; $("empty").hidden = true; $("viewbar").hidden = false;
  $("pageinfo").textContent = `Page ${page} / ${pdf.numPages}`;
  const hl = $("hl"); hl.replaceChildren();
  const k = cv.clientWidth / vp.width;   // canvas is shrunk by CSS; scale overlay boxes the same way
  const tc = await pg.getTextContent(); let n = 0;
  for (const rect of findRects(tc.items, quotes, vp)) {
    const d = el("div"); Object.assign(d.style, { left: rect.x * k + "px", top: rect.y * k + "px", width: rect.w * k + "px", height: rect.h * k + "px" }); hl.append(d); n++;
  }
  hl.dataset.count = n;
}

// Locate each quote in the page's concatenated text and highlight the text items it overlaps.
function findRects(items, qs, vp) {
  const compact = s => s.replace(/\s+/g, "");          // PDF text is fragmented ("2 . 0"): match ignoring whitespace
  let full = "", spans = [];
  for (const it of items) { if (!("str" in it)) continue; const s = compact(it.str); if (!s) continue; spans.push({ it, start: full.length, end: full.length + s.length }); full += s; }
  const out = [];
  for (const q of qs) {
    const needle = compact(q); if (!needle) continue;
    let at = full.indexOf(needle), len = needle.length;
    if (at < 0 && needle.length > 80) {   // text differs slightly: anchor on the start and the end of the quote
      const s0 = full.indexOf(needle.slice(0, 40)), e0 = full.indexOf(needle.slice(-30), Math.max(s0, 0));
      if (s0 >= 0 && e0 > s0) { at = s0; len = e0 + 30 - s0; } else if (s0 >= 0) { at = s0; len = 60; }
    }
    if (at < 0) continue;
    const end = at + len;
    for (const sp of spans) if (sp.end > at && sp.start < end) {
      const [a, b, , , e, f] = pdfjs.Util.transform(vp.transform, sp.it.transform);
      const h = Math.hypot(a, b) || 10, w = sp.it.width * vp.scale;
      out.push({ x: e, y: f - h * 0.85, w, h: h * 1.1 });
    }
  }
  return out;
}

function pageText(n) { return texts[n - 1].replace(/\s+/g, " ").trim(); }

$("file").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  if (f.size > 30 * 1024 * 1024) return add("bot nf", document.createTextNode("File too large (30 MB max)."));
  try { await load(new Uint8Array(await f.arrayBuffer()), f.name); } catch { add("bot nf", document.createTextNode("Could not read this PDF.")); }
};
$("sample").onclick = async () => {
  const bin = atob(SAMPLE_PDF_BASE64); const u8 = Uint8Array.from(bin, c => c.charCodeAt(0));
  await load(u8, "expense_policy_sample.pdf");
};
$("prev").onclick = () => show(page - 1, []); $("next").onclick = () => show(page + 1, []);

$("form").onsubmit = async e => {
  e.preventDefault(); const q = $("q").value.trim(); if (!q || !index) return;
  $("q").value = ""; add("user", document.createTextNode(q));
  const key = $("key").value.trim();
  const j = key ? await runAgent(index, q, m => httpChat({ ...LLM, key }, m), pageText) : answerExtractive(index, q);
  const box = el("div", "", j.answer.replace(/\s*\[p\.\d+\]/g, ""));          // textContent only: PDF text is never parsed as HTML
  box.append(el("span", "badge", j.mode === "agent" ? "AI agent" : "extractive (no AI)"));
  const wrap = add(j.found ? "bot" : "bot nf", box);
  if (j.trace?.length) wrap.append(el("div", "trace", "Tool calls: " + j.trace.map(x => `${x.tool}(${Object.values(x.args).join(", ")})`).join(" > ")));
  if (j.fallback_reason) wrap.append(el("div", "warn", `AI mode unavailable (${j.fallback_reason}); showing extractive answer.`));
  if (!j.found) return;
  const byPage = {};
  j.citations.forEach(c => { (byPage[c.page] ||= []); if (c.quote) byPage[c.page].push(c.quote); });
  const row = el("div");
  for (const p of Object.keys(byPage)) { const b = el("span", "cite", "p." + p); b.onclick = () => show(+p, byPage[p]); row.append(b); }
  wrap.append(row);
  j.citations.forEach(c => wrap.append(el("div", "src", c.quote ? `p.${c.page}: "${c.quote}"` : `p.${c.page}: supported by a table or figure on this page (no sentence quote)`)));
  const first = Object.keys(byPage)[0]; if (first) show(+first, byPage[first]);
};
window.__pagecite = { get index() { return index; } };
