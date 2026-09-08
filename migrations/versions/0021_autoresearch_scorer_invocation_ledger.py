"""Add the raw scorer invocation ledger for replayable AutoResearch rounds.

Revision ID: 0021_autoresearch_scorer_invocation_ledger
Revises: 0020_candidate_sequence_sha256_index
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_autoresearch_scorer_invocation_ledger"
down_revision = "0020_candidate_sequence_sha256_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autoresearch_scorer_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_sequence_sha256", sa.String(length=64), nullable=True),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("model_release_key", sa.String(length=128), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_invocation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("ordinal > 0", name="autoresearch_scorer_invocation_positive_ordinal"),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "candidate_id", "metric_name", "ordinal",
            name="uq_autoresearch_scorer_invocation_slot",
        ),
    )
    op.create_index(
        "ix_autoresearch_scorer_invocation_tool_call",
        "autoresearch_scorer_invocations",
        ["tool_call_id"],
    )
    op.create_index(
        "ix_autoresearch_scorer_invocation_run_ordinal",
        "autoresearch_scorer_invocations",
        ["run_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_autoresearch_scorer_invocation_run_ordinal",
        table_name="autoresearch_scorer_invocations",
    )
    op.drop_index(
        "ix_autoresearch_scorer_invocation_tool_call",
        table_name="autoresearch_scorer_invocations",
    )
    op.drop_table("autoresearch_scorer_invocations")
