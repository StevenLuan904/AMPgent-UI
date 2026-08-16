"""Add multi-target run branches and durable stage checkpoints.

Revision ID: 0014_multitarget_run_control
Revises: 0013_formal_submission_exact_once
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_multitarget_run_control"
down_revision = "0013_formal_submission_exact_once"
branch_labels = None
depends_on = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "experiment_run_target_branches",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_order", sa.Integer(), nullable=False),
        sa.Column("branch_key", sa.String(length=128), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("panel_role", sa.String(length=32), nullable=False),
        sa.Column("qualification_witness_sha256", sa.String(length=64), nullable=False),
        sa.Column("coordinate_sha256", sa.String(length=64), nullable=False),
        sa.Column("native_pocket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wrong_pocket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_namespace", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "native_pocket_id <> wrong_pocket_id",
            name="ck_run_target_branch_distinct_pockets",
        ),
        sa.ForeignKeyConstraint(["native_pocket_id"], ["target_pockets.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"]),
        sa.ForeignKeyConstraint(["wrong_pocket_id"], ["target_pockets.id"]),
        sa.PrimaryKeyConstraint("run_id", "branch_order"),
        sa.UniqueConstraint("run_id", "branch_key", name="uq_run_target_branch_key"),
        sa.UniqueConstraint("run_id", "evidence_namespace", name="uq_run_target_branch_namespace"),
        sa.UniqueConstraint("run_id", "target_id", name="uq_run_target_branch_target"),
    )
    op.create_index(
        "ix_run_target_branch_status",
        "experiment_run_target_branches",
        ["run_id", "status"],
    )
    op.create_table(
        "run_stage_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_name", sa.String(length=64), nullable=False),
        sa.Column("stage_order", sa.Integer(), nullable=False),
        sa.Column("observation_no", sa.Integer(), nullable=False),
        sa.Column("durable_count", sa.Integer(), nullable=False),
        sa.Column("expected_durable_count", sa.Integer(), nullable=False),
        sa.Column("stage_status", sa.String(length=32), nullable=False),
        sa.Column("controller_action", sa.String(length=64), nullable=False),
        sa.Column("reasons_json", _json(), nullable=False),
        sa.Column("tasks_json", _json(), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "stage_name",
            "observation_no",
            name="uq_run_stage_checkpoint_observation",
        ),
    )
    op.create_index(
        "ix_run_stage_checkpoint_latest",
        "run_stage_checkpoints",
        ["run_id", "stage_order", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_stage_checkpoint_latest", table_name="run_stage_checkpoints")
    op.drop_table("run_stage_checkpoints")
    op.drop_index("ix_run_target_branch_status", table_name="experiment_run_target_branches")
    op.drop_table("experiment_run_target_branches")
