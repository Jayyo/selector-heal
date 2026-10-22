"""How many controls in public admin/demo consoles carry a durable identifier?

Opens each URL with JavaScript on (these are single-page apps), counts
button-like controls that are laid out, and checks which of them have a test
id, a stable-looking id, or a name attribute. Links are counted separately:
a nav link with a real href is easy to find again, a button usually is not.

It also runs the public recorder on up to 150 button-like controls per page and
tallies which strategy it would store. The URL list is a convenience sample of
consoles reachable without an account, not a random sample of business apps.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

from . import locator as L
from .collect import USER_AGENT, read_url_list

SURVEY_JS = r"""
() => {
  const ACT = new Set(['BUTTON','A','INPUT','SELECT','TEXTAREA','SUMMARY']);
  const ROLES = new Set(['button','link','checkbox','radio','switch','tab','menuitem',
    'menuitemcheckbox','menuitemradio','option','textbox','searchbox','combobox','listbox',
    'slider','spinbutton','treeitem']);
  const looksStable = (v) => !!v && v.length <= 64 && !/\d{3,}/.test(v) && !v.includes(':') &&
    !/^[0-9a-f]{8,}$/i.test(v) && !/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i.test(v) &&
    !/^(mui|react|ember|ng|radix|headlessui|chakra|mantine|rc|cdk|mat|tippy|downshift)[-_]/i.test(v) &&
    !/^\d/.test(v);
  const CONTROL = 'button,a[href],[role=button],[role=link],[role=menuitem],[role=tab],[role=option]';
  const out = {buttons: 0, btn_testid: 0, btn_stable_id: 0, btn_name: 0, btn_durable: 0,
               links: 0, sample: []};
  let n = 0;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const role = (el.getAttribute('role') || '').trim().split(/\s+/)[0];
    const ti = el.getAttribute('tabindex');
    const parentPointer = el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer';
    const interactive = ACT.has(el.tagName) || ROLES.has(role) || el.hasAttribute('onclick') ||
      (ti !== null && Number(ti) >= 0) || (cs.cursor === 'pointer' && !parentPointer);
    if (!interactive) continue;
    if (el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    // an icon or label inside a counted control is part of that control
    if (el.parentElement && el.parentElement.closest(CONTROL)) continue;
    const href = el.getAttribute('href') || '';
    if (role === 'link' || (el.tagName === 'A' && href && href !== '#' && !href.startsWith('javascript:'))) {
      out.links++;
      continue;
    }
    out.buttons++;
    const testid = ['data-testid','data-test-id','data-test','data-cy','data-qa'].some(a => el.getAttribute(a));
    const sid = looksStable(el.getAttribute('id') || '');
    const nm = !!el.getAttribute('name');
    if (testid) out.btn_testid++;
    if (sid) out.btn_stable_id++;
    if (nm) out.btn_name++;
    if (testid || sid || nm) out.btn_durable++;
    if (out.sample.length < 150) {
      el.setAttribute('data-sh-track', 's' + n);
      out.sample.push('s' + n);
      n++;
    }
  }
  return JSON.stringify(out);
}
"""


async def survey(urls_file: Path, out: Path, wait_ms: int = 5000):
    from playwright.async_api import async_playwright

    entries = read_url_list(urls_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()
    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-dev-shm-usage"])
        for url, _ in entries:
            rec = {"url": url, "date": day}
            ctx = None
            try:
                ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                                locale="en-US", user_agent=USER_AGENT)
                page = await ctx.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(wait_ms)
                rec["status"] = resp.status if resp else None
                rec["final_url"] = page.url
                d = json.loads(await page.evaluate(SURVEY_JS))
                sample = d.pop("sample")
                rec.update(d)
                strategies = Counter()
                for t in sample:
                    r = await L.record(page, t)
                    strategies[r["primary"] if r else "none"] += 1
                rec["recorder_sample"] = len(sample)
                rec["recorder_primary"] = dict(strategies)
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:150]}"
            finally:
                if ctx:
                    await ctx.close()
            rows.append(rec)
            b = rec.get("buttons", 0)
            print(f"  {url[:60]:60s} buttons {b:4d} durable "
                  f"{rec.get('btn_durable', 0) / b if b else 0:.3f} {rec.get('error', '')}", flush=True)
        await browser.close()
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summarize(out)


def summarize(path: Path, min_buttons: int = 20) -> dict:
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    ok = [r for r in rows if "error" not in r and r.get("buttons", 0) >= min_buttons]
    shares = sorted(r["btn_durable"] / r["buttons"] for r in ok)
    tot_b = sum(r["buttons"] for r in ok)
    tot_d = sum(r["btn_durable"] for r in ok)
    strat = Counter()
    for r in ok:
        strat.update(r.get("recorder_primary") or {})
    med = None
    if shares:
        m = len(shares) // 2
        med = shares[m] if len(shares) % 2 else (shares[m - 1] + shares[m]) / 2
    s = {"consoles_measured": len(ok), "consoles_listed": len(rows), "buttons": tot_b,
         "durable_median": med, "durable_pooled": tot_d / tot_b if tot_b else None,
         "recorder_primary": dict(strat.most_common())}
    print(json.dumps(s, indent=2))
    return s
