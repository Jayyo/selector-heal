"""Build healing rows from snapshots.

Per page:

    snapshot --normalise--> before render --record--> stored locator per target
                           control render  --replay--> locator still finds the target?
    for each variant:
        mutate (bundle + deletion) --> after render --replay--> break status
        broken steps --> top-K candidates by retrieval score --> row

Only steps whose stored locator actually fails on the changed page become rows
("healing cases"). Steps whose locator still resolves to the element, or to an
accepted equivalent, are counted in the ledger as controls and never scored.

Every random choice comes from a stream keyed by (page, variant, purpose), so a
page's rows do not depend on which worker built it or in what order, and the
target sample does not depend on the deletion condition.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import shutil
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import locator as L
from .mutate import Mutator, normalize_html
from .render import ACTIONABLE_TAGS, Renderer, has_box
from .retrieval import build_candidates, equivalent, norm
from .snapshot import load_snapshot, read_index

STEP_FIELDS = ("tag", "role", "type", "name", "text", "id", "testid", "testidAttr", "nameAttr",
               "href", "hrefAbs", "bbox", "heading", "ancestors")
CAND_FIELDS = ("track", "tag", "role", "type", "name", "text", "id", "testid", "testidAttr",
               "nameAttr", "href", "hrefAbs", "bbox", "heading")


@dataclass
class BuildConfig:
    variants: int = 12
    targets_per_variant: int = 14
    topk: int = 40
    deletion: str = "lookalike"
    delete_lo: float = 0.03
    delete_hi: float = 0.15
    seed: int = 0
    dist_path: str | None = None


def stream(*parts) -> random.Random:
    return random.Random(stream_int(*parts))


def stream_int(*parts) -> int:
    h = hashlib.blake2b("|".join(map(str, parts)).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big")


def is_lineage_track(t: str) -> bool:
    """Ids stamped at collection time; render-local ('r') and mutation ('m') ids are not lineage."""
    return bool(t) and t[0] == "c"


def eligible_target(e: dict) -> bool:
    """What a recorded step could plausibly point at: a laid-out, enabled, labelled control."""
    return (e["tag"] in ACTIONABLE_TAGS and has_box(e) and not e.get("disabled")
            and e.get("type") != "hidden" and bool(e.get("name") or e.get("text"))
            and is_lineage_track(e["track"]))


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


async def _dom_tracks(page) -> Counter:
    """How often each tracking id occurs in the rendered DOM.

    A serialised DOM that scripts built (nested anchors, misnested formatting
    tags) is restructured when the HTML parser reads it back, and the parser
    clones some elements -- attributes included. A cloned id has no single
    lineage, so it is handled explicitly instead of trusted.
    """
    return Counter(json.loads(await page.evaluate(
        "() => JSON.stringify(Array.from(document.querySelectorAll('[data-sh-track]'),"
        " e => e.getAttribute('data-sh-track')))")))


async def build_page(renderer: Renderer, snap, cfg: BuildConfig, dist_path: Path | None):
    key, url = snap.key, snap.final_url or snap.url
    host = host_of(url)
    page_info = {"page": key, "url": snap.url, "final_url": url, "host": host,
                 "deletion": cfg.deletion}
    rows, ledger = [], []

    html0 = normalize_html(snap.html)
    before = await renderer.render(html0, url, snap.resources)
    page_info["misses_before"] = dict(before.misses)
    before_by_track = before.by_track
    dup_before = {t for t, n in (await _dom_tracks(before.page)).items() if n > 1}
    elig = [e for e in before.elements if eligible_target(e) and e["track"] not in dup_before]
    # deletion only touches controls that were on screen; removing hidden ones
    # would spend the budget on changes nobody could see
    deletable = {e["track"] for e in before.elements if has_box(e) and is_lineage_track(e["track"])}
    page_info["eligible"] = len(elig)
    page_info["duplicated_ids_before"] = len(dup_before)

    plans = []
    for v in range(cfg.variants):
        rng = stream(cfg.seed, key, v, "targets")
        plans.append([e["track"] for e in rng.sample(elig, min(cfg.targets_per_variant, len(elig)))])
    needed = sorted({t for p in plans for t in p})
    recs = {t: await L.record(before.page, t) for t in needed}
    await renderer.close()

    # Replay every stored locator on a second, unchanged render. A locator that
    # does not survive a no-op can only produce fake breaks later.
    control = await renderer.render(html0, url, snap.resources)
    stable = {}
    for t in needed:
        rec = recs[t]
        stable[t] = rec is not None and (await L.resolve(control.page, rec["locator"])) == [t]
    await renderer.close()
    page_info["recorded"] = sum(1 for t in needed if recs[t] is not None)
    page_info["control_stable"] = sum(stable.values())

    for v in range(cfg.variants):
        mut = Mutator(seed=stream_int(cfg.seed, key, v, "mutate"), dist_path=dist_path)
        res = mut.mutate(html0, deletion_rng=stream(cfg.seed, key, v, "delete"),
                         deletion=cfg.deletion, delete_range=(cfg.delete_lo, cfg.delete_hi),
                         deletable=deletable, base_url=url)
        base = {"page": key, "host": host, "variant": v, "deletion": cfg.deletion}
        try:
            after = await renderer.render(res.html, url, snap.resources)
        except Exception as e:
            for t in plans[v]:
                ledger.append(dict(base, row_id=f"{key}:{v}:{t}", status="RENDER_FAIL",
                                   error=type(e).__name__))
            await renderer.close()
            continue
        counts = await _dom_tracks(after.page)
        in_dom = set(counts)
        dup_after = {t for t, n in counts.items() if n > 1}
        cands_all = [e for e in after.elements if has_box(e) and e["track"] not in dup_after]
        visible = {e["track"]: e for e in cands_all}

        for t in plans[v]:
            row_id = f"{key}:{v}:{t}"
            rec = recs[t]
            led = dict(base, row_id=row_id, primary=rec["primary"] if rec else None)
            if rec is None:
                ledger.append(dict(led, status="UNRECORDABLE"))
                continue
            if not stable[t]:
                ledger.append(dict(led, status="UNSTABLE_CONTROL"))
                continue
            if t in dup_after:
                ledger.append(dict(led, status="DUPLICATED_ID"))
                continue
            old = before_by_track[t]
            axes = (res.per_element or {}).get(t, [])
            if t not in in_dom:
                twin = next((e for e in cands_all if equivalent(old, e)), None)
                if twin is not None:
                    ledger.append(dict(led, status="EQUIV_EXCLUDED"))
                    continue
                successor, lineage, gold = "ABSENT", None, []
            elif t not in visible:
                ledger.append(dict(led, status="OBSERVATION_FAILURE"))
                continue
            else:
                g = visible[t]
                same = (g["tag"] == old["tag"] or norm(g.get("name")) == norm(old.get("name"))
                        or norm(g.get("text")) == norm(old.get("text")))
                if not same and not axes and "COPY_CHANGE" not in res.applied:
                    ledger.append(dict(led, status="IDENTITY_SUSPECT"))
                    continue
                twins = [e["track"] for e in cands_all if e["track"] != t and equivalent(g, e)]
                successor, lineage, gold = "PRESENT", t, sorted({t, *twins})

            hits = await L.resolve(after.page, rec["locator"])
            if hits is None:  # the engine rejected the locator: not the same as "matches nothing"
                ledger.append(dict(led, status="RESOLVE_ERROR", successor=successor))
                continue
            brk = L.classify(hits, lineage, set(gold))
            if brk in (L.INTACT, L.INTACT_EQUIVALENT):
                ledger.append(dict(led, status=brk, successor=successor))
                continue

            scored = build_candidates(old, cands_all, cfg.topk)
            cand_tracks = {c["track"] for _, c in scored}
            retrieval = ("HIT" if set(gold) & cand_tracks else "MISS") if gold else "N/A"
            fallback = []
            for f in rec["fingerprint"]:
                if f["strategy"] == rec["primary"] or not f["unique"]:
                    continue
                fh = await L.resolve(after.page, f["locator"]) or []
                fallback.append({"strategy": f["strategy"], "display": L.display(f["locator"]),
                                 "hits": fh[:5], "n_matches": len(fh)})
            rows.append({
                "row_id": row_id, "page": key, "url": url, "host": host, "variant": v,
                "deletion": cfg.deletion,
                "bundle": list(res.bundle), "applied": res.applied, "target_axes": axes,
                "primary": rec["primary"], "locator": rec["display"],
                "break": brk, "n_matches": len(hits),
                "successor": successor, "lineage": lineage, "gold_ids": gold,
                "retrieval": retrieval,
                "step": {k: old.get(k) for k in STEP_FIELDS},
                "candidates": [dict({k: c.get(k) for k in CAND_FIELDS}, score=round(s, 4))
                               for s, c in scored],
                "fallback": fallback,
            })
            ledger.append(dict(led, status="HEALING", successor=successor, retrieval=retrieval,
                               **{"break": brk}))
        await renderer.close()
    return page_info, rows, ledger


async def _build_many(snap_dir: Path, keys: list[str], cfg: BuildConfig, shard_dir: Path):
    from playwright.async_api import async_playwright

    dist = Path(cfg.dist_path) if cfg.dist_path else None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-dev-shm-usage"])
        renderer = Renderer(browser)
        for key in keys:
            snap = load_snapshot(snap_dir, key)
            try:
                info, rows, ledger = await build_page(renderer, snap, cfg, dist)
            except Exception as e:  # recorded and failed by gate L, never silently skipped
                info, rows, ledger = {"page": key, "url": snap.url,
                                      "error": f"{type(e).__name__}: {str(e)[:200]}"}, [], []
                await renderer.close()
            _write_jsonl(shard_dir / f"{key}.rows.jsonl", rows)
            _write_jsonl(shard_dir / f"{key}.ledger.jsonl", ledger)
            (shard_dir / f"{key}.page.json").write_text(
                json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            n_heal = sum(1 for l in ledger if l.get("status") == "HEALING")
            print(f"  {key} {snap.url[:70]}  rows={n_heal}"
                  + (f"  ERROR {info['error']}" if "error" in info else ""), flush=True)
        await browser.close()


def _worker(args):
    snap_dir, keys, cfg_dict, shard_dir = args
    asyncio.run(_build_many(Path(snap_dir), keys, BuildConfig(**cfg_dict), Path(shard_dir)))
    return len(keys)


def _write_jsonl(path: Path, rows: list):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def _browser_version() -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        v = b.version
        b.close()
    return v


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(snap_dir: Path, out_dir: Path, cfg: BuildConfig, workers: int = 1,
          keys: list[str] | None = None) -> dict:
    index = [r for r in read_index(snap_dir) if "error" not in r]
    all_keys = sorted(r["key"] for r in index)
    keys = sorted(keys) if keys else all_keys
    shard_dir = out_dir / "shards"
    if shard_dir.exists():
        shutil.rmtree(shard_dir)
    shard_dir.mkdir(parents=True)
    workers = max(1, min(workers, len(keys)))
    chunks = [keys[i::workers] for i in range(workers)]
    jobs = [(str(snap_dir), c, asdict(cfg), str(shard_dir)) for c in chunks if c]
    if workers == 1:
        _worker(jobs[0])
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_worker, jobs))

    # merge in canonical page order: identical bytes whatever the worker count
    counts: dict = {}
    pages = []
    with (out_dir / "rows.jsonl").open("w", encoding="utf-8") as fr, \
         (out_dir / "ledger.jsonl").open("w", encoding="utf-8") as fl:
        for key in keys:
            for line in (shard_dir / f"{key}.rows.jsonl").read_text(encoding="utf-8").splitlines():
                fr.write(line + "\n")
            for line in (shard_dir / f"{key}.ledger.jsonl").read_text(encoding="utf-8").splitlines():
                fl.write(line + "\n")
                st = json.loads(line)["status"]
                counts[st] = counts.get(st, 0) + 1
            pages.append(json.loads((shard_dir / f"{key}.page.json").read_text(encoding="utf-8")))
    _write_jsonl(out_dir / "pages.jsonl", pages)
    shutil.rmtree(shard_dir)

    src = Path(__file__).parent
    from importlib.metadata import version
    import platform
    from .mutate import DEFAULT_DIST
    dist = Path(cfg.dist_path) if cfg.dist_path else DEFAULT_DIST
    manifest = {
        "config": asdict(cfg),
        "distribution_sha256": file_sha256(dist),
        "versions": {"python": platform.python_version(), "playwright": version("playwright"),
                     "beautifulsoup4": version("beautifulsoup4"), "chromium": _browser_version()},
        "pages": len(keys),
        "page_errors": [p["page"] for p in pages if "error" in p],
        "ledger_counts": dict(sorted(counts.items())),
        "rows_sha256": file_sha256(out_dir / "rows.jsonl"),
        "ledger_sha256": file_sha256(out_dir / "ledger.jsonl"),
        "snapshot_html_sha256": {r["key"]: r["html_sha256"] for r in index if r["key"] in set(keys)},
        "code_sha256": {f.name: file_sha256(f) for f in sorted(src.glob("*.py"))},
    }
    (out_dir / "build.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    return manifest
