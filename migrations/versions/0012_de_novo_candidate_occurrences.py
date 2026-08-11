"""Allow proposal occurrences without a parent for de novo generators.

Revision ID: 0012_de_novo_candidate_occurrences
Revises: 0011_target_qualification_lineage
"""

from alembic import op

revision = "0012_de_novo_candidate_occurrences"
down_revision = "0011_target_qualification_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("candidate_occurrences", "parent_candidate_id", nullable=True)
    op.create_check_constraint(
        "ck_candidate_occurrence_parent_semantics",
        "candidate_occurrences",
        "(occurrence_kind = 'de_novo' AND parent_candidate_id IS NULL) OR "
        "(occurrence_kind <> 'de_novo' AND parent_candidate_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM candidate_occurrences WHERE parent_candidate_id IS NULL
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade: de novo candidate occurrences have no parent';
          END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_candidate_occurrence_parent_semantics",
        "candidate_occurrences",
        type_="check",
    )
    op.alter_column("candidate_occurrences", "parent_candidate_id", nullable=False)
