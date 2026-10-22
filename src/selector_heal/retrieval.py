"""Candidate retrieval and the equivalence policy.

`candidate_score` is a hand-written similarity between the recorded step and an
element on the changed page. It is deliberately not a hard role filter: a
<button> that became <a role=button> must still be retrievable. The top-K
elements by this score are what the model and the retrieval baselines choose
from; the fallback chain replays locators on the whole page instead.
"""
from __future__ import annotations

import re

ROLE_FAMILY = {
    "button": {"button", "a", "input", "summary"},
    "a": {"a", "button"},
    "input": {"input", "textarea", "select"},
    "textarea": {"input", "textarea"},
    "select": {"select", "input"},
    "label": {"label", "span", "div"},
    "summary": {"summary", "button"},
}
DEAD_HREF = {"", "#", "javascript:void(0)", "javascript:void(0);", "javascript:;"}
VIEWPORT = (1440.0, 900.0)


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def tokens(s) -> set:
    return set(re.findall(r"[\w가-힣]+", norm(s)))


def label(e: dict) -> str:
    """Visible label, compared case-insensitively (CSS text-transform changes innerText case)."""
    return norm(e.get("name") or e.get("text"))


def real_dest(e: dict) -> str:
    """Where a link goes (resolved against the page), or '' when it points nowhere."""
    h = str(e.get("href") or "").strip()
    low = h.lower()
    if low in DEAD_HREF or low.startswith("javascript:"):
        return ""
    return str(e.get("hrefAbs") or h)


def equivalent(a: dict, b: dict) -> bool:
    """Policy-defined equivalence: two controls a recorded step could use interchangeably.

    Links with the same resolved destination are equivalent whatever their label:
    a product card's image, title and "View" link all navigate to one page.
    Other controls need the same visible label and the same id or test id (with
    the attribute that carried it). Two "Delete" buttons with nothing else in
    common are never merged, because static HTML cannot tell whether they act on
    the same record, and a dead href ("#", javascript:) says nothing.
    Destinations and identifiers are compared exactly.
    """
    da = real_dest(a)
    if da and da == real_dest(b):
        return True
    la = label(a)
    if not la or la != label(b):
        return False
    ia = str(a.get("id") or "").strip()
    if ia and ia == str(b.get("id") or "").strip():
        return True
    ta = (a.get("testidAttr") or "", str(a.get("testid") or "").strip())
    return bool(ta[1]) and ta == (b.get("testidAttr") or "", str(b.get("testid") or "").strip())


def candidate_score(old: dict, new: dict) -> float:
    s = 0.0
    fam = ROLE_FAMILY.get(old["tag"], {old["tag"]})
    if new["tag"] == old["tag"]:
        s += 2.0
    elif new["tag"] in fam:
        s += 1.2
    if old.get("role") and old.get("role") == new.get("role"):
        s += 1.0

    on, nn = tokens(old.get("name")), tokens(new.get("name"))
    if on and nn:
        s += 3.5 * len(on & nn) / len(on | nn)
        if norm(old.get("name")) == norm(new.get("name")):
            s += 2.0
    ot, nt = tokens(old.get("text")), tokens(new.get("text"))
    if ot and nt:
        s += 2.0 * len(ot & nt) / len(ot | nt)

    for k, w in (("testid", 3.0), ("nameAttr", 2.0), ("id", 1.5), ("href", 1.0)):
        a, b = old.get(k), new.get(k)
        if a and b and a == b:
            s += w

    ob, nb = old.get("bbox"), new.get("bbox")
    if ob and nb:
        dx = abs(ob[0] - nb[0]) / VIEWPORT[0]
        dy = abs(ob[1] - nb[1]) / VIEWPORT[1]
        s += 2.0 * max(0.0, 1.0 - (dx + dy))
        if ob[2] and nb[2]:
            s += 0.8 * max(0.0, 1.0 - abs(ob[2] - nb[2]) / max(ob[2], nb[2]))

    if old.get("heading") and old.get("heading") == new.get("heading"):
        s += 1.5
    oa, na = set(old.get("ancestors") or []), set(new.get("ancestors") or [])
    if oa and na:
        s += 1.5 * len(oa & na) / max(len(oa | na), 1)
    return s


def build_candidates(old: dict, elements: list, k: int) -> list[tuple[float, dict]]:
    """Top-k (score, element) pairs. The sort is stable, so ties keep document order."""
    scored = [(candidate_score(old, e), e) for e in elements]
    scored.sort(key=lambda x: -x[0])
    return scored[:k]
