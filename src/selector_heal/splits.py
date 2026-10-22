"""Assign pages to train / val / test by site group, never by row.

Two variants of one page share almost all of their text, and two pages of one
site share templates, so any split below the site level leaks. A site group is
the registrable domain approximated by a small suffix heuristic
(`docs.example.com` -> `example.com`; Korean second-level suffixes such as
`go.kr` and hosting suffixes such as `github.io` keep one more label), plus
manual merges for one product served from two domains.

Rules (configs/split.json):
  * a test group must not appear in the main lists at all -- neither as a listed
    URL (collected or not) nor as the final URL of a collected main page. Test-list
    pages on such a site are excluded, not moved;
  * validation is a fixed list of groups from the main lists, plus every main-list
    group whose SHA-1 starts with one of `val_hash_prefixes`;
  * everything else from the main lists is training.

The manifest also lists cross-split page pairs whose control labels overlap
heavily, which is how shared templates show up.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .collect import read_url_list
from .snapshot import load_snapshot, read_index

KR_SECOND_LEVEL = {"go", "or", "co", "re", "ac", "ne", "pe", "ms", "hs", "sc", "mil"}
HOSTING_SUFFIXES = {"github.io", "netlify.app", "vercel.app", "readthedocs.io", "pages.dev",
                    "gitlab.io"}


def site_group(host: str, merges: dict | None = None) -> str:
    host = host.lower().split(":")[0]
    labels = host.split(".")
    if len(labels) >= 3 and labels[-1] == "kr" and labels[-2] in KR_SECOND_LEVEL:
        g = ".".join(labels[-3:])
    elif len(labels) >= 3 and ".".join(labels[-2:]) in HOSTING_SUFFIXES:
        g = ".".join(labels[-3:])
    else:
        g = ".".join(labels[-2:])
    return (merges or {}).get(g, g)


def _canonical(url: str) -> str:
    u = urlparse(url)
    return f"{u.netloc.lower()}{u.path.rstrip('/')}"


def _labels(snap_dir: Path, key: str) -> set:
    soup = BeautifulSoup(load_snapshot(snap_dir, key, with_resources=False).html, "html.parser")
    out = set()
    for el in soup.find_all(("a", "button")):
        t = " ".join(el.get_text(" ", strip=True).split()).lower()
        if 1 < len(t) <= 60:
            out.add(t)
    return out


def make_manifest(snap_dirs: list[Path], config: Path, out: Path) -> dict:
    cfg = json.loads(config.read_text(encoding="utf-8"))
    merges = cfg.get("merge", {})
    by_page = cfg.get("group_by", "site") == "page"   # fixtures share one fake host
    test_groups = set(cfg.get("test_groups", []))
    val_groups = set(cfg["val_groups"])
    prefixes = tuple(cfg.get("val_hash_prefixes", ()))

    def is_val(group: str) -> bool:
        return group in val_groups or (
            bool(prefixes) and hashlib.sha1(group.encode()).hexdigest().startswith(prefixes))

    main_sources, test_sources = set(cfg["main_sources"]), set(cfg["test_sources"])

    pages, seen = [], {}
    for d in snap_dirs:
        for r in read_index(d):
            if "error" in r:
                continue
            host = urlparse(r["final_url"]).netloc.lower()
            group = (urlparse(r["final_url"]).path.rsplit("/", 1)[-1] if by_page
                     else site_group(host, merges))
            snap = load_snapshot(d, r["key"])
            p = {"key": r["key"], "snapshots": d.name, "url": r["url"], "final_url": r["final_url"],
                 "host": host, "group": group, "source": r["source"],
                 "collected_at": r.get("collected_at", ""),
                 "html_sha256": snap.html_sha256, "resources_sha256": snap.resources_sha256(),
                 "n_resources": len(snap.resources)}
            canon = _canonical(r["final_url"])
            if canon in seen:
                p["split"], p["reason"] = "excluded", f"same final URL as {seen[canon]}"
            else:
                seen[canon] = r["key"]
            pages.append(p)

    main_groups = {p["group"] for p in pages if p["source"] in main_sources}
    for rel in cfg.get("main_url_lists", []):
        for url, _ in read_url_list((config.parent / rel).resolve()):
            main_groups.add(site_group(urlparse(url).netloc.lower(), merges))
    for p in pages:
        if "split" in p:
            continue
        if p["group"] in test_groups:
            p["split"] = "test"
        elif p["source"] in test_sources:
            if p["group"] in main_groups:
                p["split"], p["reason"] = "excluded", "site group also in the main lists"
            else:
                p["split"] = "test"
        elif p["source"] in main_sources:
            p["split"] = "val" if is_val(p["group"]) else "train"
        else:
            p["split"], p["reason"] = "excluded", f"unknown source {p['source']}"

    # shared templates across splits: Jaccard of visible control labels
    dirs = {d.name: d for d in snap_dirs}
    labels = {p["key"]: _labels(dirs[p["snapshots"]], p["key"]) for p in pages
              if p["split"] in ("train", "val", "test")}
    flagged = []
    used = [p for p in pages if p["key"] in labels]
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            if a["split"] == b["split"]:
                continue
            la, lb = labels[a["key"]], labels[b["key"]]
            if len(la) < 10 or len(lb) < 10:
                continue
            j = len(la & lb) / len(la | lb)
            if j >= 0.3:
                flagged.append({"a": a["key"], "a_split": a["split"], "a_url": a["final_url"],
                                "b": b["key"], "b_split": b["split"], "b_url": b["final_url"],
                                "label_jaccard": round(j, 3)})

    summary = {}
    for s in ("train", "val", "test", "excluded"):
        sel = [p for p in pages if p["split"] == s]
        summary[s] = {"pages": len(sel), "groups": len({p["group"] for p in sel})}
    manifest = {"rules": cfg, "summary": summary, "cross_split_label_overlap": flagged,
                "pages": sorted(pages, key=lambda p: (p["split"], p["group"], p["key"]))}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"cross-split pairs with label Jaccard >= 0.3: {len(flagged)}")
    return manifest


def load_split(manifest: Path) -> dict:
    """page key -> split"""
    m = json.loads(manifest.read_text(encoding="utf-8"))
    return {p["key"]: p["split"] for p in m["pages"]}
