from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, UniqueConstraint

from pepagent.db.models import MultiTargetStructureEvidenceRecord
from pepagent.v38_persistence import (
    MultiTargetRunBindingReceipt,
    MultiTargetStructurePersistenceReceipt,
    StageCheckpointReceipt,
    TargetBranchBinding,
)
from pepagent.v38_run_control import RunControlDecision

SHA = "a" * 64


def _branch(order: int) -> TargetBranchBinding:
    return TargetBranchBinding(
        branch_order=order,
        branch_key=f"target-{order}",
        target_id=uuid4(),
        panel_role="reference_anchor" if order == 1 else "qualified_target",
        qualification_witness_sha256=SHA,
        coordinate_sha256=SHA,
        native_pocket_id=uuid4(),
        wrong_pocket_id=uuid4(),
        evidence_namespace=f"target/target-{order}",
    )


def test_multitarget_binding_is_hashable_and_requires_unique_contiguous_branches() -> None:
    receipt = MultiTargetRunBindingReceipt(
        run_id=uuid4(),
        branches=(_branch(1), _branch(2), _branch(3)),
    )
    assert len(receipt.sha256()) == 64
    assert receipt.shared_sequence_cohort_required is True
    assert receipt.peptide_outcomes_used_for_target_selection is False
    with pytest.raises(ValidationError):
        MultiTargetRunBindingReceipt(run_id=uuid4(), branches=(_branch(1), _branch(3)))


def test_stage_checkpoint_binds_controller_decision_and_durable_count() -> None:
    decision = RunControlDecision(
        action="continue",
        reasons=("durable_progress_within_stage_plan",),
        tasks=("schedule_next_progress_check",),
    )
    receipt = StageCheckpointReceipt(
        run_id=uuid4(),
        stage="sequence_metrics",
        stage_order=2,
        observation_no=4,
        durable_count=4500,
        expected_durable_count=8100,
        stage_status="running",
        decision=decision,
        observed_at=datetime(2026, 8, 17, tzinfo=UTC),
    )
    assert len(receipt.sha256()) == 64
    assert receipt.decision.resubmit_forbidden is True


def test_multitarget_structure_table_has_exact_branch_and_decoy_identity() -> None:
    table = MultiTargetStructureEvidenceRecord.__table__
    unique = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_multitarget_structure_evidence_identity"
    )
    assert tuple(column.name for column in unique.columns) == (
        "run_id",
        "candidate_id",
        "target_id",
        "control_lane",
        "boltz_seed",
        "evidence_kind",
        "decoy_ordinal",
    )
    checks = {
        str(constraint.name).removeprefix(
            "ck_multitarget_structure_evidence_records_"
        ): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "native" in checks["ck_multitarget_structure_control_lane"]
    assert "wrong_pocket" in checks["ck_multitarget_structure_control_lane"]
    assert "decoy_ordinal = -1" in checks[
        "ck_multitarget_structure_evidence_kind_ordinal"
    ]


def test_multitarget_structure_persistence_receipt_counts_pose_and_decoys() -> None:
    receipt = MultiTargetStructurePersistenceReceipt(
        run_id=uuid4(),
        task_sha256=SHA,
        target_id=uuid4(),
        candidate_id=uuid4(),
        control_lane="native",
        boltz_seed=20270380,
        boltz_pose_count=1,
        rosetta_decoy_count=16,
        action="persisted",
    )
    assert receipt.boltz_pose_count == 1
    assert receipt.rosetta_decoy_count == 16
    assert len(receipt.sha256()) == 64
