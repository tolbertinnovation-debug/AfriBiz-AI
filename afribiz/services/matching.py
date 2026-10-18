"""Forgiving name matching for items and customers ("tomatoes" == "Tomato", "mama ngozi" == "Mama Ngozi")."""

from __future__ import annotations

import difflib
import re


def normalize_name(name: str) -> str:
    text = re.sub(r"[^\w\s'-]", " ", (name or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text


def singular(word: str) -> str:
    for suffix, repl in (("ies", "y"), ("oes", "o"), ("ses", "s"), ("xes", "x")):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + repl
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def key(name: str) -> str:
    return " ".join(singular(w) for w in normalize_name(name).split())


def best_match(query: str, candidates: dict[int, str], cutoff: float = 0.82) -> int | None:
    """Return the id of the candidate whose name best matches `query`, or None."""
    if not candidates:
        return None
    wanted = key(query)
    keyed = {cid: key(name) for cid, name in candidates.items()}
    for cid, k in keyed.items():
        if k == wanted:
            return cid
    # One name containing the other ("rice" vs "rice 5kg") counts when the shorter is a whole word.
    contains = [cid for cid, k in keyed.items() if re.search(rf"\b{re.escape(wanted)}\b", k)]
    if len(contains) == 1:
        return contains[0]
    names = list(keyed.values())
    close = difflib.get_close_matches(wanted, names, n=1, cutoff=cutoff)
    if close:
        for cid, k in keyed.items():
            if k == close[0]:
                return cid
    return None


def tidy_display(name: str) -> str:
    """'  mama   NGOZI ' -> 'Mama Ngozi'; keeps short all-caps tokens like 'MTN' intact."""
    words = normalize_name(name).split()
    original = {w.lower(): w for w in (name or "").split()}
    out = []
    for w in words:
        raw = original.get(w, w)
        out.append(raw if raw.isupper() and len(raw) <= 4 else w.capitalize())
    return " ".join(out)
