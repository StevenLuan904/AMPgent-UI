"""Persist target qualification audits and deterministic panel-selection lineage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_target_qualification_lineage"
down_revision = "0010_harness_evolution_lineage"
branch_labels = None
depends_on = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "target_qualification_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_scope_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("shortlist_order", sa.Integer(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_key", sa.String(length=128), nullable=False),
        sa.Column("organism_and_strain", sa.Text(), nullable=False),
        sa.Column("sequence_accession", sa.String(length=128), nullable=False),
        sa.Column("sequence_entry_version", sa.String(length=64), nullable=False),
        sa.Column("sequence_admission_basis", sa.String(length=128), nullable=False),
        sa.Column("sequence_sha256", sa.String(length=64), nullable=False),
        sa.Column("sequence_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_evidence_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("structure_source_type", sa.String(length=64), nullable=False),
        sa.Column("coordinate_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "structure_validation_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "sequence_structure_mapping_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("primary_pocket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("wrong_pocket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("primary_pocket_grade", sa.String(length=8), nullable=True),
        sa.Column(
            "primary_pocket_definition_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "wrong_pocket_definition_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("audit_status", sa.String(length=32), nullable=False),
        sa.Column("rejection_reasons_json", _json(), nullable=False),
        sa.Column("diversity_vector_json", _json(), nullable=True),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "primary_pocket_id IS NULL OR wrong_pocket_id IS NULL "
            "OR primary_pocket_id <> wrong_pocket_id",
            name="target_qualification_distinct_pockets",
        ),
        sa.CheckConstraint(
            "shortlist_order > 0",
            name="target_qualification_positive_order",
        ),
        sa.ForeignKeyConstraint(["audit_decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["audit_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["audit_tool_call_id"], ["tool_calls.id"]),
        sa.ForeignKeyConstraint(["coordinate_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["feature_evidence_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["primary_pocket_definition_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["primary_pocket_id"], ["target_pockets.id"]),
        sa.ForeignKeyConstraint(["sequence_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["sequence_structure_mapping_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["source_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["structure_validation_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"]),
        sa.ForeignKeyConstraint(["wrong_pocket_definition_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["wrong_pocket_id"], ["target_pockets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "audit_scope_id",
            "shortlist_order",
            name="uq_target_qualification_scope_order",
        ),
        sa.UniqueConstraint(
            "audit_scope_id",
            "target_key",
            name="uq_target_qualification_scope_key",
        ),
        sa.UniqueConstraint(
            "audit_scope_id",
            "target_id",
            name="uq_target_qualification_scope_target",
        ),
    )
    op.create_index(
        "ix_target_qualification_scope_status",
        "target_qualification_audits",
        ["audit_scope_id", "audit_status"],
    )
    op.create_table(
        "target_panel_selection_witnesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_scope_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("selection_method", sa.String(length=128), nullable=False),
        sa.Column("selection_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_new_target_count", sa.Integer(), nullable=False),
        sa.Column("target_names_selected_before_audit", sa.Boolean(), nullable=False),
        sa.Column(
            "peptide_or_structure_outcomes_used_for_selection",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("target_agnostic_amp_lane_retained", sa.Boolean(), nullable=False),
        sa.Column("acea_anchor_vector_json", _json(), nullable=False),
        sa.Column("acea_anchor_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "selection_witness_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("snapshot_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "requested_new_target_count BETWEEN 3 AND 5",
            name="target_panel_requested_count_range",
        ),
        sa.ForeignKeyConstraint(["acea_anchor_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["selection_decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["selection_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["selection_tool_call_id"], ["tool_calls.id"]),
        sa.ForeignKeyConstraint(["selection_witness_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["snapshot_artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_scope_id"),
    )
    op.create_index(
        "ix_target_panel_selection_status",
        "target_panel_selection_witnesses",
        ["selection_status"],
    )
    op.create_table(
        "target_panel_selection_members",
        sa.Column(
            "selection_witness_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("selection_rank", sa.Integer(), nullable=False),
        sa.Column("target_audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("selection_rank > 0", name="target_panel_member_positive_rank"),
        sa.ForeignKeyConstraint(["selection_witness_id"], ["target_panel_selection_witnesses.id"]),
        sa.ForeignKeyConstraint(["target_audit_id"], ["target_qualification_audits.id"]),
        sa.PrimaryKeyConstraint("selection_witness_id", "selection_rank"),
        sa.UniqueConstraint(
            "selection_witness_id",
            "target_audit_id",
            name="uq_target_panel_selection_member_audit",
        ),
    )


def downgrade() -> None:
    op.drop_table("target_panel_selection_members")
    op.drop_index(
        "ix_target_panel_selection_status",
        table_name="target_panel_selection_witnesses",
    )
    op.drop_table("target_panel_selection_witnesses")
    op.drop_index(
        "ix_target_qualification_scope_status",
        table_name="target_qualification_audits",
    )
    op.drop_table("target_qualification_audits")
