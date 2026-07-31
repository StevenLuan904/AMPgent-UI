"""Persist normalized tool inputs required for exact replay."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_tool_call_replay_input"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_calls",
        sa.Column(
            "input_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("tool_calls", "input_json", server_default=None)


def downgrade() -> None:
    op.drop_column("tool_calls", "input_json")
