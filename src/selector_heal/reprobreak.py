"""Derive change-axis bundle frequencies from ReproBreak's locator pairs.

ReproBreak (Ruhr University Bochum, MIT) lists 9,572 old/new locator pairs mined
from Cypress and Playwright test suites. These are test-code strings, not DOM
snapshots, so they cannot be replayed here. What they do give is how often
different kinds of change happen together, which is what `mutate.py` samples.

    python -m selector_heal reprobreak --csv ReproBreak/locator_analysis.csv

The mapping from locator diff to axis is coarse (regexes over selector strings
and the getBy* / cy.contains APIs) and only axes the mutation engine can apply
are sampled, so the result is a projection of the locator changes, not a
measurement of how often real UIs change in each way.
"""
from __future__ import annotations

import collections
import csv
import json
import re
from pathlib import Path

Q = r"""['"`]"""
TEXT_API = re.compile(r"(?:getByText|getByLabel|getByPlaceholder|getByTitle|getByAltText)\(\s*"
                      + Q + r"([^'\"`]+)")
TESTID_API = re.compile(r"getByTestId\(\s*" + Q + r"([^'\"`]+)")
ROLE_API = re.compile(r"getByRole\(\s*" + Q + r"(\w+)" + Q + r"(.*)", re.S)
CONTAINS = re.compile(r"\bcontains\(\s*" + Q + r"([^'\"`]*)" + Q
                      + r"(?:\s*,\s*" + Q + r"([^'\"`]*)" + Q + r")?")


def strip_wrapper(loc: str) -> str:
    """The selector payload of cy.get('...'), page.locator('...') and similar."""
    s = loc.strip()
    m = re.match(r"^(?:cy|page|await page|this\.page)?\.?\w*\(?\s*(['\"`])(.+?)\1", s)
    if m:
        return m.group(2)
    m = re.search(r"(['\"`])([^'\"`]{2,}?)\1", s)
    return m.group(2) if m else s


def _css_tokens(sel: str, t: dict):
    t["ids"] |= set(re.findall(r"#([\w-]+)", sel))
    t["classes"] |= set(re.findall(r"\.([\w-]+)", sel))
    for attr, val in re.findall(r"\[([\w-]+)\s*[~|^$*]?=\s*['\"]?([^'\"\]]*)['\"]?", sel):
        if attr.startswith("data-test") or attr == "data-cy":
            t["testids"].add(val)
        else:
            t["attrs"].add(f"{attr}={val}")
    t["attrs"] |= set(re.findall(r"\[([\w-]+)\]", sel))
    # Playwright text and role engines inside a selector string
    t["text"] |= {x.strip() for x in re.findall(r"(?:text=|:has-text\(|:text\()\s*['\"]?([^'\")\]]+)", sel)}
    for role, name in re.findall(r"role=(\w+)(?:\[name=['\"]([^'\"]*)['\"])?", sel):
        t["roles"].add(role)
        if name:
            t["text"].add(name)
    plain = re.sub(r"\[[^\]]*\]|\([^)]*\)|role=\w+|text=\S+", " ", sel)
    t["tags"] |= set(re.findall(r"(?:^|[\s>+~])([a-z][a-z0-9-]*)(?![\w-])", plain.lower()))
    t["nth"] |= set(re.findall(r"nth-(?:child|of-type)\((\d+)\)", sel))
    t["depth"] = len(re.findall(r"[>\s+~]", plain.strip()))


def parse(loc: str) -> tuple[dict, str]:
    """Token sets of one locator and the strategy it uses (css, testid, text, role)."""
    t = {k: set() for k in ("ids", "classes", "testids", "attrs", "text", "tags", "nth", "roles")}
    t["depth"] = 0
    m = TESTID_API.search(loc)
    if m:
        t["testids"].add(m.group(1))
        return t, "testid"
    m = TEXT_API.search(loc)
    if m:
        t["text"].add(m.group(1).strip())
        return t, "text"
    m = ROLE_API.search(loc)
    if m:
        t["roles"].add(m.group(1))
        n = re.search(r"name\s*:\s*" + Q + r"([^'\"`]+)", m.group(2))
        if n:
            t["text"].add(n.group(1).strip())
        return t, "role"
    m = CONTAINS.search(loc)
    if m:
        if m.group(2) is not None:      # cy.contains(selector, text)
            _css_tokens(m.group(1), t)
            t["text"].add(m.group(2).strip())
        else:
            t["text"].add(m.group(1).strip())
        return t, "text"
    # an API called with a variable instead of a literal still tells the strategy
    for api, kind in (("getByTestId", "testid"), ("getByRole", "role"), ("getByText", "text"),
                      ("getByLabel", "text"), ("getByPlaceholder", "text")):
        if api + "(" in loc:
            return t, kind
    _css_tokens(strip_wrapper(loc), t)
    return t, ("testid" if t["testids"] else "css")


def classify(old: str, new: str) -> set:
    to, so = parse(old)
    tn, sn = parse(new)
    axes = set()
    if to["ids"] != tn["ids"]:
        axes.add("ID_CHANGE" if (to["ids"] and tn["ids"]) else
                 ("ID_REMOVED" if to["ids"] else "ID_ADDED"))
    if to["classes"] != tn["classes"]:
        axes.add("CLASS_CHURN")
    if to["testids"] and tn["testids"] and to["testids"] != tn["testids"]:
        axes.add("TESTID_CHANGE")
    if to["attrs"] != tn["attrs"]:
        axes.add("ATTR_CHANGE")
    # a label change needs a label on both sides; gaining one is a strategy change
    if to["text"] and tn["text"] and to["text"] != tn["text"]:
        axes.add("COPY_CHANGE")
    if (to["tags"] | to["roles"]) != (tn["tags"] | tn["roles"]) and (to["tags"] or to["roles"]) \
            and (tn["tags"] or tn["roles"]):
        axes.add("TAG_SWAP")
    if to["nth"] != tn["nth"]:
        axes.add("POSITION_CHANGE")
    if so == sn == "css" and to["depth"] != tn["depth"]:
        axes.add("DEPTH_CHANGE")
    if so != sn:
        axes.add(f"STRATEGY_{so.upper()}_TO_{sn.upper()}")
    if not axes:
        axes.add("OTHER_MINOR")
    return axes


def copy_kind(old_names: set, new_names: set) -> str | None:
    """How a label changed: case-only, extended/shortened, partly reworded, or replaced."""
    if len(old_names) != 1 or len(new_names) != 1:
        return None
    a, b = next(iter(old_names)), next(iter(new_names))
    if a == b:
        return None
    if a.lower() == b.lower():
        return "case"
    if a.lower() in b.lower() or b.lower() in a.lower():
        return "partial"
    wa, wb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return "substantial" if wa & wb else "replace"


def analyze(csv_path: Path) -> dict:
    from .mutate import AXES
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    axis_count, bundle_count, kinds = collections.Counter(), collections.Counter(), collections.Counter()
    sampled = 0
    for r in rows:
        axes = classify(r["old_locator"], r["new_locator"])
        axis_count.update(axes)
        bundle_count[tuple(sorted(axes))] += 1
        if axes & set(AXES):
            sampled += 1
        if "COPY_CHANGE" in axes:
            k = copy_kind(parse(r["old_locator"])[0]["text"], parse(r["new_locator"])[0]["text"])
            if k:
                kinds[k] += 1
    n_kinds = sum(kinds.values())
    return {
        "source": "ReproBreak locator_analysis.csv (https://github.com/rub-sq/ReproBreak, commit c603a0c)",
        "total": len(rows),
        "pairs_with_a_supported_axis": sampled,
        "axes": dict(axis_count.most_common()),
        "bundles": {" + ".join(b): c for b, c in bundle_count.most_common()},
        "copy_change_kinds_counts": dict(kinds.most_common()),
        "copy_change_kinds": {k: round(v / n_kinds, 4) for k, v in kinds.most_common()} if n_kinds else {},
    }


def main(csv_path: Path, out: Path):
    d = analyze(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    multi = sum(c for b, c in d["bundles"].items() if " + " in b)
    print(f"{d['total']} locator pairs; {d['pairs_with_a_supported_axis']} map to an axis the "
          f"mutation engine applies; {multi} classified on more than one axis")
    print("axes:", d["axes"])
    print("copy-change kinds:", d["copy_change_kinds_counts"])
    print(f"wrote {out}")
