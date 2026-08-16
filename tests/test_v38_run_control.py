from datetime import UTC, datetime, timedelta

from pepagent.v38_run_control import (
    StageProgressObservation,
    assess_run_control,
    build_default_run_control_plan,
)

NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


def _observation(**updates: object) -> StageProgressObservation:
    payload: dict[str, object] = {
        "observed_at": NOW,
        "stage": "sequence_metrics",
        "stage_started_at": NOW - timedelta(minutes=20),
        "last_durable_progress_at": NOW - timedelta(minutes=2),
        "durable_count": 1000,
        "previous_durable_count": 900,
        "queue_backlog": 20,
        "active_owned_slots": 5,
        "required_poller_count": 1,
        "database_healthy": True,
        "temporal_healthy": True,
        "object_store_healthy": True,
        "evidence_integrity_ok": True,
        "exact_identity_ok": True,
        "allowed_capacity_available": False,
        "allowed_capacity_consecutive_confirmations": 0,
        "active_attempt_count": 5,
    }
    payload.update(updates)
    return StageProgressObservation.model_validate(payload)


def test_default_controller_has_sequence_first_stages_and_two_hour_review() -> None:
    plan = build_default_run_control_plan()
    names = [item.stage for item in plan.stages]
    assert names.index("sequence_admission") < names.index("parallel_target_structure")
    assert plan.activity_heartbeat_seconds == 30
    assert plan.operator_review_seconds == 7200
    assert plan.allowed_idle_capacity_confirmations == 2
    assert plan.structure_before_sequence_admission_forbidden is True


def test_controller_advances_only_on_durable_stage_count() -> None:
    plan = build_default_run_control_plan(evaluation_count=1100)
    decision = assess_run_control(plan, _observation(durable_count=1100))
    assert decision.action == "advance_stage"
    assert "persist_stage_completion_receipt" in decision.tasks


def test_controller_fails_closed_on_evidence_or_identity_drift() -> None:
    plan = build_default_run_control_plan()
    decision = assess_run_control(plan, _observation(evidence_integrity_ok=False))
    assert decision.action == "fail_closed"
    assert decision.resubmit_forbidden is True


def test_controller_repairs_missing_owned_worker_without_resubmission() -> None:
    plan = build_default_run_control_plan()
    decision = assess_run_control(plan, _observation(required_poller_count=0))
    assert decision.action == "repair_owned_worker"
    assert "verify_exact_worker_ownership" in decision.tasks
    assert decision.resubmit_forbidden is True


def test_controller_detects_stall_from_durable_progress_not_wall_clock_alone() -> None:
    plan = build_default_run_control_plan()
    stalled = assess_run_control(
        plan,
        _observation(last_durable_progress_at=NOW - timedelta(minutes=16)),
    )
    assert stalled.action == "diagnose_stage_stall"
    progressing = assess_run_control(
        plan,
        _observation(
            stage_started_at=NOW - timedelta(hours=2),
            last_durable_progress_at=NOW - timedelta(minutes=2),
        ),
    )
    assert progressing.action == "continue"


def test_controller_scales_only_after_two_verified_allowed_capacity_checks() -> None:
    plan = build_default_run_control_plan()
    first = assess_run_control(
        plan,
        _observation(
            queue_backlog=30,
            allowed_capacity_available=True,
            allowed_capacity_consecutive_confirmations=1,
        ),
    )
    assert first.action == "continue"
    second = assess_run_control(
        plan,
        _observation(
            queue_backlog=30,
            allowed_capacity_available=True,
            allowed_capacity_consecutive_confirmations=2,
        ),
    )
    assert second.action == "scale_allowed_capacity"


def test_structure_stage_waits_without_authorized_gpu_instead_of_wasting_work() -> None:
    plan = build_default_run_control_plan()
    decision = assess_run_control(
        plan,
        _observation(
            stage="parallel_target_structure",
            durable_count=0,
            active_owned_slots=0,
            queue_backlog=144,
        ),
    )
    assert decision.action == "wait_for_allowed_capacity"
    assert "retain_backlog_without_dispatch_loss" in decision.tasks
