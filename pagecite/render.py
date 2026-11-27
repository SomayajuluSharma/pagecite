"""Render a PDF page to PNG with cited text highlighted."""
import pymupdf as fitz


def render_page(index, page_no, quotes=(), zoom=1.6):
    if not 1 <= page_no <= index.page_count:
        raise IndexError("page out of range")
    page = index.doc[page_no - 1]
    hits = 0
    for q in quotes:
        q = " ".join(q.split())
        rects = page.search_for(q[:120])
        if not rects and len(q) > 60:        # long sentence wrapped oddly: try its halves
            rects = page.search_for(q[:60]) + page.search_for(q[-60:])
        for r in rects:
            page.add_highlight_annot(r)
            hits += 1
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    data = pix.tobytes("png")
    # remove annotations so later renders start clean
    for a in list(page.annots() or []):
        page.delete_annot(a)
    return data, hits
