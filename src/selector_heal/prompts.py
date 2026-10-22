"""The one prompt builder used by training and inference.

The model answers with a single token: one label per shown candidate (A-Z,
then a-n) or "-" for "none of these". Training and inference both read the
next-token logits at the end of the prompt and compare only those 41 tokens, so
the decision is a choice among labels, not free text.

Candidates are shown in a caller-given order. Training reshuffles every epoch
and inference uses a seeded per-row shuffle, which randomises the mapping from
retrieval rank to display label. If a prompt is too long, candidates are dropped
from the end of the shown order without looking at the labels, in training and
inference alike; a training row whose gold candidates were all dropped is
skipped, never relabelled as "none".
"""
from __future__ import annotations

import hashlib
import random
import re

LABELS = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + \
         [chr(c) for c in range(ord("a"), ord("n") + 1)]
NONE_LABEL = "-"

SYSTEM = (
    "You repair broken steps in recorded web guides. You are shown the element a step "
    "was recorded on, and elements from the changed page. If one of them is the same "
    "control, one that does the same thing for the user, answer with its label only. "
    "If none of them is, answer '-' only. Do not pick a control just because it looks "
    "similar; a wrong pick is worse than answering '-'."
)


def _clean(v, n=40) -> str:
    return re.sub(r"\s+", " ", str(v)).strip()[:n]


def fmt_el(e: dict, ctx_heading: str = "") -> str:
    bits = [f"tag={e.get('tag', '')}"]
    name = _clean(e.get("name") or "")
    text = _clean(e.get("text") or "")
    if name:
        bits.append(f"name={name}")
    if text and text != name:
        bits.append(f"text={text}")
    for k in ("role", "id", "testid", "nameAttr"):
        if e.get(k):
            bits.append(f"{k}={_clean(e[k], 30)}")
    href = e.get("href")
    if href:
        tail = str(href).rstrip("/").split("/")[-1] or str(href)
        bits.append(f"href={_clean(tail, 30)}")
    head = _clean(e.get("heading") or "", 30)
    if head and head != ctx_heading:
        bits.append(f"sec={head}")
    b = e.get("bbox")
    if b:
        bits.append(f"box=({b[0]},{b[1]},{b[2]},{b[3]})")
    return " ".join(bits)


def user_prompt(row: dict, order: list[int]) -> str:
    ctx = _clean(row["step"].get("heading") or "", 30)
    lines = ["Recorded element:", fmt_el(row["step"]), "", "Elements on the changed page:"]
    for slot, ci in enumerate(order[: len(LABELS)]):
        lines.append(f"[{LABELS[slot]}] {fmt_el(row['candidates'][ci], ctx)}")
    lines += ["", "Answer:"]
    return "\n".join(lines)


def gold_slots(row: dict, order: list[int]) -> list[int]:
    gold = set(row.get("gold_ids") or [])
    return [slot for slot, ci in enumerate(order[: len(LABELS)])
            if row["candidates"][ci]["track"] in gold]


def display_order(row: dict, how: str) -> list[int]:
    """'retrieval' keeps score order; 'shuffle:<seed>' is a per-row seeded permutation."""
    n = min(len(row["candidates"]), len(LABELS))
    order = list(range(n))
    if how == "retrieval":
        return order
    if how.startswith("shuffle:"):
        seed = how.split(":", 1)[1]
        h = hashlib.blake2b(f"{seed}|{row['row_id']}".encode(), digest_size=8).digest()
        random.Random(int.from_bytes(h, "big")).shuffle(order)
        return order
    raise ValueError(f"unknown order {how!r}")


class PromptEncoder:
    """Chat-template encoding plus the label token ids, checked at the answer boundary."""

    def __init__(self, tokenizer, max_len: int):
        self.tok = tokenizer
        self.max_len = max_len
        probe = self._prefix_ids("x")
        ids = []
        for lab in LABELS + [NONE_LABEL]:
            full = self.tok(self._text("x") + lab, add_special_tokens=False)["input_ids"]
            if full[: len(probe)] != probe or len(full) != len(probe) + 1:
                raise SystemExit(f"label {lab!r} is not a single token after the answer prefix; "
                                 "the label-logit decision would be wrong for this tokenizer")
            ids.append(full[-1])
        self.label_ids, self.none_id = ids[:-1], ids[-1]

    def _text(self, user: str) -> str:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def _prefix_ids(self, user: str) -> list[int]:
        return self.tok(self._text(user), add_special_tokens=False)["input_ids"]

    def encode(self, row: dict, order: list[int]) -> tuple[list[int], list[int], bool]:
        """Token ids for the prompt, the order actually shown, and whether it was cut."""
        order = list(order[: len(LABELS)])
        ids = self._prefix_ids(user_prompt(row, order))
        cut = False
        while len(ids) > self.max_len and len(order) > 1:
            drop = max(1, (len(ids) - self.max_len) // 40)
            order = order[:-drop]
            ids = self._prefix_ids(user_prompt(row, order))
            cut = True
        return ids, order, cut
