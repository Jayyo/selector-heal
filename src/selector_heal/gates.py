"""Checks that must pass before a build is trained on or scored.

Each one exists because the failure it catches has happened before:

  A  control      stored locators still find their element on an unchanged re-render
  B  rebuild      rebuilding pages gives byte-identical rows and ledger (salted hashes,
                  global RNG and output files written by several workers all broke this once)
  C  broken only  every row is a step whose stored locator actually fails
  D  labels       gold sets, retrieval status and candidates agree with each other
  E  holdout      no site group in two splits, every row's page in the manifest
  L  ledger       no page failed, every sampled step has exactly one status, and the
                  HEALING entries are exactly the rows

The descriptive numbers printed after the gates are not pass/fail, but any
headline number should be read next to them -- in particular how often the gold
element is already the top retrieved candidate.
"""
from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from .build import BuildConfig, build
from .locator import BROKEN


def _rows(data: Path):
    return [json.loads(l) for l in (data / "rows.jsonl").open(encoding="utf-8")]


def _ledger(data: Path):
    return [json.loads(l) for l in (data / "ledger.jsonl").open(encoding="utf-8")]


def _pages(data: Path):
    return [json.loads(l) for l in (data / "pages.jsonl").open(encoding="utf-8")]


def gate_control(pages) -> bool:
    """Per distinct recorded step, not per step-variant."""
    recorded = sum(p.get("recorded", 0) for p in pages)
    stable = sum(p.get("control_stable", 0) for p in pages)
    rate = (recorded - stable) / recorded if recorded else 0.0
    print(f"  A control   stored locators lost on an unchanged re-render: "
          f"{recorded - stable}/{recorded} = {rate:.4f}")
    return recorded > 0 and rate <= 0.02


def gate_ledger(data: Path, rows, ledger, pages) -> bool:
    built = json.loads((data / "build.json").read_text(encoding="utf-8"))
    cfg = built["config"]
    errors = [p["page"] for p in pages if "error" in p]
    ids = [l["row_id"] for l in ledger]
    expected = sum(cfg["variants"] * min(cfg["targets_per_variant"], p.get("eligible", 0))
                   for p in pages if "error" not in p)
    healing = sorted(l["row_id"] for l in ledger if l["status"] == "HEALING")
    row_ids = sorted(r["row_id"] for r in rows)
    ok = (not errors and len(ids) == len(set(ids)) == expected
          and healing == row_ids and len(row_ids) == len(set(row_ids)))
    print(f"  L ledger    page errors {len(errors)}; ledger entries {len(ids)} "
          f"(expected {expected}, unique {len(set(ids))}); HEALING entries match rows: "
          f"{healing == row_ids}")
    return ok


def gate_broken(rows) -> bool:
    bad = [r["row_id"] for r in rows if r["break"] not in BROKEN]
    print(f"  C broken    rows whose locator did not fail: {len(bad)}/{len(rows)}")
    return bool(rows) and not bad


def gate_labels(rows) -> bool:
    bad = Counter()
    for r in rows:
        tracks = [c["track"] for c in r["candidates"]]
        gold = set(r["gold_ids"])
        if len(tracks) != len(set(tracks)):
            bad["duplicate candidate"] += 1
        if r["successor"] == "PRESENT":
            if not gold or r["lineage"] not in gold:
                bad["present without lineage in gold"] += 1
            hit = bool(gold & set(tracks))
            if (r["retrieval"] == "HIT") != hit:
                bad["retrieval status disagrees with candidates"] += 1
        else:
            if gold or r["retrieval"] != "N/A" or r["lineage"] is not None:
                bad["absent row with gold or lineage"] += 1
            target = r["row_id"].rsplit(":", 1)[1]
            if target in tracks:
                bad["removed element still a candidate"] += 1
    print(f"  D labels    defects: {dict(bad) or 0}")
    return bool(rows) and not bad


def gate_holdout(rows, manifest: Path) -> bool:
    m = json.loads(manifest.read_text(encoding="utf-8"))
    split_of = {p["key"]: p["split"] for p in m["pages"]}
    group_splits = {}
    for p in m["pages"]:
        if p["split"] in ("train", "val", "test"):
            group_splits.setdefault(p["group"], set()).add(p["split"])
    mixed = {g: sorted(s) for g, s in group_splits.items() if len(s) > 1}
    unknown = {r["page"] for r in rows if r["page"] not in split_of}
    print(f"  E holdout   groups in more than one split: {mixed or 0}; rows from unlisted pages: "
          f"{len(unknown)}")
    return not mixed and not unknown


def gate_rebuild(data: Path, snapshots: Path, n_pages: int) -> bool:
    built = json.loads((data / "build.json").read_text(encoding="utf-8"))
    cfg = BuildConfig(**built["config"])
    keys = sorted(built["snapshot_html_sha256"])[:n_pages]
    with tempfile.TemporaryDirectory(prefix="sh_rebuild_") as tmp:
        build(snapshots, Path(tmp), cfg, workers=min(2, len(keys)), keys=keys)
        again = (Path(tmp) / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        again_ledger = (Path(tmp) / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    keyset = set(keys)
    orig = [l for l in (data / "rows.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(l)["page"] in keyset]
    orig_ledger = [l for l in (data / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
                   if json.loads(l)["page"] in keyset]
    same = orig == again and orig_ledger == again_ledger
    print(f"  B rebuild   {len(keys)} pages rebuilt: {len(again)} rows, {len(again_ledger)} "
          f"ledger entries, {'identical' if same else 'DIFFERENT'}")
    if not same:
        a = {json.loads(l)["row_id"]: json.loads(l) for l in orig}
        b = {json.loads(l)["row_id"]: json.loads(l) for l in again}
        fields = Counter(f for k in set(a) & set(b) for f in a[k] if a[k][f] != b[k].get(f))
        print(f"              rows only in one build: {len(set(a) ^ set(b))}; "
              f"fields that differ: {dict(fields)}")
    return same


def describe(rows, ledger):
    S = [r for r in rows if r["successor"] == "PRESENT"]
    hit = [r for r in S if r["retrieval"] == "HIT"]
    ranks = Counter()
    for r in hit:
        tr = [c["track"] for c in r["candidates"]]
        pos = next(i for i, t in enumerate(tr) if t in set(r["gold_ids"]))
        ranks[pos if pos < 3 else "3+"] += 1
    top1 = ranks.get(0, 0)
    print("\n  descriptive (not gates)")
    print(f"    rows {len(rows)}: successor present {len(S)}, removed {len(rows) - len(S)}")
    print(f"    retrieval recall@K {len(hit)}/{len(S)}"
          + (f" = {len(hit) / len(S):.3f}" if S else ""))
    print(f"    gold rank among retrieved: {dict(ranks)}; gold is the top candidate in "
          f"{top1}/{len(hit)}" + (f" = {top1 / len(hit):.3f}" if hit else ""))
    print(f"    break types: {dict(Counter(r['break'] for r in rows))}")
    print(f"    stored strategy of broken steps: {dict(Counter(r['primary'] for r in rows))}")
    print(f"    ledger: {dict(Counter(l['status'] for l in ledger))}")


def run_gates(data: Path, manifest: Path | None, rebuild_snapshots: Path | None = None,
              rebuild_pages: int = 3) -> bool:
    rows, ledger, pages = _rows(data), _ledger(data), _pages(data)
    print(f"gates for {data}")
    results = {"A control": gate_control(pages), "C broken": gate_broken(rows),
               "D labels": gate_labels(rows), "L ledger": gate_ledger(data, rows, ledger, pages)}
    if manifest:
        results["E holdout"] = gate_holdout(rows, manifest)
    if rebuild_snapshots:
        results["B rebuild"] = gate_rebuild(data, rebuild_snapshots, rebuild_pages)
    describe(rows, ledger)
    print("\n  " + "  ".join(f"{k}: {'PASS' if v else 'FAIL'}" for k, v in results.items()))
    return all(results.values())
