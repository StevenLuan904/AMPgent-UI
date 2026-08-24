from __future__ import annotations

import hashlib
import inspect

import pytest

from pepagent.model_workers.pepmlm_target_conditional_cli import validate_request
from pepagent.workers.activities import (
    persist_seven_branch_target_sequence,
    score_seven_branch_target_sequence,
)
from pepagent.workers.v38_temporal_worker import V38_ROLE_CONFIG
from pepagent.workflows.seven_branch_design import SevenBranchPeptideDesignWorkflow


def _sha(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _request() -> dict:
    target_sequence = "ACDEFGHIKLMNPQRSTVWY"
    peptide_sequence = "KLLKLLKKLLK"
    return {
        "target": {
            "target_key": "acea",
            "accession": "NP_418439.1",
            "sequence": target_sequence,
            "sequence_sha256": _sha(target_sequence),
        },
        "peptides": [
            {
                "candidate_id": "11111111-1111-1111-1111-111111111111",
                "sequence": peptide_sequence,
                "sequence_sha256": _sha(peptide_sequence),
            }
        ],
    }


def test_target_conditional_request_preserves_pair_identities() -> None:
    target, peptides = validate_request(_request())
    assert target["target_key"] == "acea"
    assert peptides[0]["candidate_id"] == "11111111-1111-1111-1111-111111111111"


def test_target_conditional_request_rejects_hash_drift_and_duplicates() -> None:
    drifted = _request()
    drifted["target"]["sequence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sequence_sha256 mismatch"):
        validate_request(drifted)

    duplicate = _request()
    duplicate["peptides"].append(dict(duplicate["peptides"][0]))
    with pytest.raises(ValueError, match="must be unique"):
        validate_request(duplicate)


def test_v39_target_sequence_worker_and_control_persistence_are_registered() -> None:
    queue, activities, workflows = V38_ROLE_CONFIG["v39-target-sequence"]
    assert queue == "pepagent-gpu-target-sequence-v39"
    assert workflows == []
    assert score_seven_branch_target_sequence in activities
    _, control_activities, _ = V38_ROLE_CONFIG["v38-control"]
    assert persist_seven_branch_target_sequence in control_activities


def test_seven_branch_workflow_orders_score_all_before_target_score_and_checkpoint() -> None:
    _, activities, workflows = V38_ROLE_CONFIG["v38-control"]
    assert SevenBranchPeptideDesignWorkflow in workflows
    registered = {item.__temporal_activity_definition.name for item in activities}
    assert {
        "load_seven_branch_target_score_cohort",
        "persist_seven_branch_target_sequence",
        "persist_seven_branch_round_progress",
        "mark_run_cancelled",
    } <= registered
    source = inspect.getsource(SevenBranchPeptideDesignWorkflow.run)
    child = source.index('"V38SequenceFirstAgentWorkflow"')
    target = source.index('"score_seven_branch_target_sequence"')
    persistence = source.index('"persist_seven_branch_target_sequence"')
    checkpoint = source.index('"persist_seven_branch_round_progress"')
    child_success = source.index('"branch_sequence_and_target_evidence_complete"')
    assert child < target < persistence < checkpoint < child_success
