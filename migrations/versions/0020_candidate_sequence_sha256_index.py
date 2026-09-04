"""Add a global candidate sequence hash lookup index.

Revision ID: 0020_candidate_sequence_sha256_index
Revises: 0019_merge_shadow_skill_evolution
"""

from alembic import op

revision = "0020_candidate_sequence_sha256_index"
down_revision = "0019_merge_shadow_skill_evolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_candidate_sequence_sha256 ON candidates(sequence_sha256)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_candidate_sequence_sha256")
