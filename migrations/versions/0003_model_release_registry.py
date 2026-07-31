"""Add the canonical model-release and release-artifact registry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_model_release_registry"
down_revision = "0002_tool_call_replay_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "model_releases" not in tables:
        op.create_table(
            "model_releases",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("role", sa.String(length=128), nullable=False),
            sa.Column("source_uri", sa.Text(), nullable=False),
            sa.Column("source_revision", sa.String(length=128), nullable=False),
            sa.Column("weights_sha256", sa.String(length=64), nullable=False),
            sa.Column("adapter_version", sa.String(length=128), nullable=False),
            sa.Column("admission_status", sa.String(length=32), nullable=False),
            sa.Column("mlflow_model_name", sa.String(length=255), nullable=True),
            sa.Column("mlflow_model_version", sa.String(length=64), nullable=True),
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
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "name", "source_revision", "weights_sha256", name="uq_model_release_identity"
            ),
        )
    if "model_release_artifacts" not in tables:
        op.create_table(
            "model_release_artifacts",
            sa.Column("model_release_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("role", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
            sa.ForeignKeyConstraint(["model_release_id"], ["model_releases.id"]),
            sa.PrimaryKeyConstraint("model_release_id", "artifact_id", "role"),
        )


def downgrade() -> None:
    op.drop_table("model_release_artifacts")
    op.drop_table("model_releases")
