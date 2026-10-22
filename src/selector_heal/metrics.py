"""Outcomes, rates, the threshold rule and group-level uncertainty.

Every row is one broken step. With S = rows whose element still has an
acceptable successor on the page and D = rows whose element was removed:

    S = C + M + W     correct / missed (abstained) / wrong pick
    D = A + F         correct abstain / false repair
    attempts            = C + W + F
    coverage            = attempts / (S + D)
    wrong per attempt   = (W + F) / attempts
    successor recall    = C / S        (retrieval misses stay in S: they are missed repairs)
    deletion detection  = A / D

Rates with an empty denominator are None, never 0.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

OUTCOMES = ("C", "M", "W", "A", "F")


def outcome(row: dict, pred) -> str:
    if row["successor"] == "PRESENT":
        if pred is None:
            return "M"
        return "C" if pred in set(row["gold_ids"]) else "W"
    return "A" if pred is None else "F"


def _div(a, b):
    return a / b if b else None


def summarize(counts: Counter) -> dict:
    c = {k: counts.get(k, 0) for k in OUTCOMES}
    S, D = c["C"] + c["M"] + c["W"], c["A"] + c["F"]
    att = c["C"] + c["W"] + c["F"]
    return dict(c, S=S, D=D, n=S + D, attempts=att,
                coverage=_div(att, S + D),
                wrong_per_attempt=_div(c["W"] + c["F"], att),
                successor_recall=_div(c["C"], S),
                wrong_pick_rate=_div(c["W"], S),
                deletion_detection=_div(c["A"], D),
                false_repair_rate=_div(c["F"], D))


def tally(rows: list, preds: list) -> dict:
    return summarize(Counter(outcome(r, p) for r, p in zip(rows, preds)))


def gated(rows, scores, answers, tau):
    """Predictions when answering exactly the rows whose score is >= tau."""
    return [a if s >= tau else None for s, a in zip(scores, answers)]


def curve(rows, scores, answers) -> list[dict]:
    """Risk and coverage at every distinct threshold, highest threshold first."""
    idx = sorted(range(len(rows)), key=lambda i: -scores[i])
    out, cnt = [], Counter()
    base = Counter(outcome(rows[i], None) for i in range(len(rows)))  # everything abstained
    for pos, i in enumerate(idx):
        o_none, o_ans = outcome(rows[i], None), outcome(rows[i], answers[i])
        base[o_none] -= 1
        cnt[o_ans] += 1
        last = pos == len(idx) - 1 or scores[idx[pos + 1]] != scores[i]
        if last:
            s = summarize(cnt + base)
            s["tau"] = scores[i]
            out.append(s)
    return out


def select_threshold(rows, scores, answers, budget: float, min_attempts: int):
    """The frozen rule: lowest threshold whose wrong-per-attempt is within budget.

    Chosen on validation rows only. Requires at least `min_attempts` answered
    rows so a lucky handful of attempts cannot qualify. Returns None when no
    threshold qualifies -- a valid result, reported as such.
    """
    best = None
    for p in curve(rows, scores, answers):
        if p["attempts"] >= min_attempts and p["wrong_per_attempt"] is not None \
                and p["wrong_per_attempt"] <= budget:
            best = p["tau"] if best is None else min(best, p["tau"])
    return best


def group_bootstrap(rows, preds_by_method: dict, keys: list[str], B: int = 2000,
                    seed: int = 20261022, paired: tuple | None = None) -> dict:
    """95% percentile intervals from resampling site groups, not rows.

    Rows from one site share a template and errors cluster by site, so treating
    rows as independent gives intervals that are far too narrow. With few groups
    the interval is coarse, and it cannot show failure modes no group exhibits.
    """
    by_group = defaultdict(list)
    for i, r in enumerate(rows):
        by_group[r["group"]].append(i)
    groups = sorted(by_group)
    per = {m: [[outcome(rows[i], p[i]) for i in by_group[g]] for g in groups]
           for m, p in preds_by_method.items()}
    rng = random.Random(seed)
    draws = {m: {k: [] for k in keys} for m in preds_by_method}
    diffs = {k: [] for k in keys}
    for _ in range(B):
        pick = [rng.randrange(len(groups)) for _ in groups]
        stats = {}
        for m in preds_by_method:
            c = Counter()
            for gi in pick:
                c.update(per[m][gi])
            stats[m] = summarize(c)
            for k in keys:
                if stats[m][k] is not None:
                    draws[m][k].append(stats[m][k])
        if paired:
            a, b = paired
            for k in keys:
                if stats[a][k] is not None and stats[b][k] is not None:
                    diffs[k].append(stats[a][k] - stats[b][k])

    def ci(v):
        if len(v) < 20:
            return None
        v = sorted(v)
        return [round(v[int(0.025 * len(v))], 4), round(v[int(0.975 * len(v)) - 1], 4)]

    out = {m: {k: ci(draws[m][k]) for k in keys} for m in preds_by_method}
    if paired:
        out["_paired"] = {"a": paired[0], "b": paired[1], **{k: ci(diffs[k]) for k in keys}}
    out["_groups"] = len(groups)
    return out
