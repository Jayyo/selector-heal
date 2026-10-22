"""Turn rows, model dumps and the learned gate into tables, intervals and a figure.

Thresholds are chosen once, on validation rows of one deletion condition, with
the frozen rule in metrics.select_threshold, and then applied unchanged to the
test rows of every condition. Nothing here looks at test rows to make a choice.

The report config (JSON) names everything; relative paths resolve against the
directory passed as `root` (default: the config's directory). See
configs/report.json.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .baselines import LogisticGate, fallback_pick, features, top_score, top_track
from .infer import row_fingerprint
from .metrics import curve, gated, group_bootstrap, outcome, select_threshold, tally
from .splits import load_split

METHODS = [
    ("top", "Top retrieved candidate, always answers"),
    ("retrieval_tau", "Top candidate if retrieval score >= tau"),
    ("fallback", "Fallback chain over recorded locators"),
    ("gate", "Learned gate (logistic, 12 features) + top candidate"),
    ("base", "Qwen2.5-3B-Instruct, not fine-tuned"),
    ("model", "Fine-tuned, own abstain point (margin >= 0)"),
    ("model_tau", "Fine-tuned, margin >= tau"),
    ("model_gate_top", "Fine-tuned abstain decision + top candidate"),
]
CI_KEYS = ("successor_recall", "wrong_per_attempt", "deletion_detection", "coverage")


def _load_rows(data: Path, manifest: Path, split: str) -> list[dict]:
    m = json.loads(manifest.read_text(encoding="utf-8"))
    split_of = {p["key"]: p["split"] for p in m["pages"]}
    group_of = {p["key"]: p["group"] for p in m["pages"]}
    rows = []
    for line in (data / "rows.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if split_of.get(r["page"]) == split:
            r["group"] = group_of[r["page"]]
            rows.append(r)
    return sorted(rows, key=lambda r: r["row_id"])


def _load_dump(path: Path | None, rows: list[dict], order: str | None = None) -> dict | None:
    """A model dump, checked against the rows it is about to score."""
    if path is None:
        return None
    if not path.exists():
        raise SystemExit(f"configured dump does not exist: {path}")
    lines = [json.loads(l) for l in path.open(encoding="utf-8")]
    d = {x["row_id"]: x for x in lines}
    if len(d) != len(lines):
        raise SystemExit(f"{path}: duplicate row ids")
    if set(d) != {r["row_id"] for r in rows}:
        raise SystemExit(f"{path}: row ids differ from the evaluated rows "
                         f"({len(d)} in dump, {len(rows)} rows)")
    stale = [r["row_id"] for r in rows if d[r["row_id"]].get("row_fp") != row_fingerprint(r)]
    if stale:
        raise SystemExit(f"{path}: {len(stale)} rows were scored on different data, e.g. {stale[0]}")
    if order and any(x["order"] != order for x in lines):
        raise SystemExit(f"{path}: expected candidate order {order}")
    return d


def _ledger(data: Path, manifest: Path, split: str) -> Counter:
    split_of = load_split(manifest)
    c = Counter()
    for line in (data / "ledger.jsonl").open(encoding="utf-8"):
        l = json.loads(line)
        if split_of.get(l["page"]) == split:
            c[l["status"]] += 1
    return c


def _scores(rows, gate, dump):
    top = [top_track(r) for r in rows]
    out = {"retrieval": ([top_score(r) for r in rows], top),
           "gate": (gate.predict(np.array([features(r) for r in rows], dtype=float)).tolist(), top)}
    if dump:
        out["model"] = ([dump[r["row_id"]]["gate_margin"] for r in rows],
                        [dump[r["row_id"]]["forced"] for r in rows])
    return out


def predictions(rows, gate, taus, model, base) -> dict:
    top = [top_track(r) for r in rows]
    sc = _scores(rows, gate, model)
    p = {"top": top, "fallback": [fallback_pick(r) for r in rows]}
    if taus.get("retrieval") is not None:
        p["retrieval_tau"] = gated(rows, *sc["retrieval"], taus["retrieval"])
    if taus.get("gate") is not None:
        p["gate"] = gated(rows, *sc["gate"], taus["gate"])
    if base:
        p["base"] = [base[r["row_id"]]["pred"] for r in rows]
    if model:
        p["model"] = [model[r["row_id"]]["pred"] for r in rows]
        if taus.get("model") is not None:
            p["model_tau"] = gated(rows, *sc["model"], taus["model"])
        # the model's own answer/abstain decision, with the retrieval winner as the answer
        p["model_gate_top"] = [t if model[r["row_id"]]["pred"] is not None else None
                               for r, t in zip(rows, top)]
    return p


def order_robustness(rows, dumps: list[dict]) -> dict:
    """How much the candidate display order alone moves the model's decisions.

    Rows whose prompt was shortened under some order saw different candidate
    sets, so they are counted separately: only uncut rows isolate the order.
    `element_changes` counts rows where two orders answered with different
    elements (abstentions ignored).
    """
    def flips(idx):
        act = elem = outc = 0
        for i in idx:
            ps = [d[rows[i]["row_id"]]["pred"] for d in dumps]
            act += len({p is None for p in ps}) > 1
            elem += len({p for p in ps if p is not None}) > 1
            outc += len({outcome(rows[i], p) for p in ps}) > 1
        n = len(idx)
        return {"rows": n, "act_abstain_flips": act, "element_changes": elem,
                "outcome_changes": outc,
                "act_abstain_flip_rate": act / n if n else None,
                "outcome_change_rate": outc / n if n else None}

    cut = {i for i, r in enumerate(rows) if any(d[r["row_id"]]["prompt_cut"] for d in dumps)}
    uncut = [i for i in range(len(rows)) if i not in cut]
    return {"orders": [next(iter(d.values()))["order"] for d in dumps],
            "uncut": flips(uncut), "cut": flips(sorted(cut)),
            "per_order": [tally(rows, [d[r["row_id"]]["pred"] for r in rows]) for d in dumps]}


def breakdown(rows, preds: dict) -> dict:
    out = {}
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[("strategy", r["primary"])].append(i)
        by[("break", r["break"])].append(i)
        if r["successor"] == "PRESENT":
            copy = "COPY_CHANGE" in (r.get("target_axes") or [])
            by[("relabelled", "yes" if copy else "no")].append(i)
    for (kind, val), idx in sorted(by.items()):
        sub = [rows[i] for i in idx]
        out.setdefault(kind, {})[val] = {
            m: tally(sub, [preds[m][i] for i in idx]) for m in ("top", "gate", "model") if m in preds}
        out[kind][val]["rows"] = len(idx)
    ranks = Counter()
    for r in rows:
        if r["retrieval"] == "HIT":
            tr = [c["track"] for c in r["candidates"]]
            pos = next(i for i, t in enumerate(tr) if t in set(r["gold_ids"]))
            ranks[str(pos) if pos < 3 else "3+"] += 1
        elif r["successor"] == "PRESENT":
            ranks["not retrieved"] += 1
    out["gold_rank"] = dict(sorted(ranks.items()))
    return out


def per_group(rows, pred) -> dict:
    wrong = Counter()
    groups = {r["group"] for r in rows}
    for r, p in zip(rows, pred):
        if outcome(r, p) in ("W", "F"):
            wrong[r["group"]] += 1
    total = sum(wrong.values())
    top3 = sum(c for _, c in wrong.most_common(3))
    return {"groups": len(groups), "groups_with_wrong": len(wrong), "wrong_total": total,
            "wrong_in_top3_groups": top3, "top_groups": wrong.most_common(5)}


def report(config: Path, out: Path, root: Path | None = None) -> dict:
    cfg = json.loads(config.read_text(encoding="utf-8"))
    root = root or config.parent
    P = lambda p: (root / p) if p else None
    manifest = P(cfg["manifest"])
    gate = LogisticGate.from_json(json.loads(P(cfg["gate"]).read_text(encoding="utf-8")))
    budget, min_att = cfg.get("budget", 0.02), cfg.get("min_attempts", 30)
    order = cfg.get("order", "shuffle:1")

    # thresholds: validation rows of one condition only
    tc = cfg["conditions"][cfg["threshold_condition"]]
    val_rows = _load_rows(P(tc["val"]), manifest, "val")
    val_model = _load_dump(P(tc.get("model", {}).get("val")), val_rows, order)
    taus = {k: select_threshold(val_rows, s, a, budget, min_att)
            for k, (s, a) in _scores(val_rows, gate, val_model).items()}

    summary = {"budget": budget, "min_attempts": min_att, "thresholds": taus,
               "threshold_rows": len(val_rows), "conditions": {}}
    for name, c in cfg["conditions"].items():
        res = {}
        for split in ("val", "test"):
            rows = _load_rows(P(c[split]), manifest, split)
            model = _load_dump(P(c.get("model", {}).get(split)), rows, order)
            base = _load_dump(P(c.get("base", {}).get(split)), rows, order)
            preds = predictions(rows, gate, taus, model, base)
            entry = {"rows": len(rows), "groups": len({r["group"] for r in rows}),
                     "ledger": dict(_ledger(P(c[split]), manifest, split)),
                     "methods": {m: tally(rows, p) for m, p in preds.items()},
                     "missing_operating_points": [k for k in ("retrieval_tau", "gate", "model_tau")
                                                  if k not in preds and (k != "model_tau" or model)]}
            if split == "test":
                entry["ci"] = group_bootstrap(rows, preds, list(CI_KEYS))
                if "model" in preds:
                    for a, b in (("model", "top"), ("model", "gate"), ("model_tau", "gate")):
                        if a not in preds or b not in preds:
                            entry[f"paired_{a}_vs_{b}"] = None
                            continue
                        entry[f"paired_{a}_vs_{b}"] = group_bootstrap(
                            rows, {a: preds[a], b: preds[b]}, list(CI_KEYS),
                            paired=(a, b))["_paired"]
                    entry["per_group_model"] = per_group(rows, preds["model"])
                entry["per_group_top"] = per_group(rows, preds["top"])
                entry["breakdown"] = breakdown(rows, preds)
                sc = _scores(rows, gate, model)
                entry["curves"] = {k: [{x: p[x] for x in ("tau", "coverage", "wrong_per_attempt",
                                                          "successor_recall", "attempts")}
                                       for p in curve(rows, s, a)]
                                   for k, (s, a) in sc.items()}
                entry["at_coverage"] = at_coverage(entry["curves"])
                if c.get("orders"):
                    dumps = [_load_dump(P(o), rows) for o in c["orders"]]
                    names = [next(iter(d.values()))["order"] for d in dumps]
                    if len(set(names)) != len(names) or names[0] != order:
                        raise SystemExit(f"order dumps must be distinct and start with {order}: {names}")
                    entry["order_robustness"] = order_robustness(rows, dumps)
            res[split] = entry
        summary["conditions"][name] = res

    out.mkdir(parents=True, exist_ok=True)
    (out / "tables.md").write_text(tables(summary), encoding="utf-8")
    if cfg.get("figure", True):
        figure(summary, out / "risk_coverage.png")
    for res in summary["conditions"].values():
        for k, pts in res["test"].get("curves", {}).items():
            res["test"]["curves"][k] = _thin(pts, 150)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    print(f"wrote {out}/summary.json, tables.md")
    return summary


def at_coverage(curves: dict, targets=(0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8)) -> dict:
    """Risk and recall of each sweep at the first threshold reaching each coverage."""
    out = {}
    for k, pts in curves.items():
        row = {}
        for t in targets:
            reach = [p for p in pts if p["coverage"] is not None and p["coverage"] >= t]
            if reach:
                p = min(reach, key=lambda p: p["coverage"])
                row[str(t)] = {"coverage": round(p["coverage"], 4),
                               "wrong_per_attempt": p["wrong_per_attempt"],
                               "successor_recall": p["successor_recall"]}
        out[k] = row
    return out


def _thin(pts: list, n: int) -> list:
    """At most about n points, always keeping both ends of the curve."""
    if len(pts) <= n:
        return pts
    step = len(pts) / (n - 1)
    keep = sorted({min(len(pts) - 1, round(i * step)) for i in range(n)})
    return [pts[i] for i in keep]


def _f(x, nd=3):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _ci(ci, key):
    v = (ci or {}).get(key)
    return f" [{v[0]:.3f}, {v[1]:.3f}]" if v else ""


def tables(s: dict) -> str:
    t = s["thresholds"]
    lines = [f"Thresholds chosen on {s['threshold_rows']} validation rows "
             f"(budget {s['budget']:.0%} wrong answers per attempt, at least "
             f"{s['min_attempts']} attempts): "
             + ", ".join(f"{k} {_f(v, 3 if abs(v) < 1 else 2) if v is not None else 'none qualifies'}"
                         for k, v in t.items()), ""]
    for cond, res in s["conditions"].items():
        for split in ("test", "val"):
            e = res[split]
            lines += [f"### {cond} deletions, {split}: {e['rows']} rows from {e['groups']} site groups",
                      "", "| method | answered | correct / S | wrong picks | false repairs / D "
                      "| wrong per attempt | successor recall | deletion detection |",
                      "|---|---|---|---|---|---|---|---|"]
            ci = e.get("ci", {})
            for key, label in METHODS:
                m = e["methods"].get(key)
                if not m:
                    continue
                c = ci.get(key, {})
                lines.append(
                    f"| {label} | {m['attempts']}/{m['n']} ({_f(m['coverage'], 2)}) "
                    f"| {m['C']}/{m['S']} | {m['W']} | {m['F']}/{m['D']} "
                    f"| {_f(m['wrong_per_attempt'])}{_ci(c, 'wrong_per_attempt')} "
                    f"| {_f(m['successor_recall'])}{_ci(c, 'successor_recall')} "
                    f"| {_f(m['deletion_detection'])}{_ci(c, 'deletion_detection')} |")
            lines.append("")
    return "\n".join(lines) + "\n"


def figure(s: dict, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    ink, ink2, grid = "#0F172A", "#475569", "#E2E8F0"
    style = {"model": ("#0046FF", "Fine-tuned 3B, margin sweep"),
             "gate": ("#64748B", "Learned gate, probability sweep"),
             "retrieval": ("#94A3B8", "Retrieval score sweep")}
    family = "Pretendard" if any("Pretendard" in f.name for f in font_manager.fontManager.ttflist) \
        else "DejaVu Sans"
    plt.rcParams.update({"font.family": family, "font.size": 10, "axes.edgecolor": grid,
                         "axes.labelcolor": ink2, "xtick.color": ink2, "ytick.color": ink2})
    titles = {"lookalike": "Look-alike deletions", "uniform": "Uniform deletions"}
    conds = list(s["conditions"])
    fig, axes = plt.subplots(1, len(conds), figsize=(5.4 * len(conds), 4.6), sharey=True)
    axes = np.atleast_1d(axes)
    ymax = 0.0
    for ax, cond in zip(axes, conds):
        e = s["conditions"][cond]["test"]
        ax.axhline(s["budget"], color="#CBD5E1", lw=1.2, zorder=1)
        ax.annotate(f"{s['budget']:.0%} budget, met on validation",
                    (0.12, s["budget"]), xytext=(0, 3), textcoords="offset points",
                    fontsize=8, color=ink2)
        for key in ("retrieval", "gate", "model"):
            pts = [p for p in e["curves"].get(key, []) if p["wrong_per_attempt"] is not None
                   and p["attempts"] >= 10]
            if not pts:
                continue
            color, label = style[key]
            xs = [p["coverage"] for p in pts]
            ys = [p["wrong_per_attempt"] for p in pts]
            ymax = max(ymax, max(ys))
            ax.plot(xs, ys, color=color, lw=2, solid_capstyle="round", label=label, zorder=2)
        m = e["methods"]
        marks = [("top", "Top candidate", "#94A3B8", "^", (-70, 8)),
                 ("fallback", "Fallback chain", "#64748B", "D", (8, 6)),
                 ("model", "Fine-tuned, own abstain", "#0046FF", "o", (10, -14)),
                 ("model_tau", None, "#0046FF", "s", None),
                 ("gate", None, "#64748B", "s", None)]
        for key, label, color, marker, off in marks:
            v = m.get(key)
            if not v or v["coverage"] is None or v["wrong_per_attempt"] is None:
                continue
            ax.scatter([v["coverage"]], [v["wrong_per_attempt"]], s=64, color=color, marker=marker,
                       edgecolor="white", linewidth=2, zorder=3)
            if label:
                ax.annotate(label, (v["coverage"], v["wrong_per_attempt"]), xytext=off,
                            textcoords="offset points", fontsize=8.5, color=ink)
            ymax = max(ymax, v["wrong_per_attempt"])
        if "model_tau" in m and "gate" in m:
            v = m["gate"]
            ax.annotate("thresholds chosen on validation\n(learned gate and fine-tuned 3B)",
                        (v["coverage"], v["wrong_per_attempt"]), xytext=(0.04, 0.58),
                        textcoords="axes fraction", fontsize=8.5, color=ink,
                        arrowprops=dict(arrowstyle="-", color=ink2, lw=1))
        ax.set_title(f"{titles.get(cond, cond)} (test, {e['rows']} rows)", color=ink,
                     fontsize=11, loc="left")
        ax.set_xlabel("coverage (share of broken steps answered)")
        ax.grid(color=grid, lw=1)
        ax.set_axisbelow(True)
        ax.set_xlim(0, 1.02)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    axes[0].set_ylabel("wrong answers per attempt")
    # the untuned model sits far above everything else; it is in the table, not the plot
    axes[0].set_ylim(0, 0.45)
    axes[0].legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=ink)
    fig.tight_layout()
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)
