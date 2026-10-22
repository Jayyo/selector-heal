import asyncio

import pytest

from selector_heal import locator as L

pytest.importorskip("playwright")

PAGE = """<!DOCTYPE html><html><body>
<nav><a data-sh-track="c1" href="/docs">Docs</a><a data-sh-track="c2" href="/api">API</a></nav>
<button data-sh-track="c3" data-testid="save-btn">Save</button>
<button data-sh-track="c4" id="cancel">Cancel</button>
<button data-sh-track="c5" id="order-1024-view">View</button>
<button data-sh-track="c6" aria-label="Close dialog">x</button>
<input data-sh-track="c7" name="email" placeholder="Email">
<footer><a data-sh-track="c8" href="/docs">Docs</a></footer>
</body></html>"""


def run(coro):
    return asyncio.run(coro)


async def _with_page(html, fn):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(java_script_enabled=False)
        page = await ctx.new_page()
        await page.set_content(html)
        try:
            return await fn(page)
        finally:
            await browser.close()


def test_strategy_order_and_generated_ids():
    async def go(page):
        return {t: await L.record(page, t) for t in ("c3", "c4", "c5", "c6", "c7", "c1")}
    recs = run(_with_page(PAGE, go))
    assert recs["c3"]["primary"] == "testid"
    assert recs["c4"]["primary"] == "id"
    # a counter-style id is not trusted; the button falls through to role + name
    assert recs["c5"]["primary"] == "role"
    assert recs["c6"]["locator"] == {"kind": "role", "role": "button", "name": "Close dialog"}
    assert recs["c7"]["primary"] == "name"
    # "Docs" exists twice, so neither role+name nor text is unique: structural path
    assert recs["c1"]["primary"] == "css"
    assert all(f["unique"] is False for f in recs["c1"]["fingerprint"] if f["strategy"] in ("role", "text"))


def test_classify_outcomes():
    changed = PAGE.replace('data-testid="save-btn">Save', 'data-testid="save-btn-x1">Save') \
                  .replace('>Cancel<', '>Discard<')

    async def go(page):
        rec_save = await L.record(page, "c3")
        rec_docs = await L.record(page, "c1")
        await page.set_content(changed)
        save_hits = await L.resolve(page, rec_save["locator"])
        docs_hits = await L.resolve(page, rec_docs["locator"])
        role_hits = await L.resolve(page, {"kind": "role", "role": "link", "name": "Docs"})
        return save_hits, docs_hits, role_hits

    save_hits, docs_hits, role_hits = run(_with_page(PAGE, go))
    assert L.classify(save_hits, "c3", {"c3"}) == L.MISSING
    assert L.classify(docs_hits, "c1", {"c1"}) == L.INTACT
    assert L.classify(role_hits, "c1", {"c1", "c8"}) == L.AMBIGUOUS
    assert L.classify(["c8"], "c1", {"c1", "c8"}) == L.INTACT_EQUIVALENT
    assert L.classify(["c2"], "c1", {"c1"}) == L.WRONG_TARGET
    assert L.classify(["c2"], None, set()) == L.WRONG_TARGET


def test_parse_aria_head():
    assert L.parse_aria_head('- button "Save"') == ("button", "Save")
    assert L.parse_aria_head('- link "Read \\"more\\" here":\n  - /url: "#"') == ("link", 'Read "more" here')
    assert L.parse_aria_head("- combobox:\n  - option \"A\"") == ("combobox", "")
    assert L.parse_aria_head("- text: hello") == (None, "")
    assert L.parse_aria_head("") == (None, "")
