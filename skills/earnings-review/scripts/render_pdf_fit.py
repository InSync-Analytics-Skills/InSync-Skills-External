"""Footer fitting for render_pdf.py (audit class 16): the identifying first segment (TICKER nQyy Earnings Review) and the last
segment (consensus and market DATES) are never cut. The source list in between is shortened first, then dropped; if the text
still does not fit it goes onto two lines. Returns (lines, changed) and never elides with '...'."""

def _len(s, size=6.5):
    try:
        try: import pymupdf as fitz
        except ImportError: import fitz
        return fitz.get_text_length(s, fontname="helv", fontsize=size)
    except Exception:
        return len(s) * size * 0.5

def fit_footer(text, avail, prefix="InSync Analytics  |  ", size=6.5):
    segs = [s.strip() for s in text.split("|")]
    def join(ss): return prefix + "  |  ".join(ss)
    if _len(join(segs), size) <= avail: return [join(segs)]
    first, last, mid = segs[0], segs[-1], segs[1:-1]
    for alt in (["Source: company documents, InSync"], ["Source: company, InSync"], []):
        cand = [first] + (alt if mid else []) + ([last] if len(segs) > 1 else [])
        if _len(join(cand), size) <= avail: return [join(cand)]
    return [join([first] + mid), "  |  ".join([last])]
