"""Add target/control isolated v38 structure evidence records.

Revision ID: 0015_multitarget_structure_evidence
Revises: 0014_multitarget_run_control
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_multitarget_structure_evidence"
down_revision = "0014_multitarget_run_control"
branch_labels = None
depends_on = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "multitarget_structure_evidence_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_namespace", sa.String(length=255), nullable=False),
        sa.Column("control_lane", sa.String(length=32), nullable=False),
        sa.Column("boltz_seed", sa.BigInteger(), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("decoy_ordinal", sa.Integer(), nullable=False),
        sa.Column("task_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("score_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "control_lane IN ('native', 'wrong_pocket')",
            name="ck_multitarget_structure_control_lane",
        ),
        sa.CheckConstraint(
            "(evidence_kind = 'boltz_pose' AND decoy_ordinal = -1) OR "
            "(evidence_kind = 'rosetta_decoy' AND decoy_ordinal >= 0)",
            name="ck_multitarget_structure_evidence_kind_ordinal",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "candidate_id",
            "target_id",
            "control_lane",
            "boltz_seed",
            "evidence_kind",
            "decoy_ordinal",
            name="uq_multitarget_structure_evidence_identity",
        ),
    )
    op.create_index(
        "ix_multitarget_structure_evidence_branch",
        "multitarget_structure_evidence_records",
        ["run_id", "target_id", "control_lane", "evidence_kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_multitarget_structure_evidence_branch",
        table_name="multitarget_structure_evidence_records",
    )
    op.drop_table("multitarget_structure_evidence_records")
