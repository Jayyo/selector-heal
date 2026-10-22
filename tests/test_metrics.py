from selector_heal.metrics import curve, gated, outcome, select_threshold, tally


def row(present, gold=("g",), group="a"):
    return {"successor": "PRESENT" if present else "ABSENT",
            "gold_ids": list(gold) if present else [], "group": group}


def test_outcomes_and_denominators():
    rows = [row(True), row(True), row(True), row(False), row(False)]
    preds = ["g", None, "x", None, "y"]
    assert [outcome(r, p) for r, p in zip(rows, preds)] == ["C", "M", "W", "A", "F"]
    t = tally(rows, preds)
    assert (t["S"], t["D"], t["attempts"]) == (3, 2, 3)
    assert t["wrong_per_attempt"] == 2 / 3
    assert t["successor_recall"] == 1 / 3 and t["deletion_detection"] == 1 / 2


def test_empty_denominators_are_none_not_zero():
    t = tally([row(True)], [None])
    assert t["wrong_per_attempt"] is None and t["deletion_detection"] is None


def test_curve_covers_everything_at_lowest_threshold():
    rows = [row(True), row(True), row(False), row(True)]
    scores = [3.0, 1.0, 2.0, 1.0]
    answers = ["g", "g", "z", "x"]
    pts = curve(rows, scores, answers)
    assert pts[-1]["coverage"] == 1.0
    assert [p["tau"] for p in pts] == [3.0, 2.0, 1.0]
    # tie at 1.0 is accepted together
    assert pts[-1]["attempts"] == 4


def test_threshold_rule():
    rows = [row(True)] * 8 + [row(False)] * 2
    scores = [9, 8, 7, 6, 5, 4, 3, 2, 1.5, 1]
    answers = ["g"] * 7 + ["x", "z", "z"]
    # lowest threshold whose wrong-per-attempt stays within 0 with >= 3 attempts
    assert select_threshold(rows, scores, answers, budget=0.0, min_attempts=3) == 3
    assert select_threshold(rows, scores, answers, budget=0.0, min_attempts=9) is None
    preds = gated(rows, scores, answers, 3)
    assert tally(rows, preds)["W"] == 0
