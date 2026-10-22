"""The recorder: which locator a step stores, and what it resolves to later.

At record time each strategy below is tried in order, and the first one whose
locator matches exactly the target element on the record-time render (one match,
and it is the target) becomes the step's stored locator:

    testid  [data-testid="..."] and friends -- an explicit automation contract
    id      #id, only when the id does not look generated (see STRATEGY_JS)
    name    tag[name="..."] on form controls
    role    getByRole(role, { name, exact: true }) with an interactive role
    text    getByText(text, { exact: true })
    css     an nth-of-type path from the nearest stable-id ancestor, else from body

All resolution goes through Playwright itself, so role names, text matching and
visibility follow Playwright's locator semantics: role locators skip hidden
elements, text and CSS locators do not.

Every strategy that could be computed is kept as the step's fingerprint, with a
flag saying whether it uniquely identified the target at record time. The
fallback-chain baseline replays that fingerprint.
"""
from __future__ import annotations

import json
import re

STRATEGIES = ("testid", "id", "name", "role", "text", "css")

TESTID_ATTRS = ("data-testid", "data-test-id", "data-test", "data-cy", "data-qa")

INTERACTIVE_ROLES = frozenset({
    "button", "link", "checkbox", "radio", "switch", "tab", "menuitem",
    "menuitemcheckbox", "menuitemradio", "option", "textbox", "searchbox",
    "combobox", "listbox", "slider", "spinbutton", "treeitem",
})

MAX_ROLE_NAME = 120
MAX_TEXT = 80

INTACT = "INTACT"                        # resolves to the recorded element
INTACT_EQUIVALENT = "INTACT_EQUIVALENT"  # resolves to one accepted equivalent element
MISSING = "MISSING"                      # matches nothing
AMBIGUOUS = "AMBIGUOUS"                  # matches more than one element
WRONG_TARGET = "WRONG_TARGET"            # matches exactly one element, not an acceptable one
BROKEN = (MISSING, AMBIGUOUS, WRONG_TARGET)

# Builds the attribute-based selectors in the page so escaping is done by the
# browser's own CSS.escape. The stable-id test is a heuristic: it rejects ids
# that look like counters, hashes or framework-generated values.
STRATEGY_JS = r"""
(el) => {
  const looksStable = (v) => !!v && v.length <= 64 &&
    !/\d{3,}/.test(v) &&
    !v.includes(':') &&
    !/^[0-9a-f]{8,}$/i.test(v) &&
    !/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i.test(v) &&
    !/^(mui|react|ember|ng|radix|headlessui|chakra|mantine|rc|cdk|mat|tippy|downshift)[-_]/i.test(v) &&
    !/^\d/.test(v);
  const str = (v) => '"' + v.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
                              .replace(/\n/g, '\\a ') + '"';
  const tag = el.tagName.toLowerCase();
  let testid = null;
  for (const a of ['data-testid','data-test-id','data-test','data-cy','data-qa']) {
    const v = el.getAttribute(a);
    if (v && v.trim()) { testid = '[' + a + '=' + str(v) + ']'; break; }
  }
  const id = el.getAttribute('id') || '';
  const idSel = id && looksStable(id) ? '#' + CSS.escape(id) : null;
  const nameAttr = el.getAttribute('name') || '';
  const nameSel = nameAttr && ['input','select','textarea','button'].includes(tag)
    ? tag + '[name=' + str(nameAttr) + ']' : null;
  const text = (el.innerText || '').replace(/\s+/g, ' ').trim();

  const segs = [];
  let cur = el;
  while (cur && cur.nodeType === 1) {
    if (cur.tagName === 'BODY' || cur.tagName === 'HTML') { segs.unshift(cur.tagName.toLowerCase()); break; }
    const cid = cur.getAttribute('id');
    if (cur !== el && cid && looksStable(cid) &&
        document.querySelectorAll('#' + CSS.escape(cid)).length === 1) {
      segs.unshift('#' + CSS.escape(cid));
      break;
    }
    const parent = cur.parentElement;
    if (!parent) break;
    const same = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
    segs.unshift(cur.tagName.toLowerCase() + ':nth-of-type(' + (same.indexOf(cur) + 1) + ')');
    cur = parent;
  }
  return JSON.stringify({ testid, idSel, nameSel, text, css: segs.join(' > ') });
}
"""

_ARIA_HEAD = re.compile(r'^- ([a-z]+)(?: "((?:[^"\\]|\\.)*)")?')


def parse_aria_head(snapshot: str) -> tuple[str | None, str]:
    """Role and accessible name from the first line of `locator.aria_snapshot()`."""
    if not snapshot:
        return None, ""
    m = _ARIA_HEAD.match(snapshot.splitlines()[0])
    if not m or m.group(1) in ("text", "generic", "none", "presentation"):
        return None, ""
    raw = m.group(2)
    if raw is None:
        return m.group(1), ""
    try:
        name = json.loads('"' + raw + '"')
    except ValueError:
        name = raw.replace('\\"', '"')
    return m.group(1), name


def display(loc: dict) -> str:
    """Playwright-style rendering of a stored locator, for logs and docs."""
    q = lambda s: "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if loc["kind"] == "role":
        return f"getByRole({q(loc['role'])}, {{ name: {q(loc['name'])}, exact: true }})"
    if loc["kind"] == "text":
        return f"getByText({q(loc['value'])}, {{ exact: true }})"
    return f"locator({q(loc['value'])})"


def _playwright_locator(page, loc: dict):
    if loc["kind"] == "role":
        return page.get_by_role(loc["role"], name=loc["name"], exact=True)
    if loc["kind"] == "text":
        return page.get_by_text(loc["value"], exact=True)
    return page.locator(loc["value"])


async def resolve(page, loc: dict) -> list[str] | None:
    """Tracking ids of every element the locator matches, in document order.

    None means the locator could not be evaluated at all (e.g. a selector the
    engine rejects). Callers keep that apart from "matches nothing".
    """
    try:
        return json.loads(await _playwright_locator(page, loc).evaluate_all(
            "els => JSON.stringify(els.map(e => e.getAttribute('data-sh-track')))"))
    except Exception:
        return None


async def candidate_locators(page, track: str) -> list[tuple[str, dict]]:
    handle = page.locator(f'[data-sh-track="{track}"]')
    info = json.loads(await handle.evaluate(STRATEGY_JS, timeout=5000))
    try:
        snap = await handle.aria_snapshot(timeout=3000)
    except Exception:
        snap = ""
    role, name = parse_aria_head(snap)

    out = []
    if info["testid"]:
        out.append(("testid", {"kind": "css", "value": info["testid"]}))
    if info["idSel"]:
        out.append(("id", {"kind": "css", "value": info["idSel"]}))
    if info["nameSel"]:
        out.append(("name", {"kind": "css", "value": info["nameSel"]}))
    if role in INTERACTIVE_ROLES and name and len(name) <= MAX_ROLE_NAME:
        out.append(("role", {"kind": "role", "role": role, "name": name}))
    if info["text"] and len(info["text"]) <= MAX_TEXT:
        out.append(("text", {"kind": "text", "value": info["text"]}))
    if info["css"]:
        out.append(("css", {"kind": "css", "value": info["css"]}))
    return out


async def record(page, track: str) -> dict | None:
    """The stored locator for one element, plus its fingerprint. None if nothing works."""
    fingerprint, primary = [], None
    try:
        cands = await candidate_locators(page, track)
    except Exception:  # the element went away (live pages re-render)
        return None
    for strategy, loc in cands:
        hits = await resolve(page, loc)
        unique = hits == [track]
        fingerprint.append({"strategy": strategy, "locator": loc, "unique": unique,
                            "n_matches": None if hits is None else len(hits)})
        if unique and primary is None:
            primary = {"strategy": strategy, "locator": loc}
    if primary is None:
        return None
    return {"primary": primary["strategy"], "locator": primary["locator"],
            "display": display(primary["locator"]), "fingerprint": fingerprint}


def classify(hits: list[str] | None, lineage: str | None, accepted: set) -> str:
    """Outcome of replaying a stored locator on a changed page.

    `lineage` is the recorded element's own id if it survived, `accepted` the
    full set of elements that would perform the step correctly (the element
    itself plus any functionally equivalent controls).
    """
    if not hits:
        return MISSING
    if len(hits) > 1:
        return AMBIGUOUS
    if lineage is not None and hits[0] == lineage:
        return INTACT
    if hits[0] in accepted:
        return INTACT_EQUIVALENT
    return WRONG_TARGET
