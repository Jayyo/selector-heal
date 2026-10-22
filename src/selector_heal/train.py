"""QLoRA fine-tuning of the candidate chooser.

The loss reads the next-token logits at the end of the prompt -- exactly what
inference reads -- restricted to the shown labels plus "-":

    loss = logsumexp(all valid labels) - logsumexp(acceptable labels)

i.e. -log of the total probability of every acceptable answer. A row with
several equivalent gold elements credits any of them; a row with no acceptable
candidate (removed element, or the successor fell outside the top-K) targets "-".

The loop is written out instead of using a Trainer so that this is the only
place the loss is defined and nothing is computed on the answer token itself.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .prompts import PromptEncoder, display_order, gold_slots
from .splits import load_split

DEFAULT_BASE = "Qwen/Qwen2.5-3B-Instruct"
DEFAULT_REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"


@dataclass
class TrainConfig:
    data: str
    manifest: str
    out: str
    base: str = DEFAULT_BASE
    revision: str | None = DEFAULT_REVISION
    epochs: float = 2.0
    lora_r: int = 16
    max_len: int = 4096
    lr: float = 1e-4
    accum: int = 16
    warmup: float = 0.03
    seed: int = 1
    max_rows: int = 0


def load_rows(data_dirs: str, manifest: Path, split: str) -> list[dict]:
    splits = load_split(manifest)
    rows = []
    for d in str(data_dirs).split(","):
        for line in (Path(d) / "rows.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            if splits.get(r["page"]) == split:
                rows.append(r)
    return rows


def target_slots(row: dict, shown: list[int]) -> list[int] | None:
    """Indices into [shown labels..., '-'] that count as correct; None = unusable row."""
    if row["successor"] == "PRESENT" and row["retrieval"] == "HIT":
        g = gold_slots(row, shown)
        return g or None        # gold cut from the prompt: skip, never relabel as '-'
    return [len(shown)]


def load_model(base: str, revision: str | None, adapter: str | None = None, trainable=False):
    """4-bit base model, prepared the same way for training and for scoring.

    `prepare_model_for_kbit_training` keeps norms and embeddings in fp32. Doing it
    only for training would score with a different numeric path than the one
    the adapter was trained on.
    """
    import torch
    from peft import prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tok = AutoTokenizer.from_pretrained(base, revision=revision)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(
        base, revision=revision, quantization_config=bnb, dtype=dtype,
        device_map={"": 0}, attn_implementation="sdpa")
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=trainable,
        gradient_checkpointing_kwargs={"use_reentrant": False} if trainable else None)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter, is_trainable=trainable)
    return tok, model, dtype


def label_logits(model, input_ids, attention_mask, position_ids, token_ids):
    """fp32 logits of `token_ids` at the last position, shape (batch, len(token_ids)).

    Reads the backbone's final hidden state and multiplies it by just those rows
    of the output matrix in fp32. Full-vocabulary bf16 logits have a step of about
    0.125 at this scale, which turns near-equal candidates into exact ties that
    the display order then breaks.
    """
    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    h = base.model(input_ids=input_ids, attention_mask=attention_mask,
                   position_ids=position_ids, use_cache=False).last_hidden_state[:, -1]
    w = base.lm_head.weight[token_ids]
    return h.float() @ w.float().T


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def train(cfg: TrainConfig):
    import torch
    from peft import LoraConfig, get_peft_model

    out = Path(cfg.out)
    out.mkdir(parents=True, exist_ok=True)
    random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)

    rows = load_rows(cfg.data, Path(cfg.manifest), "train")
    if cfg.max_rows:
        rows = sorted(rows, key=lambda r: r["row_id"])
        random.Random(cfg.seed).shuffle(rows)
        rows = rows[: cfg.max_rows]
    n_none = sum(1 for r in rows if not (r["successor"] == "PRESENT" and r["retrieval"] == "HIT"))
    print(f"train rows {len(rows)} ({n_none} target '-'), hosts {len({r['host'] for r in rows})}")

    tok, model, dtype = load_model(cfg.base, cfg.revision, trainable=True)
    enc = PromptEncoder(tok, cfg.max_len)
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(
        r=cfg.lora_r, lora_alpha=2 * cfg.lora_r, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()

    token_ids = torch.tensor(enc.label_ids + [enc.none_id], device="cuda")
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=cfg.lr, weight_decay=0.0)
    n_examples = int(len(rows) * cfg.epochs)
    total = max(1, math.ceil(n_examples / cfg.accum))
    warm = max(1, int(total * cfg.warmup))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(1.0, s / total))))

    def step():
        # rows skipped inside a window must not change the per-row weight
        if pending != cfg.accum:
            for p in params:
                if p.grad is not None:
                    p.grad.mul_(cfg.accum / pending)
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)

    model.train()
    seen = skipped = cut_rows = pending = updates = 0
    running, t0, epoch = [], time.time(), 0
    while seen < n_examples:
        rng = random.Random(f"{cfg.seed}|epoch|{epoch}")
        order_rows = sorted(rows, key=lambda r: r["row_id"])
        rng.shuffle(order_rows)
        for row in order_rows:
            if seen >= n_examples:
                break
            seen += 1
            ids, shown, cut = enc.encode(row, display_order(row, f"shuffle:{cfg.seed}-{epoch}"))
            tgt = target_slots(row, shown)
            if tgt is None:
                skipped += 1
                continue
            cut_rows += cut
            x = torch.tensor([ids], device="cuda")
            pos = torch.arange(x.shape[1], device="cuda").unsqueeze(0)
            lz = label_logits(model, x, torch.ones_like(x), pos, token_ids)[0]
            z = torch.cat([lz[: len(shown)], lz[-1:]])
            mask = torch.zeros_like(z, dtype=torch.bool)
            mask[tgt] = True
            loss = torch.logsumexp(z, 0) - torch.logsumexp(z[mask], 0)
            (loss / cfg.accum).backward()
            running.append(loss.item())
            pending += 1
            if pending == cfg.accum:
                step()
                pending = 0
                updates += 1
                if updates % 10 == 0:
                    el = time.time() - t0
                    print(f"step {updates}/{total} epoch {epoch} loss {sum(running)/len(running):.4f} "
                          f"lr {sched.get_last_lr()[0]:.2e} {seen/el:.2f} rows/s", flush=True)
                    running = []
        epoch += 1
    if pending:
        step()
        updates += 1

    model.save_pretrained(out / "adapter")
    tok.save_pretrained(out / "adapter")
    meta = {"config": asdict(cfg), "rows": len(rows), "examples_seen": seen,
            "optimizer_steps": updates, "skipped_gold_cut": skipped, "prompts_cut": cut_rows,
            "train_hosts": sorted({r["host"] for r in rows}),
            "train_pages": sorted({r["page"] for r in rows}),
            "data_sha256": {d: _sha256(Path(d) / "rows.jsonl") for d in str(cfg.data).split(",")},
            "minutes": round((time.time() - t0) / 60, 1), "dtype": str(dtype)}
    (out / "train_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"saved {out / 'adapter'}  ({meta['minutes']} min, skipped {skipped}, cut {cut_rows})")
