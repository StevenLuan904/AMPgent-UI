from __future__ import annotations

from pepagent.autoresearch_retry_eligibility import (
    RetryEligibilityObservation,
    decide_retry_eligibility,
)


def _observation(**updates: object) -> RetryEligibilityObservation:
    payload: dict[str, object] = {
        "predecessor_run_id": "01b3da2f-8f34-5fbb-ab5a-cd66dd784679",
        "target_key": "angpt1",
        "database_status": "failed",
        "temporal_workflow_id": "workflow-angpt1",
        "temporal_run_id": "c31ef8b8-2251-4987-80da-9338dd225030",
        "observed_temporal_workflow_id": "workflow-angpt1",
        "observed_temporal_run_id": "c31ef8b8-2251-4987-80da-9338dd225030",
        "temporal_status": "FAILED",
        "source_revision": "a" * 40,
    }
    payload.update(updates)
    return RetryEligibilityObservation.model_validate(payload)


def test_terminal_failed_predecessor_is_freeze_eligible_but_not_submittable() -> None:
    decision = decide_retry_eligibility(_observation())

    assert decision.eligible_to_freeze is True
    assert decision.eligible_to_submit is False
    assert "eligible_exact_once_successor_freeze" in decision.reason_codes
    assert "freeze_only_policy" in decision.reason_codes
    assert "new_gpu_tasks_prohibited" in decision.reason_codes
    assert len(decision.successor_identity_seed_sha256) == 64
    assert len(decision.eligibility_sha256) == 64


def test_running_temporal_execution_cannot_freeze_a_successor() -> None:
    decision = decide_retry_eligibility(_observation(temporal_status="RUNNING"))

    assert decision.eligible_to_freeze is False
    assert decision.eligible_to_submit is False
    assert "temporal_execution_not_terminal" in decision.reason_codes


def test_existing_successor_blocks_duplicate_freeze_and_submission() -> None:
    decision = decide_retry_eligibility(
        _observation(successor_run_ids=("53ee9d1c-3348-52a6-9b54-b64d88b4089b",))
    )

    assert decision.eligible_to_freeze is False
    assert decision.eligible_to_submit is False
    assert "existing_successor_present" in decision.reason_codes


def test_submission_requires_explicit_policy_and_gpu_authorization() -> None:
    decision = decide_retry_eligibility(
        _observation(
            execution_policy="submit_allowed",
            new_gpu_tasks_allowed=True,
        )
    )

    assert decision.eligible_to_freeze is True
    assert decision.eligible_to_submit is True
    assert "eligible_exact_once_successor_submission" in decision.reason_codes
    assert "new_gpu_tasks_prohibited" not in decision.reason_codes


def test_temporal_binding_drift_fails_closed() -> None:
    decision = decide_retry_eligibility(
        _observation(observed_temporal_run_id="different-run")
    )

    assert decision.eligible_to_freeze is False
    assert "temporal_run_binding_drifted" in decision.reason_codes
