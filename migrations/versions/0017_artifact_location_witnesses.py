"""Add append-only per-edge Artifact location witnesses.

Revision ID: 0017_artifact_location_witnesses
Revises: 0016_autoresearch_evidence
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_artifact_location_witnesses"
down_revision = "0016_autoresearch_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_artifact_locations",
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("location_witness_sha256", sa.String(length=64), nullable=False),
        sa.Column("requested_storage_uri", sa.Text(), nullable=False),
        sa.Column(
            "location_metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id", "artifact_id", "role"],
            [
                "evidence_artifacts.tool_call_id",
                "evidence_artifacts.artifact_id",
                "evidence_artifacts.role",
            ],
            name="fk_evidence_artifact_location_edge",
        ),
        sa.PrimaryKeyConstraint(
            "tool_call_id",
            "artifact_id",
            "role",
            "location_witness_sha256",
            name="pk_evidence_artifact_locations",
        ),
    )
    op.create_index(
        "ix_evidence_artifact_location_witness_sha256",
        "evidence_artifact_locations",
        ["location_witness_sha256"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_artifact_location_witness_sha256",
        table_name="evidence_artifact_locations",
    )
    op.drop_table("evidence_artifact_locations")
