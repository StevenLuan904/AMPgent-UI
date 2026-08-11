"""Add the database identity for exact-once formal submissions.

Revision ID: 0013_formal_submission_exact_once
Revises: 0012_de_novo_candidate_occurrences
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_formal_submission_exact_once"
down_revision = "0012_de_novo_candidate_occurrences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiment_runs",
        sa.Column("formal_submission_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_experiment_runs_formal_submission_key",
        "experiment_runs",
        ["formal_submission_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_experiment_runs_formal_submission_key",
        "experiment_runs",
        type_="unique",
    )
    op.drop_column("experiment_runs", "formal_submission_key")
