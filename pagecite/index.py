"""PDF text extraction, chunking and BM25 retrieval. No network, no model."""
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf as fitz  # PyMuPDF

STOP = set("""a an and are as at be by for from has have how i in is it its of on or that the
this to was were what when where which who whom whose why will with does do did can could should
would about into than then there their they them we you your our not no yes""".split())

TOKEN = re.compile(r"[a-z0-9][a-z0-9.%,₹$-]*[a-z0-9%]|[a-z0-9]")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])|\n{2,}")


def tokenize(text):
    toks = []
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 50,000 -> 50000
    for t in TOKEN.findall(text.lower()):
        t = t.strip(".,-")
        if t and t not in STOP:
            toks.append(stem(t))
    return toks


def stem(t):
    for suf in ("ing", "edly", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3 and not t.isdigit():
            r = t[: -len(suf)]
            if len(r) > 3 and r[-1] == r[-2] and r[-1] not in "lsz":
                r = r[:-1]  # submitt -> submit
            return r
    return t


def split_sentences(text):
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text.replace("\r", "")) if "\n\n" not in text else text
    parts = [re.sub(r"\s+", " ", p).strip() for p in SENT.split(text)]
    return [p for p in parts if len(p) > 3]


@dataclass
class Chunk:
    page: int          # 1-based
    text: str
    tokens: list = field(default_factory=list)
    heading: str = ""


class DocIndex:
    def __init__(self, pdf_bytes, name="document.pdf"):
        self.name = name
        self.pdf_bytes = pdf_bytes
        self.doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        self.page_count = self.doc.page_count
        self.chunks = []
        self.empty_pages = []
        for i, page in enumerate(self.doc, start=1):
            blocks = [b[4].strip() for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            if not blocks:
                self.empty_pages.append(i)  # scanned/image-only: no OCR in this version
                continue
            heading = ""
            for blk in blocks:
                if self._is_heading(blk):
                    heading = " ".join(blk.split())
                    continue
                for c in self._chunk(blk):
                    self.chunks.append(Chunk(i, c, tokenize(heading + " " + c), heading))
        self._build()

    @staticmethod
    def _is_heading(block):
        t = " ".join(block.split())
        return len(t.split()) <= 8 and not t.endswith((".", "!", "?", ":", ";", ","))

    @staticmethod
    def _chunk(text, target=350):
        """Group sentences into ~target-char chunks, never crossing a page."""
        out, cur = [], ""
        for s in split_sentences(text):
            if cur and len(cur) + len(s) > target:
                out.append(cur)
                cur = ""
            cur = (cur + " " + s).strip()
        if cur:
            out.append(cur)
        return out

    def _build(self):
        n = len(self.chunks)
        self.avgdl = (sum(len(c.tokens) for c in self.chunks) / n) if n else 0
        df = Counter()
        for c in self.chunks:
            df.update(set(c.tokens))
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, q_tokens, chunk, k1=1.5, b=0.75):
        tf = Counter(chunk.tokens)
        dl = len(chunk.tokens) or 1
        s = 0.0
        for t in set(q_tokens):
            if t in tf:
                f = tf[t]
                s += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (self.avgdl or 1)))
        return s

    def search(self, question, k=4):
        q = tokenize(question)
        if not q:
            return [], q
        scored = [(self.score(q, c), c) for c in self.chunks]
        scored = [x for x in scored if x[0] > 0]
        scored.sort(key=lambda x: -x[0])
        return scored[:k], q
