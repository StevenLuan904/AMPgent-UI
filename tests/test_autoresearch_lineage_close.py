from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    analysis_dir = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis_dir))
    spec = importlib.util.spec_from_file_location(
        "_autoresearch_lineage_close",
        analysis_dir / "autoresearch_lineage_close.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_close_accepts_previous_update_as_next_snapshot() -> None:
    module = _load_module()
    snapshot = {"schema_version": "ampgent.autoresearch-archive-snapshot.1"}

    assert module._archive_snapshot_payload(snapshot) is snapshot
    assert module._archive_snapshot_payload({"current": snapshot}) is snapshot


def test_lineage_close_selects_only_full_scored_plan_actions() -> None:
    module = _load_module()
    plan = {
        "actions": [
            {"action_sha256": "a" * 64},
            {"action_sha256": "b" * 64},
            {"action_sha256": "c" * 64},
        ]
    }

    selected, skipped = module._full_scored_action_payloads(plan, {"b" * 64})

    assert selected == [{"action_sha256": "b" * 64}]
    assert skipped == 2
