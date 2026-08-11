"""Preserve every proposal occurrence before candidate deduplication."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_candidate_occurrences"
down_revision = "0008_research_experience_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurrence_rank", sa.Integer(), nullable=False),
        sa.Column("occurrence_kind", sa.String(length=32), nullable=False),
        sa.Column("opaque_arm_label", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Text(), nullable=False),
        sa.Column("sequence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tool_call_id",
            "occurrence_rank",
            name="uq_candidate_occurrence_call_rank",
        ),
    )
    op.create_index(
        "ix_candidate_occurrence_run_label",
        "candidate_occurrences",
        ["run_id", "opaque_arm_label"],
    )
    op.create_index(
        "ix_candidate_occurrence_run_sequence",
        "candidate_occurrences",
        ["run_id", "sequence_sha256"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_occurrence_run_sequence", table_name="candidate_occurrences"
    )
    op.drop_index(
        "ix_candidate_occurrence_run_label", table_name="candidate_occurrences"
    )
    op.drop_table("candidate_occurrences")
