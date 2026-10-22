from selector_heal.prompts import LABELS, PromptEncoder, display_order, gold_slots, user_prompt


def make_row(n=12, gold=("t3",)):
    return {"row_id": "p:0:t3", "gold_ids": list(gold),
            "step": {"tag": "button", "name": "Save", "heading": "Profile"},
            "candidates": [{"track": f"t{i}", "tag": "button", "name": f"Button {i}",
                            "heading": "Profile", "bbox": [i, i, 10, 10]} for i in range(n)]}


def test_display_order_is_a_seeded_permutation():
    r = make_row()
    a, b = display_order(r, "shuffle:1"), display_order(r, "shuffle:1")
    assert a == b and sorted(a) == list(range(12))
    assert display_order(r, "shuffle:2") != a
    assert display_order(r, "retrieval") == list(range(12))


def test_gold_follows_the_element_not_the_slot():
    r = make_row()
    order = display_order(r, "shuffle:1")
    slot = gold_slots(r, order)
    assert len(slot) == 1 and order[slot[0]] == 3
    text = user_prompt(r, order)
    assert f"[{LABELS[slot[0]]}] tag=button name=Button 3" in text


class CharTokenizer:
    """One token per character, with a chat template that mimics the real layout."""

    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=True):
        return "".join(f"<{m['role']}>{m['content']}" for m in msgs) + "<assistant>"

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": [ord(c) for c in text]}


def test_too_long_prompts_drop_candidates_label_blind():
    tok = CharTokenizer()
    r = make_row(n=40)
    enc = PromptEncoder(tok, max_len=1400)
    order = display_order(r, "shuffle:1")
    ids, shown, cut = enc.encode(r, order)
    assert cut and len(ids) <= 1400
    assert shown == order[: len(shown)]          # only the tail of the shown order is dropped
    assert enc.none_id == ord("-") and len(enc.label_ids) == len(LABELS)
