"""Add immutable Agent decision nodes and typed operation edges."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_agent_decision_graph"
down_revision = "0006_tool_call_dependencies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("decision_type", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column("agent_version", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=False),
        sa.Column("structured_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["prompt_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["response_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_decision_run_generation",
        "agent_decisions",
        ["run_id", "generation"],
    )
    op.create_table(
        "agent_decision_tool_call_edges",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("decision_id", "tool_call_id", "direction", "relation_type"),
    )


def downgrade() -> None:
    op.drop_table("agent_decision_tool_call_edges")
    op.drop_index("ix_agent_decision_run_generation", table_name="agent_decisions")
    op.drop_table("agent_decisions")
