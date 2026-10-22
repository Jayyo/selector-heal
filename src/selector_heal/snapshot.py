"""On-disk snapshot format.

A snapshot directory holds one page per collected URL:

    <dir>/index.jsonl          one line per page (key, url, final_url, source, hashes)
    <dir>/pages/<key>.json.gz  the stamped post-JS HTML plus metadata
    <dir>/resources/<key>.zip  stylesheets, fonts and images the page loaded

The resource archive is what makes builds hermetic: the renderer serves every
subresource from it and aborts anything that is missing, so a build never
depends on the live site still looking the way it did at collection time.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass
class Resource:
    url: str
    status: int
    content_type: str
    body: bytes


@dataclass
class Snapshot:
    key: str
    url: str
    final_url: str
    title: str
    html: str
    source: str = ""
    collected_at: str = ""
    resources: dict = field(default_factory=dict)  # url -> Resource

    @property
    def html_sha256(self) -> str:
        return sha256_bytes(self.html.encode("utf-8"))

    def resources_sha256(self) -> str:
        h = hashlib.sha256()
        for url in sorted(self.resources):
            r = self.resources[url]
            h.update(f"{url}\0{r.status}\0{r.content_type}\0{sha256_bytes(r.body)}\n"
                     .encode("utf-8"))
        return h.hexdigest()


def write_snapshot(root: Path, snap: Snapshot) -> dict:
    (root / "pages").mkdir(parents=True, exist_ok=True)
    (root / "resources").mkdir(parents=True, exist_ok=True)
    meta = {"key": snap.key, "url": snap.url, "final_url": snap.final_url,
            "title": snap.title, "source": snap.source, "collected_at": snap.collected_at}
    page = dict(meta, html=snap.html)
    with gzip.open(root / "pages" / f"{snap.key}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(page, f, ensure_ascii=False)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        index, seen = {}, set()
        for url in sorted(snap.resources):
            r = snap.resources[url]
            digest = sha256_bytes(r.body)
            index[url] = {"status": r.status, "content_type": r.content_type, "sha256": digest}
            if digest not in seen:
                # fixed timestamp keeps the archive bytes reproducible
                info = zipfile.ZipInfo(f"b/{digest}", date_time=(2020, 1, 1, 0, 0, 0))
                z.writestr(info, r.body)
                seen.add(digest)
        info = zipfile.ZipInfo("index.json", date_time=(2020, 1, 1, 0, 0, 0))
        z.writestr(info, json.dumps(index, sort_keys=True))
    (root / "resources" / f"{snap.key}.zip").write_bytes(buf.getvalue())

    return dict(meta, html_sha256=snap.html_sha256, n_resources=len(snap.resources),
                resources_sha256=snap.resources_sha256())


def read_index(root: Path) -> list[dict]:
    p = root / "index.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def load_snapshot(root: Path, key: str, with_resources: bool = True) -> Snapshot:
    with gzip.open(root / "pages" / f"{key}.json.gz", "rt", encoding="utf-8") as f:
        d = json.load(f)
    res = {}
    zpath = root / "resources" / f"{key}.zip"
    if with_resources and zpath.exists():
        with zipfile.ZipFile(zpath) as z:
            index = json.loads(z.read("index.json"))
            for url, m in index.items():
                res[url] = Resource(url, int(m["status"]), m["content_type"],
                                    z.read(f"b/{m['sha256']}"))
    return Snapshot(key=d["key"], url=d["url"], final_url=d["final_url"], title=d.get("title", ""),
                    html=d["html"], source=d.get("source", ""),
                    collected_at=d.get("collected_at", ""), resources=res)
