from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "analysis" / "autoresearch_proposal_score_probe.py"
    spec = importlib.util.spec_from_file_location("_autoresearch_proposal_score_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reusable_metric_requires_exact_candidate_order(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "metric.json"
    path.write_text(
        json.dumps(
            {
                "status": "complete",
                "candidate_count": 2,
                "records": [
                    {"candidate_id": "candidate-b"},
                    {"candidate_id": "candidate-a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="identity/order"):
        module._load_reusable_result(
            path,
            candidates=[{"id": "candidate-a"}, {"id": "candidate-b"}],
        )


def test_reusable_unavailable_auxiliary_result_is_auditable(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "metric.json"
    payload = {
        "status": "unavailable",
        "candidate_count": 1,
        "records": [],
        "reason": "optional runtime stopped",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert module._load_reusable_result(
        path,
        candidates=[{"id": "candidate-a"}],
    ) == payload


def test_v39_physicochemical_supplement_covers_formal_pair() -> None:
    module = _module()
    values = module.describe(
        "KLLKLLKLLK",
        ph=7.4,
        c_terminal_amidated=False,
        hydrophobic_moment_angle=100,
    )
    assert values["maximum_hydrophobic_run"] == 2
    assert isinstance(values["guruprasad_instability_index"], float)
