import uuid

from pepagent.seven_branch_top_up_cli import (
    _advance_past_excluded_attempt,
    _excluded_controller_is_recoverable,
)
from pepagent.workflows.seven_branch_top_up import summarize_top_up_receipts


def _completed(action: str = "quota_complete") -> dict[str, object]:
    return {
        "status": "cumulative_selection_persisted",
        "cumulative": {"top_up_plan": {"action": action}},
    }


def test_failed_branch_does_not_discard_completed_siblings() -> None:
    receipts = [
        _completed(),
        {"status": "failed_successor_required", "branch_key": "vegfa"},
        _completed(),
    ]

    failed, completed, successor_required, status = summarize_top_up_receipts(receipts)

    assert len(failed) == 1
    assert len(completed) == 2
    assert successor_required is True
    assert status == "partial_success_successor_required"


def test_completed_branch_can_request_successor_without_failure() -> None:
    failed, completed, successor_required, status = summarize_top_up_receipts(
        [_completed("freeze_successor_round")]
    )

    assert failed == []
    assert len(completed) == 1
    assert successor_required is True
    assert status == "successor_top_up_required"


def test_complete_epoch_needs_no_successor() -> None:
    failed, completed, successor_required, status = summarize_top_up_receipts([_completed()])

    assert failed == []
    assert len(completed) == 1
    assert successor_required is False
    assert status == "epoch_branch_quotas_complete"


def test_all_failed_epoch_is_not_reported_as_partial_success() -> None:
    failed, completed, successor_required, status = summarize_top_up_receipts(
        [
            {"status": "failed_successor_required", "branch_key": "acea"},
            {"status": "failed_successor_required", "branch_key": "vegfa"},
        ]
    )

    assert len(failed) == 2
    assert completed == []
    assert successor_required is True
    assert status == "all_branches_failed_successor_required"


def test_legacy_all_failed_succeeded_controller_is_recoverable() -> None:
    assert _excluded_controller_is_recoverable(
        controller_status="succeeded", child_statuses=["failed", "failed"]
    )
    assert not _excluded_controller_is_recoverable(
        controller_status="succeeded", child_statuses=["failed", "succeeded"]
    )
    assert not _excluded_controller_is_recoverable(
        controller_status="succeeded", child_statuses=[]
    )


def test_recovery_advances_seed_round_without_reusing_failed_outputs() -> None:
    normalized = {"next_round_ordinal": 1}
    controller_id = uuid.uuid4()
    child_id = uuid.uuid4()

    _advance_past_excluded_attempt(
        normalized,
        excluded_attempt_controller_run_id=controller_id,
        excluded_round_ordinal=1,
        excluded_run_id=child_id,
    )

    assert normalized == {
        "next_round_ordinal": 2,
        "excluded_attempt_controller_run_id": str(controller_id),
        "excluded_attempt_run_ids": [str(child_id)],
        "excluded_attempt_outputs_reused": False,
    }
