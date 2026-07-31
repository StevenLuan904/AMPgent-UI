"""Allow explicit pocket evidence states without abbreviations."""

import sqlalchemy as sa
from alembic import op

revision = "0005_widen_pocket_status"
down_revision = "0004_pocket_evidence_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "target_pockets",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "target_pockets",
        "status",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
