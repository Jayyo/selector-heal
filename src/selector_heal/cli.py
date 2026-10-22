"""Command line entry point: `selector-heal <command>` or `python -m selector_heal <command>`."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

DEFAULT_BASE = "Qwen/Qwen2.5-3B-Instruct"
DEFAULT_REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"  # pinned; see docs/METHOD.md


def _collect(a):
    from .collect import collect_local, collect_urls, read_url_list
    if a.html_dir:
        rows = asyncio.run(collect_local(Path(a.html_dir), Path(a.out)))
    else:
        entries, seen = [], set()
        for f in a.urls:
            for u, src in read_url_list(Path(f)):
                if u not in seen:          # one snapshot per URL
                    seen.add(u)
                    entries.append((u, src))
        prev = {}
        if a.retry_failed:
            from .snapshot import read_index
            prev = {r["url"]: r for r in read_index(Path(a.out))}
            entries = [(u, s) for u, s in entries if u not in prev or "error" in prev[u]]
            print(f"retrying {len(entries)} failed URLs")
        rows = asyncio.run(collect_urls(entries, Path(a.out), concurrency=a.concurrency,
                                        wait_ms=a.wait_ms))
        if prev:
            from .collect import _write_index
            new = {r["url"]: r for r in rows}
            merged = [new.get(u, r) for u, r in prev.items()]
            merged += [r for u, r in new.items() if u not in prev]
            _write_index(Path(a.out), merged)
            rows = merged
    ok = sum(1 for r in rows if "error" not in r)
    print(f"collected {ok}/{len(rows)} pages -> {a.out}")


def _build(a):
    from .build import BuildConfig, build
    cfg = BuildConfig(variants=a.variants, targets_per_variant=a.targets, topk=a.topk,
                      deletion=a.deletion, seed=a.seed, dist_path=a.dist)
    m = build(Path(a.snapshots), Path(a.out), cfg, workers=a.workers,
              keys=a.pages.split(",") if a.pages else None)
    print(json.dumps(m["ledger_counts"], indent=2))
    if m["page_errors"]:
        print(f"pages with errors: {m['page_errors']}")


def _split(a):
    from .splits import make_manifest
    make_manifest([Path(p) for p in a.snapshots], Path(a.config), Path(a.out))


def _check(a):
    from .gates import run_gates
    sys.exit(0 if run_gates(Path(a.data), Path(a.manifest) if a.manifest else None,
                            rebuild_snapshots=Path(a.rebuild) if a.rebuild else None,
                            rebuild_pages=a.rebuild_pages) else 1)


def _fit_gate(a):
    from .baselines import fit_and_write
    fit_and_write(Path(a.data), Path(a.manifest), Path(a.out))


def _train(a):
    from .train import TrainConfig, train
    train(TrainConfig(data=a.data, manifest=a.manifest, out=a.out, base=a.base,
                      revision=a.revision, epochs=a.epochs, lora_r=a.lora_r, max_len=a.max_len,
                      seed=a.seed, lr=a.lr, accum=a.accum, max_rows=a.max_rows))


def _infer(a):
    from .infer import infer
    infer(Path(a.data), Path(a.manifest), a.split, Path(a.out), adapter=a.adapter, base=a.base,
          revision=a.revision, order=a.order, max_len=a.max_len, batch_size=a.batch_size)


def _report(a):
    from .report import report
    report(Path(a.config), Path(a.out), root=Path(a.root) if a.root else None)


def _survey(a):
    from .survey import survey
    asyncio.run(survey(Path(a.urls), Path(a.out)))


def _reprobreak(a):
    from .reprobreak import main as rb
    rb(Path(a.csv), Path(a.out))


def main(argv=None):
    p = argparse.ArgumentParser(prog="selector-heal")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="snapshot live URLs or local HTML files")
    c.add_argument("--urls", nargs="*", default=[], help="URL list files")
    c.add_argument("--html-dir", help="collect local *.html files instead (offline)")
    c.add_argument("--out", required=True)
    c.add_argument("--concurrency", type=int, default=4)
    c.add_argument("--wait-ms", type=int, default=3500, help="settle time after DOMContentLoaded")
    c.add_argument("--retry-failed", action="store_true",
                   help="only re-collect URLs whose last attempt failed, keep the rest")
    c.set_defaults(fn=_collect)

    b = sub.add_parser("build", help="mutate snapshots and write healing rows")
    b.add_argument("--snapshots", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--variants", type=int, default=12)
    b.add_argument("--targets", type=int, default=14, help="sampled steps per variant")
    b.add_argument("--topk", type=int, default=40)
    b.add_argument("--deletion", choices=("uniform", "lookalike"), default="lookalike")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--dist", help="change distribution JSON (default: bundled)")
    b.add_argument("--workers", type=int, default=1)
    b.add_argument("--pages", help="comma-separated page keys (default: all)")
    b.set_defaults(fn=_build)

    s = sub.add_parser("split", help="assign pages to train/val/test by site group")
    s.add_argument("--snapshots", nargs="+", required=True)
    s.add_argument("--config", required=True, help="split rules JSON")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=_split)

    k = sub.add_parser("check", help="dataset gates")
    k.add_argument("--data", required=True, help="build output directory")
    k.add_argument("--manifest", help="split manifest, for the holdout gate")
    k.add_argument("--rebuild", help="snapshot dir: rebuild a few pages and compare bytes")
    k.add_argument("--rebuild-pages", type=int, default=3)
    k.set_defaults(fn=_check)

    bl = sub.add_parser("fit-gate", help="fit the learned-gate baseline on train rows")
    bl.add_argument("--data", required=True, help="build output holding the train split")
    bl.add_argument("--manifest", required=True)
    bl.add_argument("--out", required=True)
    bl.set_defaults(fn=_fit_gate)

    t = sub.add_parser("train", help="QLoRA fine-tune the reranker (GPU)")
    t.add_argument("--data", required=True)
    t.add_argument("--manifest", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--base", default=DEFAULT_BASE)
    t.add_argument("--revision", default=DEFAULT_REVISION)
    t.add_argument("--epochs", type=float, default=2.0)
    t.add_argument("--lora-r", type=int, default=16)
    t.add_argument("--max-len", type=int, default=4096)
    t.add_argument("--lr", type=float, default=1e-4)
    t.add_argument("--accum", type=int, default=16)
    t.add_argument("--seed", type=int, default=1)
    t.add_argument("--max-rows", type=int, default=0, help="cap training rows (smoke runs)")
    t.set_defaults(fn=_train)

    i = sub.add_parser("infer", help="score rows with the model and dump per-row logits (GPU)")
    i.add_argument("--data", required=True)
    i.add_argument("--manifest", required=True)
    i.add_argument("--split", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--adapter", help="LoRA adapter dir; omit for the untuned base model")
    i.add_argument("--base", default=DEFAULT_BASE)
    i.add_argument("--revision", default=DEFAULT_REVISION)
    i.add_argument("--order", default="shuffle:1", help="'retrieval' or 'shuffle:<seed>'")
    i.add_argument("--max-len", type=int, default=4096)
    i.add_argument("--batch-size", type=int, default=1)
    i.set_defaults(fn=_infer)

    r = sub.add_parser("report", help="tables, bootstrap intervals and the figure")
    r.add_argument("--config", required=True, help="report config JSON")
    r.add_argument("--out", required=True)
    r.add_argument("--root", help="resolve relative paths in the config against this directory")
    r.set_defaults(fn=_report)

    v = sub.add_parser("survey", help="identifier survey of public admin/demo consoles")
    v.add_argument("--urls", required=True)
    v.add_argument("--out", required=True)
    v.set_defaults(fn=_survey)

    rb = sub.add_parser("reprobreak", help="derive the change distribution from ReproBreak")
    rb.add_argument("--csv", required=True)
    rb.add_argument("--out", default="data/distributions/reprobreak_changes.json")
    rb.set_defaults(fn=_reprobreak)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
