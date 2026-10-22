"""Score rows with the model and write one line of raw decision logits per row.

Everything downstream (the model's own abstain point, validation-chosen
thresholds, risk-coverage curves, order flips) is computed from these dumps, so
the GPU pass runs once per (split, order). `gate_margin` is
max(candidate logits) - logit('-'): the model answers exactly when it is >= 0.

Batch size defaults to 1, like training. With a 4-bit model in bf16, putting
rows in a batch of 4 moved label logits by up to about one unit even with no
padding, which is enough to flip decisions near a threshold.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .prompts import PromptEncoder, display_order
from .train import DEFAULT_BASE, DEFAULT_REVISION, label_logits, load_model, load_rows


def row_fingerprint(r: dict) -> str:
    """What a dump line was computed from: the row's step and its candidate list."""
    payload = json.dumps([r["row_id"], r["step"], [c["track"] for c in r["candidates"]]],
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def infer(data: Path, manifest: Path, split: str, out: Path, adapter: str | None,
          base: str = DEFAULT_BASE, revision: str | None = DEFAULT_REVISION,
          order: str = "shuffle:1", max_len: int = 4096, batch_size: int = 1):
    import torch

    if adapter:
        meta_path = Path(adapter).parent / "train_meta.json"
        if meta_path.exists():
            trained = json.loads(meta_path.read_text(encoding="utf-8"))["config"]
            if (trained["base"], trained["revision"]) != (base, revision):
                raise SystemExit(f"adapter was trained on {trained['base']}@{trained['revision']}, "
                                 f"not {base}@{revision}")
    rows = load_rows(str(data), manifest, split)
    ids_ = [r["row_id"] for r in rows]
    if len(ids_) != len(set(ids_)):
        raise SystemExit("duplicate row ids in the data")
    tok, model, _ = load_model(base, revision, adapter=adapter)
    tok.padding_side = "left"
    model.eval()
    enc = PromptEncoder(tok, max_len)
    token_ids = torch.tensor(enc.label_ids + [enc.none_id], device="cuda")

    items = []
    for r in rows:
        ids, shown, cut = enc.encode(r, display_order(r, order))
        items.append((r, ids, shown, cut))
    items.sort(key=lambda x: len(x[1]))

    results = {}
    t0 = time.time()
    with torch.no_grad():
        for b in range(0, len(items), batch_size):
            batch = items[b:b + batch_size]
            width = max(len(x[1]) for x in batch)
            ids = torch.full((len(batch), width), tok.pad_token_id, dtype=torch.long)
            att = torch.zeros((len(batch), width), dtype=torch.long)
            for i, (_, seq, _, _) in enumerate(batch):
                ids[i, width - len(seq):] = torch.tensor(seq)
                att[i, width - len(seq):] = 1
            pos = (att.cumsum(-1) - 1).clamp(min=0)
            lz = label_logits(model, ids.cuda(), att.cuda(), pos.cuda(), token_ids)
            for i, (r, seq, shown, cut) in enumerate(batch):
                cand = lz[i, : len(shown)]
                none = float(lz[i, -1])
                if len(shown):
                    k = int(cand.argmax())
                    forced = r["candidates"][shown[k]]["track"]
                    margin = float(cand.max()) - none
                else:  # nothing on the page to choose from
                    forced, margin = None, float("-inf")
                gold = set(r.get("gold_ids") or [])
                results[r["row_id"]] = {
                    "row_id": r["row_id"], "row_fp": row_fingerprint(r), "order": order,
                    "shown": shown, "logits": [float(x) for x in cand.tolist()],
                    "none_logit": none, "gate_margin": margin,
                    "forced": forced, "pred": forced if margin >= 0 else None,
                    "gold_shown": bool(gold & {r["candidates"][c]["track"] for c in shown}),
                    "prompt_cut": cut, "prompt_tokens": len(seq),
                }
            if (b // batch_size) % 200 == 0:
                print(f"  {min(b + batch_size, len(items))}/{len(items)} "
                      f"{(b + len(batch)) / (time.time() - t0):.2f} rows/s", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda r: r["row_id"]):
            f.write(json.dumps(results[r["row_id"]], ensure_ascii=False) + "\n")
    meta = {"data_sha256": hashlib.sha256((Path(data) / "rows.jsonl").read_bytes()).hexdigest(),
            "split": split, "order": order, "base": base, "revision": revision,
            "adapter": Path(adapter).name if adapter else None,
            "rows": len(rows), "max_len": max_len, "batch_size": batch_size,
            "prompts_cut": sum(1 for x in items if x[3]),
            "gold_not_shown": sum(1 for r in rows
                                  if r["gold_ids"] and r["retrieval"] == "HIT"
                                  and not results[r["row_id"]]["gold_shown"]),
            "seconds": round(time.time() - t0, 1)}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} rows, {meta['seconds']} s)")
