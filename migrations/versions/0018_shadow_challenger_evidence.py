"""Add queryable candidate-level shadow/challenger evidence dimensions.

Revision ID: 0018_shadow_challenger_evidence
Revises: 0017_artifact_location_witnesses
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_shadow_challenger_evidence"
down_revision = "0017_artifact_location_witnesses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evaluations",
        sa.Column("subject_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("evaluations", sa.Column("evidence_role", sa.String(32), nullable=True))
    op.add_column("evaluations", sa.Column("evidence_family", sa.String(64), nullable=True))
    op.add_column(
        "evaluations", sa.Column("model_release_key", sa.String(128), nullable=True)
    )
    op.add_column(
        "evaluations", sa.Column("applicability_status", sa.String(32), nullable=True)
    )
    op.add_column(
        "evaluations", sa.Column("conflict_status", sa.String(64), nullable=True)
    )
    op.create_foreign_key(
        "fk_evaluation_subject_run",
        "evaluations",
        "experiment_runs",
        ["subject_run_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_evaluation_evidence_role",
        "evaluations",
        "evidence_role IS NULL OR evidence_role IN "
        "('primary', 'hard_gate', 'shadow', 'challenger', 'structure', 'md')",
    )
    op.create_check_constraint(
        "ck_evaluation_applicability_status",
        "evaluations",
        "applicability_status IS NULL OR applicability_status IN "
        "('applicable', 'not_applicable', 'runtime_unavailable', 'failed')",
    )
    op.create_check_constraint(
        "ck_evaluation_conflict_status",
        "evaluations",
        "conflict_status IS NULL OR conflict_status IN "
        "('no_conflict', 'cross_model_disagreement_retained', 'not_assessed')",
    )
    op.create_index(
        "ix_evaluation_shadow_challenger_lookup",
        "evaluations",
        ["subject_run_id", "candidate_id", "evidence_role", "model_release_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_shadow_challenger_lookup", table_name="evaluations")
    op.drop_constraint(
        "ck_evaluation_conflict_status", "evaluations", type_="check"
    )
    op.drop_constraint(
        "ck_evaluation_applicability_status", "evaluations", type_="check"
    )
    op.drop_constraint("ck_evaluation_evidence_role", "evaluations", type_="check")
    op.drop_constraint("fk_evaluation_subject_run", "evaluations", type_="foreignkey")
    op.drop_column("evaluations", "conflict_status")
    op.drop_column("evaluations", "applicability_status")
    op.drop_column("evaluations", "model_release_key")
    op.drop_column("evaluations", "evidence_family")
    op.drop_column("evaluations", "evidence_role")
    op.drop_column("evaluations", "subject_run_id")
