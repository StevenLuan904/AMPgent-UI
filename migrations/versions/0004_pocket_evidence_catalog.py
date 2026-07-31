"""Add canonical target pockets and multi-source pocket evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_pocket_evidence_catalog"
down_revision = "0003_model_release_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "target_pockets" not in tables:
        op.create_table(
            "target_pockets",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("pocket_key", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("pocket_type", sa.String(length=64), nullable=False),
            sa.Column("functional_role", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False),
            sa.Column("evidence_grade", sa.String(length=8), nullable=False),
            sa.Column("evidence_score", sa.Float(), nullable=False),
            sa.Column("conditioning_priority", sa.String(length=32), nullable=False),
            sa.Column("conditioning_enabled", sa.Boolean(), nullable=False),
            sa.Column(
                "residue_indices",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "context_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "limitations_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "metadata_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["target_id"], ["targets.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("target_id", "pocket_key", name="uq_target_pocket_key"),
        )
        op.create_index(
            "ix_target_pocket_conditioning",
            "target_pockets",
            ["conditioning_enabled", "evidence_score"],
        )

    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("pocket_evidence")
    }
    additions = {
        "pocket_id": sa.Column("pocket_id", postgresql.UUID(as_uuid=True), nullable=True),
        "evidence_kind": sa.Column(
            "evidence_kind", sa.String(length=64), server_default="legacy", nullable=False
        ),
        "evidence_grade": sa.Column(
            "evidence_grade", sa.String(length=8), server_default="U", nullable=False
        ),
        "source_accession": sa.Column("source_accession", sa.String(length=128), nullable=True),
        "source_revision_date": sa.Column(
            "source_revision_date", sa.DateTime(timezone=True), nullable=True
        ),
        "retrieved_at": sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        "experimental_method": sa.Column(
            "experimental_method", sa.String(length=128), nullable=True
        ),
        "resolution_angstrom": sa.Column("resolution_angstrom", sa.Float(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("pocket_evidence", column)
    for name in ("chain_ids", "source_residue_indices", "mapping_json", "limitations_json"):
        if name in columns:
            continue
        default = "'[]'::jsonb" if name != "mapping_json" else "'{}'::jsonb"
        op.add_column(
            "pocket_evidence",
            sa.Column(
                name,
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text(default),
                nullable=False,
            ),
        )
        op.alter_column("pocket_evidence", name, server_default=None)

    inspector = sa.inspect(op.get_bind())
    foreign_keys = {item["name"] for item in inspector.get_foreign_keys("pocket_evidence")}
    if "fk_pocket_evidence_pocket_id_target_pockets" not in foreign_keys:
        op.create_foreign_key(
            "fk_pocket_evidence_pocket_id_target_pockets",
            "pocket_evidence",
            "target_pockets",
            ["pocket_id"],
            ["id"],
        )
    indexes = {item["name"] for item in inspector.get_indexes("pocket_evidence")}
    if "ix_pocket_evidence_pocket" not in indexes:
        op.create_index("ix_pocket_evidence_pocket", "pocket_evidence", ["pocket_id"])
    unique_constraints = {
        item["name"] for item in inspector.get_unique_constraints("pocket_evidence")
    }
    if "uq_pocket_evidence_sha256" not in unique_constraints:
        op.create_unique_constraint(
            "uq_pocket_evidence_sha256", "pocket_evidence", ["evidence_sha256"]
        )


def downgrade() -> None:
    op.drop_constraint("uq_pocket_evidence_sha256", "pocket_evidence", type_="unique")
    op.drop_index("ix_pocket_evidence_pocket", table_name="pocket_evidence")
    op.drop_constraint(
        "fk_pocket_evidence_pocket_id_target_pockets",
        "pocket_evidence",
        type_="foreignkey",
    )
    for name in (
        "resolution_angstrom",
        "experimental_method",
        "limitations_json",
        "mapping_json",
        "source_residue_indices",
        "chain_ids",
        "retrieved_at",
        "source_revision_date",
        "source_accession",
        "evidence_grade",
        "evidence_kind",
        "pocket_id",
    ):
        op.drop_column("pocket_evidence", name)
    op.drop_index("ix_target_pocket_conditioning", table_name="target_pockets")
    op.drop_table("target_pockets")
