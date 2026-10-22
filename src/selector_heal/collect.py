"""Collect page snapshots: stamped post-JS HTML plus the subresources it loaded.

Live pages are rendered once in headless Chromium with JavaScript on. Every
element gets a `data-sh-track` id before the DOM is serialised, so any later
copy of the page can be mapped back to the original elements.

Stylesheets, fonts and images are captured from the network responses and
stored next to the HTML; the build serves them from there instead of the web.
Local HTML files (the test fixtures) go through the same code with a fake
origin and no network at all.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

from .snapshot import Resource, Snapshot, url_key, write_snapshot

# Returns a JSON string: some pages patch built-ins so that Playwright's own
# serialisation of objects and arrays comes back as None.
STAMP_JS = r"""
() => {
  const taken = new Set(Array.from(document.querySelectorAll('[data-sh-track]'))
                         .map(e => e.getAttribute('data-sh-track')));
  let auto = 0;
  let interactive = 0;
  const ACT = new Set(['BUTTON','A','INPUT','SELECT','TEXTAREA','SUMMARY']);
  for (const el of document.querySelectorAll('*')) {
    if (!el.hasAttribute('data-sh-track')) {
      let id;
      do { id = 'c' + (auto++); } while (taken.has(id));
      taken.add(id);
      el.setAttribute('data-sh-track', id);
    }
    if (ACT.has(el.tagName)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) interactive++;
    }
  }
  let doctype = '';
  if (document.doctype) doctype = new XMLSerializer().serializeToString(document.doctype);
  return JSON.stringify({ html: doctype + document.documentElement.outerHTML,
                          title: document.title, url: location.href, interactive: interactive });
}
"""

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
KEEP_TYPES = ("stylesheet", "font", "image")
MAX_RESOURCE_BYTES = 8 * 1024 * 1024
FIXTURE_ORIGIN = "https://fixtures.selector-heal.test/"


def read_url_list(path: Path) -> list[tuple[str, str]]:
    """Lines are URLs; `# list: <name>` switches the source label for what follows."""
    out, source = [], path.stem
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line[1:].strip().startswith("list:"):
                source = line.split(":", 1)[1].strip()
            continue
        out.append((line, source))
    return out


async def _capture(page, store: dict, pending: list):
    async def grab(resp):
        req = resp.request
        if req.resource_type not in KEEP_TYPES or req.method != "GET":
            return
        try:
            body = await resp.body()
        except Exception:
            return
        if len(body) > MAX_RESOURCE_BYTES:
            return
        ct = (await resp.all_headers()).get("content-type", "")
        res = Resource(req.url, resp.status, ct, body)
        store[req.url] = res
        prev = req.redirected_from
        while prev is not None:  # serve the final body for every hop of a redirect chain
            store.setdefault(prev.url, res)
            prev = prev.redirected_from

    page.on("response", lambda r: pending.append(asyncio.ensure_future(grab(r))))


async def collect_urls(entries, out_dir: Path, concurrency: int = 4, wait_ms: int = 3500,
                       timeout_ms: int = 45000, min_interactive: int = 5) -> list[dict]:
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results = {}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-dev-shm-usage"])

        async def one(url: str, source: str):
            async with sem:
                ctx = None
                status = {"url": url, "source": source, "key": url_key(url)}
                try:
                    ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                                    locale="ko-KR", timezone_id="Asia/Seoul",
                                                    user_agent=USER_AGENT)
                    page = await ctx.new_page()
                    store, pending = {}, []
                    await _capture(page, store, pending)
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    # let client-side frameworks settle; networkidle never fires on
                    # pages with analytics beacons or long polling
                    await page.wait_for_timeout(wait_ms)
                    data = json.loads(await page.evaluate(STAMP_JS))
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if data["interactive"] < min_interactive:
                        status["error"] = f"too few interactive elements ({data['interactive']})"
                    else:
                        snap = Snapshot(key=status["key"], url=url, final_url=data["url"],
                                        title=data["title"], html=data["html"], source=source,
                                        collected_at=stamp, resources=store)
                        status.update(write_snapshot(out_dir, snap))
                        status["interactive"] = data["interactive"]
                except Exception as e:  # network errors, timeouts, crashes
                    status["error"] = f"{type(e).__name__}: {str(e)[:160]}"
                finally:
                    if ctx:
                        await ctx.close()
                results[url] = status
                tag = "ERR " if "error" in status else "OK  "
                print(f"  {tag}{url}  {status.get('error', status.get('interactive', ''))}",
                      flush=True)

        await asyncio.gather(*(one(u, s) for u, s in entries))
        await browser.close()

    rows = [results[u] for u, _ in entries]
    _write_index(out_dir, rows)
    return rows


async def collect_local(html_dir: Path, out_dir: Path) -> list[dict]:
    """Snapshot local HTML files under a fake origin. No request leaves the machine."""
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for f in sorted(html_dir.glob("*.html")):
            url = FIXTURE_ORIGIN + f.name
            html = f.read_text(encoding="utf-8")
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                            locale="ko-KR", timezone_id="Asia/Seoul")
            page = await ctx.new_page()

            async def handler(route, request, html=html, url=url):
                if request.url == url:
                    await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                        body=html)
                else:
                    await route.abort()

            await page.route("**/*", handler)
            await page.goto(url, wait_until="load")
            data = json.loads(await page.evaluate(STAMP_JS))
            await ctx.close()
            snap = Snapshot(key=url_key(url), url=url, final_url=data["url"], title=data["title"],
                            html=data["html"], source="fixtures", collected_at="fixture")
            row = dict(write_snapshot(out_dir, snap), interactive=data["interactive"])
            rows.append(row)
            print(f"  OK  {f.name}  {data['interactive']} interactive", flush=True)
        await browser.close()
    _write_index(out_dir, rows)
    return rows


def _write_index(out_dir: Path, rows: list[dict]):
    with (out_dir / "index.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
