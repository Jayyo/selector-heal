"""Methods that need no language model.

top_candidate    answer the highest-scoring retrieved candidate (abstains only when
                 nothing was retrieved)
retrieval_tau    the same, but abstain when its score is below a threshold
fallback_chain   replay the step's other recorded locators on the whole changed page,
                 in recorder order, and take the first one that matches exactly one
                 element; only locators that matched the target uniquely at record
                 time are used, and the answer need not be among the candidates
learned_gate     logistic regression over retrieval features predicting "the top
                 candidate is acceptable"; answer the top candidate when p >= threshold

The learned gate asks whether a handful of retrieval features can already tell
when answering with the top candidate is safe. The fine-tuned model is compared
with it on the same rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .retrieval import norm, real_dest, tokens

FEATURES = ("top_score", "gap_to_second", "n_candidates", "same_name", "name_jaccard",
            "same_tag", "same_href", "same_id", "same_testid", "box_shift", "same_heading",
            "top_name_dupes")


def top_track(row):
    return row["candidates"][0]["track"] if row["candidates"] else None


def top_score(row) -> float:
    return row["candidates"][0]["score"] if row["candidates"] else float("-inf")


def fallback_pick(row):
    for f in row.get("fallback") or []:
        if f["n_matches"] == 1:
            return f["hits"][0]
    return None


def features(row) -> list[float]:
    c, step = row["candidates"], row["step"]
    if not c:
        return [0.0] * len(FEATURES)
    top = c[0]
    s1 = top["score"]
    s2 = c[1]["score"] if len(c) > 1 else 0.0
    sn, tn = norm(step.get("name")), norm(top.get("name"))
    a, b = tokens(step.get("name")), tokens(top.get("name"))
    jac = len(a & b) / len(a | b) if a and b else 0.0
    ob, nb = step.get("bbox") or [0, 0, 0, 0], top.get("bbox") or [0, 0, 0, 0]
    shift = min(1.0, (abs(ob[0] - nb[0]) + abs(ob[1] - nb[1])) / 1000.0)
    dupes = sum(1 for x in c if tn and norm(x.get("name")) == tn)
    eq = lambda k: 1.0 if step.get(k) and step.get(k) == top.get(k) else 0.0
    return [s1, s1 - s2, len(c) / 40.0, 1.0 if sn and sn == tn else 0.0, jac,
            1.0 if step.get("tag") == top.get("tag") else 0.0,
            1.0 if real_dest(step) and real_dest(step) == real_dest(top) else 0.0,
            eq("id"), eq("testid"), shift,
            1.0 if step.get("heading") and step.get("heading") == top.get("heading") else 0.0,
            min(dupes, 5) / 5.0]


def gate_label(row) -> int:
    """1 when answering with the top candidate would be correct."""
    return int(row["successor"] == "PRESENT" and top_track(row) in set(row["gold_ids"]))


class LogisticGate:
    def __init__(self, l2: float = 1.0):
        self.l2 = l2

    def fit(self, X: np.ndarray, y: np.ndarray, iters: int = 50):
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        Z = np.hstack([(X - self.mu) / self.sd, np.ones((len(X), 1))])
        w = np.zeros(Z.shape[1])
        reg = np.eye(Z.shape[1]) * self.l2
        reg[-1, -1] = 0.0
        for _ in range(iters):  # Newton steps on the L2-penalised log-likelihood
            p = 1 / (1 + np.exp(-Z @ w))
            g = Z.T @ (p - y) + reg @ w
            H = (Z * (p * (1 - p))[:, None]).T @ Z + reg
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        self.w = w
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        Z = np.hstack([(X - self.mu) / self.sd, np.ones((len(X), 1))])
        return 1 / (1 + np.exp(-Z @ self.w))

    def to_json(self) -> dict:
        return {"features": list(FEATURES), "mean": self.mu.round(6).tolist(),
                "std": self.sd.round(6).tolist(), "weights": self.w.round(6).tolist(),
                "l2": self.l2}

    @classmethod
    def from_json(cls, d: dict) -> "LogisticGate":
        g = cls(d["l2"])
        g.mu, g.sd, g.w = np.array(d["mean"]), np.array(d["std"]), np.array(d["weights"])
        return g


def fit_and_write(data: Path, manifest: Path, out: Path):
    """Fit the gate on train rows and save it; scoring happens in report.py."""
    from .train import load_rows
    rows = load_rows(str(data), manifest, "train")
    X = np.array([features(r) for r in rows], dtype=float)
    y = np.array([gate_label(r) for r in rows], dtype=float)
    gate = LogisticGate().fit(X, y)
    d = gate.to_json()
    d.update({"train_rows": len(rows), "positive_rate": round(float(y.mean()), 4)})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"learned gate fit on {len(rows)} train rows (positive rate {y.mean():.3f}) -> {out}")
