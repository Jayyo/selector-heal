"""Hermetic renderer shared by the build and the tests.

Every page that ends up in a dataset row -- the unchanged snapshot and each
mutated variant -- goes through exactly this path: JavaScript off, the document
served from memory at its original URL, subresources served from the snapshot's
resource archive, everything else aborted, animations paused. The before and
after pages are rendered under identical offline settings, so a rebuild does not
depend on when it runs or on the live site.

JavaScript stays off because the snapshot is already the post-hydration DOM.
Letting a framework boot again tears those nodes down and rebuilds them, which
drops the tracking attributes and silently breaks the before/after mapping.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

TRACK = "data-sh-track"

# Tags a recorded guide or test step can target. Elements outside this set can
# still be candidates (a div with a click handler is a plausible successor).
ACTIONABLE_TAGS = ("a", "button", "input", "select", "textarea", "summary")

# Runs in the page. Reads the per-element signals used for retrieval and for the
# model prompt. It never decides lineage: that is the tracking attribute's job.
# `name` approximates the accessible name: aria-label, aria-labelledby, associated
# <label>s, title, placeholder, the value of button-type inputs, then the text,
# then an image's alt. Playwright's own computation is used for locators.
EXTRACT_JS = r"""
() => {
  const ACT = new Set(['BUTTON','A','INPUT','SELECT','TEXTAREA','SUMMARY','LABEL']);
  const TESTID = ['data-testid','data-test-id','data-test','data-cy','data-qa'];
  const txt = (n) => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const out = [];
  let auto = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (!el.hasAttribute('data-sh-track')) {
      // parser-inserted or otherwise unstamped: give it a render-local id that
      // can never be mistaken for lineage (see build.is_lineage_track)
      el.setAttribute('data-sh-track', 'r' + (auto++));
    }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const interactive = ACT.has(el.tagName) || el.hasAttribute('role') ||
      el.hasAttribute('onclick') || el.getAttribute('tabindex') !== null ||
      cs.cursor === 'pointer';
    if (!interactive) continue;
    const r = el.getBoundingClientRect();
    let name = (el.getAttribute('aria-label') || '').trim();
    if (!name && el.getAttribute('aria-labelledby')) {
      name = el.getAttribute('aria-labelledby').split(/\s+/)
        .map(id => txt(document.getElementById(id))).filter(Boolean).join(' ');
    }
    if (!name && el.labels && el.labels.length) {
      name = Array.from(el.labels).map(txt).filter(Boolean).join(' ');
    }
    if (!name) name = (el.getAttribute('title') || el.getAttribute('placeholder') || '').trim();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (!name && el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(type)) {
      name = (el.getAttribute('value') || '').trim();
    }
    if (!name) name = txt(el);
    if (!name && el.querySelector) {
      const img = el.querySelector('img[alt]');
      if (img) name = (img.getAttribute('alt') || '').trim();
    }
    let testidAttr = '', testid = '';
    for (const a of TESTID) {
      const v = el.getAttribute(a);
      if (v) { testidAttr = a; testid = v; break; }
    }
    const anc = [];
    let p = el.parentElement, d = 0;
    while (p && d < 5) {
      anc.push(p.tagName.toLowerCase() + (p.id ? '#' + p.id : ''));
      p = p.parentElement; d++;
    }
    let heading = '';
    let n = el, hops = 0;
    while (n && hops < 8 && !heading) {
      const h = n.querySelector ? n.querySelector('h1,h2,h3,legend') : null;
      if (h && h.innerText) heading = h.innerText.trim().slice(0, 80);
      n = n.parentElement; hops++;
    }
    out.push({
      track: el.getAttribute('data-sh-track'),
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      type: type,
      name: name.slice(0, 120),
      text: (el.innerText || '').trim().slice(0, 120),
      id: el.id || '',
      testid: testid,
      testidAttr: testidAttr,
      nameAttr: el.getAttribute('name') || '',
      aria: el.getAttribute('aria-label') || '',
      href: el.getAttribute('href') || '',
      hrefAbs: (el.hasAttribute('href') && typeof el.href === 'string') ? el.href : '',
      disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true',
      bbox: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      ancestors: anc,
      heading: heading,
    });
  }
  return JSON.stringify(out);
}
"""

CONTEXT_OPTIONS = dict(
    viewport={"width": 1440, "height": 900},
    device_scale_factor=1,
    locale="ko-KR",
    timezone_id="Asia/Seoul",
    color_scheme="light",
    reduced_motion="reduce",
    java_script_enabled=False,
    service_workers="block",
)


@dataclass
class RenderedPage:
    page: object                     # playwright Page, open until the context closes
    elements: list
    misses: Counter = field(default_factory=Counter)  # aborted requests by resource type

    @property
    def by_track(self) -> dict:
        return {e["track"]: e for e in self.elements}


class Renderer:
    """Renders HTML offline. Use one instance per browser; each render gets its own context."""

    def __init__(self, browser, settle_ms: int = 400, timeout_ms: int = 30000):
        self.browser = browser
        self.settle_ms = settle_ms
        self.timeout_ms = timeout_ms
        self._ctx = None

    async def render(self, html: str, url: str, resources: dict) -> RenderedPage:
        await self.close()
        ctx = await self.browser.new_context(**CONTEXT_OPTIONS)
        self._ctx = ctx
        page = await ctx.new_page()
        misses: Counter = Counter()
        served = {"doc": False}

        async def handler(route, request):
            if request.is_navigation_request():
                if request.frame == page.main_frame and not served["doc"]:
                    served["doc"] = True
                    await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                        body=html)
                else:
                    misses["subframe"] += 1
                    await route.abort()
                return
            r = resources.get(request.url)
            if r is None:
                misses[request.resource_type] += 1
                await route.abort()
                return
            headers = {"access-control-allow-origin": "*"}  # cross-origin fonts need CORS
            if r.content_type:
                headers["content-type"] = r.content_type
            await route.fulfill(status=r.status, headers=headers, body=r.body)

        await page.route("**/*", handler)
        # CSS animations and transitions still run with scripts off; a carousel or
        # a slide-in banner then sits wherever it happens to be when the boxes are
        # read. Pausing the document timeline makes every render read time zero.
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Animation.enable")
        await cdp.send("Animation.setPlaybackRate", {"playbackRate": 0})
        await page.goto(url, wait_until="load", timeout=self.timeout_ms)
        try:
            await page.evaluate("document.fonts ? document.fonts.ready.then(() => 1) : 1")
        except Exception:
            pass
        await page.wait_for_timeout(self.settle_ms)
        elements = json.loads(await page.evaluate(EXTRACT_JS))
        return RenderedPage(page=page, elements=elements, misses=misses)

    async def close(self):
        if self._ctx is not None:
            try:
                await self._ctx.close()
            finally:
                self._ctx = None


def has_box(e: dict) -> bool:
    b = e.get("bbox") or [0, 0, 0, 0]
    return b[2] > 0 and b[3] > 0
