"""Offline end-to-end run on the hand-made fixtures: no GPU, no network."""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from selector_heal.baselines import fit_and_write  # noqa: E402
from selector_heal.build import BuildConfig, build  # noqa: E402
from selector_heal.collect import collect_local  # noqa: E402
from selector_heal.gates import run_gates  # noqa: E402
from selector_heal.report import report  # noqa: E402
from selector_heal.splits import make_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_pipeline(tmp_path):
    snaps = tmp_path / "snapshots"
    rows = asyncio.run(collect_local(ROOT / "fixtures" / "pages", snaps))
    assert len(rows) == 6 and all("error" not in r for r in rows)

    manifest = tmp_path / "splits.json"
    make_manifest([snaps], ROOT / "configs" / "split_fixtures.json", manifest)

    for cond in ("lookalike", "uniform"):
        m = build(snaps, tmp_path / cond, BuildConfig(variants=3, deletion=cond), workers=2)
        assert m["ledger_counts"].get("HEALING", 0) > 20
        assert not m["page_errors"]
    assert run_gates(tmp_path / "lookalike", manifest, rebuild_snapshots=snaps, rebuild_pages=2)

    fit_and_write(tmp_path / "lookalike", manifest, tmp_path / "gate.json")
    cfg = {"manifest": "splits.json", "gate": "gate.json", "budget": 0.1, "min_attempts": 3,
           "threshold_condition": "lookalike", "figure": False,
           "conditions": {c: {"val": c, "test": c} for c in ("lookalike", "uniform")}}
    (tmp_path / "report.json").write_text(json.dumps(cfg))
    s = report(tmp_path / "report.json", tmp_path / "out")
    top = s["conditions"]["lookalike"]["test"]["methods"]["top"]
    # the top candidate never abstains, so it cannot detect a single deletion
    assert top["coverage"] == 1.0 and top["A"] == 0
    assert (tmp_path / "out" / "tables.md").read_text().count("| method |") == 4
