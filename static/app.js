let doc = null, page = 1, quotes = [];
const $ = id => document.getElementById(id);
function show(p, q) {
  if (!doc) return;
  page = Math.min(Math.max(1, p), doc.pages);
  if (q !== undefined) quotes = q;
  const qs = quotes.map(x => "q=" + encodeURIComponent(x)).join("&");
  $("pageimg").src = `/api/doc/${doc.id}/page/${page}.png?${qs}`;
  $("pageimg").hidden = false; $("empty").hidden = true; $("viewbar").hidden = false;
  $("pageinfo").textContent = `Page ${page} / ${doc.pages}`;
}
function add(cls, node) { const d = document.createElement("div"); d.className = "msg " + cls; d.append(node); $("log").append(d); $("log").scrollTop = 1e9; return d; }
$("file").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const r = await fetch("/api/upload", {method: "POST", headers: {"X-Filename": f.name}, body: f});
  const j = await r.json();
  if (!r.ok) return add("bot nf", document.createTextNode("Upload failed: " + j.error));
  doc = j; $("docname").textContent = j.name; $("q").disabled = $("send").disabled = false;
  let note = `Loaded "${j.name}" (${j.pages} pages).`;
  if (j.empty_pages.length) note += ` No text on page(s) ${j.empty_pages.join(", ")} (scanned images are not supported yet).`;
  add("bot", document.createTextNode(note)); show(1, []);
};
$("prev").onclick = () => show(page - 1, []); $("next").onclick = () => show(page + 1, []);
$("form").onsubmit = async e => {
  e.preventDefault(); const q = $("q").value.trim(); if (!q || !doc) return;
  $("q").value = ""; add("user", document.createTextNode(q));
  const r = await fetch("/api/ask", {method: "POST", body: JSON.stringify({doc: doc.id, question: q, mode: $("mode").value})});
  const j = await r.json();
  if (!r.ok) return add("bot nf", document.createTextNode(j.error));
  const box = document.createElement("div"); box.append(j.answer.replace(/\s*\[p\.\d+\]/g, ""));
  const wrap = add(j.found ? "bot" : "bot nf", box);
  const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = j.mode === "agent" ? "AI agent" : "extractive"; box.append(badge);
  if (j.trace && j.trace.length) { const t = document.createElement("div"); t.className = "trace"; t.textContent = "Tool calls: " + j.trace.map(x => x.tool + "(" + Object.values(x.args).join(", ") + ")").join(" > "); wrap.append(t); }
  if (j.fallback_reason) { const w = document.createElement("div"); w.className = "warn"; w.textContent = "AI mode unavailable (" + j.fallback_reason + "); showing extractive answer."; wrap.append(w); }
  if (!j.found) return;
  const byPage = {};
  j.citations.forEach(c => { (byPage[c.page] ||= []); if (c.quote) byPage[c.page].push(c.quote); });
  const row = document.createElement("div");
  Object.keys(byPage).forEach(p => {
    const b = document.createElement("span"); b.className = "cite"; b.textContent = "p." + p;
    b.onclick = () => show(+p, byPage[p]); row.append(b);
  });
  wrap.append(row);
  j.citations.forEach(c => { const s = document.createElement("div"); s.className = "src"; s.textContent = c.quote ? `p.${c.page}: "${c.quote}"` : `p.${c.page}: supported by a table or figure on this page (no sentence quote)`; wrap.append(s); });
  const first = Object.keys(byPage)[0]; show(+first, byPage[first]);
};
