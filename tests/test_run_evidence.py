from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from pepagent.run_evidence import (
    evidence_stage,
    logical_evidence_prefix,
    order_tool_calls,
    require_existing_posthoc_runs,
)


def _call(number: int, name: str, attempt: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=UUID(int=number), tool_name=name, attempt=attempt)


def _edge(parent: SimpleNamespace, child: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(parent_tool_call_id=parent.id, child_tool_call_id=child.id)


def test_stage_order_places_posthoc_structure_and_md_after_sequence_evidence() -> None:
    calls = [
        _call(4, "pool-a-mmgbsa-analysis"),
        _call(2, "score-all-evaluation"),
        _call(3, "rosetta-interfaceanalyzer"),
        _call(1, "pepflow-generator"),
    ]
    assert [call.id.int for call in order_tool_calls(calls, [])] == [1, 2, 3, 4]


def test_dependencies_override_stage_priority() -> None:
    parent = _call(2, "score-all-evaluation")
    child = _call(1, "pepflow-generator")
    assert order_tool_calls([child, parent], [_edge(parent, child)]) == [parent, child]


def test_cycle_is_rejected() -> None:
    first = _call(1, "score-all")
    second = _call(2, "rosetta")
    with pytest.raises(ValueError, match="cycle"):
        order_tool_calls([first, second], [_edge(first, second), _edge(second, first)])


def test_logical_path_is_anchored_to_original_run_and_candidate() -> None:
    path = logical_evidence_prefix("original-run", stage="md", candidate_id="candidate-1")
    assert path == "runs/original-run/candidates/candidate-1/evidence/70-md/"
    assert evidence_stage(metric_name="rosetta_dg_separated") == "rosetta"
    assert evidence_stage(evidence_family="HemoPI2 challenger") == "challenger"
    assert evidence_stage(tool_name="pool-a-md-interface-ingest") == "md"


def test_new_posthoc_experiment_run_is_forbidden_but_existing_run_can_resume() -> None:
    with pytest.raises(RuntimeError, match="original ExperimentRun"):
        require_existing_posthoc_runs(0, 6)
    with pytest.raises(ValueError, match="partially present"):
        require_existing_posthoc_runs(2, 6)
    require_existing_posthoc_runs(6, 6)
