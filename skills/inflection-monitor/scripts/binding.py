#!/usr/bin/env python3
"""The words that bind a deep-dive paragraph to its row.

Shared by the linter (gate 36's "the paragraph must touch its own line") and
the renderer's narrative template, which prints the words for each dive BEFORE
the prose is written, so the model knows which words count before writing
rather than by failing the gate.

    from binding import required_binding_words
    required_binding_words({"display": "AUV (12 Month Trailing)"})
    -> {'auv', 'average', 'unit', 'volume', 'month', 'trailing'}

What counts, deliberately weak: ONE shared word anywhere in the paragraph, and
paraphrase counts. The words are the content words of the row's display name
and its source name, INCLUDING words inside parentheses ("Trailing" in
"AUV (12 Month Trailing)" counts, so a paragraph about "trailing average unit
volume" passes), except basis and unit decoration
("Ex-1x Items", "Cents", "%", "adj"), which identifies no line. Common
abbreviations bring their spelled-out words ("AUV" -> average unit volume),
because a paraphrase of an acronym is the acronym spelled out.
"""

import re

STOP = {"the", "a", "an", "its", "this", "that", "and", "of", "in", "on",
        "for", "total", "net", "consolidated", "adjusted", "per", "share"}
DECORATION = {"ex", "items", "item", "excl", "incl", "excluding", "including", "adj",
              "cents", "cent", "usd", "bps", "pp", "non", "gaap", "mn", "bn", "eop",
              "bop", "avg", "calculated", "insync", "reported", "the"}
ABBREVIATIONS = {
    "auv": ("average", "unit", "volume", "volumes"),
    "sss": ("same", "store", "comparable", "comps"),
    "comps": ("comparable", "sales"),
    "eps": ("earnings",),
    "ebit": ("operating", "earnings"),
    "ebitda": ("earnings",),
    "nim": ("interest", "margin"),
    "nii": ("interest", "income"),
    "fcf": ("free", "cash", "flow"),
    "ocf": ("operating", "cash", "flow"),
    "cogs": ("cost", "goods", "sold"),
    "sbc": ("stock", "compensation"),
    "opex": ("operating", "expense", "expenses"),
    "capex": ("capital", "spending", "expenditure"),
    "etr": ("tax", "rate"),
    "dio": ("inventory", "days"),
    "dso": ("receivable", "days"),
    "arpu": ("revenue", "user"),
    "casm": ("cost", "seat", "mile"),
    "rasm": ("revenue", "seat", "mile"),
    "ffo": ("funds", "operations"),
    "noi": ("operating", "income"),
    "rpo": ("performance", "obligations"),
    "cet1": ("capital",),
    "roe": ("return", "equity"),
    "g&a": ("general", "administrative"),
}


def _words(text):
    t = (text or "").lower().replace("%", " ")
    t = re.sub(r"\bex-\s?1x\b|\bex-\s?\d+x\b", " ", t)
    return [w for w in re.findall(r"[a-z][a-z0-9&]{1,}", t)]


def required_binding_words(row):
    names = [row.get("display"), row.get("name")] if isinstance(row, dict) else [row]
    out = set()
    for nm in names:
        for w in _words(nm or ""):
            if w in ABBREVIATIONS:
                out.add(w)
                out.update(ABBREVIATIONS[w])
                continue
            if len(w) >= 3 and w not in STOP and w not in DECORATION:
                out.add(w)
                out.update(_number_forms(w))
    return out


def _number_forms(w):
    if len(w) < 4 or not w.isalpha():
        return set()
    out = set()
    if w.endswith("ies") and len(w) > 4:
        out.add(w[:-3] + "y")
    elif w.endswith(("ses", "xes", "zes", "ches", "shes")):
        out.add(w[:-2])
    elif w.endswith("s") and not w.endswith("ss"):
        out.add(w[:-1])
    else:
        if w.endswith("y") and w[-2] not in "aeiou":
            out.add(w[:-1] + "ies")
        elif w.endswith(("s", "x", "z", "ch", "sh")):
            out.add(w + "es")
        else:
            out.add(w + "s")
    return out
