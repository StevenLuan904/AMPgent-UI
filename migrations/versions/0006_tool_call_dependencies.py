"""Add explicit typed dependencies between experiment attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_tool_call_dependencies"
down_revision = "0005_widen_pocket_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_call_dependencies",
        sa.Column("child_tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["child_tool_call_id"], ["tool_calls.id"]),
        sa.ForeignKeyConstraint(["parent_tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint(
            "child_tool_call_id", "parent_tool_call_id", "relation_type"
        ),
    )
    op.create_index(
        "ix_tool_call_dependency_parent",
        "tool_call_dependencies",
        ["parent_tool_call_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_call_dependency_parent", table_name="tool_call_dependencies"
    )
    op.drop_table("tool_call_dependencies")
