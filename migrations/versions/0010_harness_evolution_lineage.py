"""Add typed harness release lineage and champion/challenger evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_harness_evolution_lineage"
down_revision = "0009_candidate_occurrences"
branch_labels = None
depends_on = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "harness_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harness_id", sa.String(length=128), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("release_status", sa.String(length=32), nullable=False),
        sa.Column("change_hypothesis", sa.Text(), nullable=False),
        sa.Column("primary_changed_component", sa.String(length=128), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_bundle_sha256", sa.String(length=64), nullable=False),
        sa.Column("tool_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("environment_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("failure_taxonomy_sha256", sa.String(length=64), nullable=False),
        sa.Column("budget_contract_sha256", sa.String(length=64), nullable=False),
        sa.Column("history_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "allowed_evidence_slice_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "forbidden_holdout_manifest_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("endpoint_contract_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rollback_harness_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["allowed_evidence_slice_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["endpoint_contract_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(
            ["forbidden_holdout_manifest_artifact_id"], ["artifacts.id"]
        ),
        sa.ForeignKeyConstraint(
            ["rollback_harness_release_id"], ["harness_releases.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("harness_id"),
    )
    op.create_index(
        "ix_harness_release_scope_status",
        "harness_releases",
        ["scope_id", "release_status"],
    )
    op.create_table(
        "harness_lineage_edges",
        sa.Column("child_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "child_release_id <> parent_release_id",
            name="harness_lineage_not_self",
        ),
        sa.ForeignKeyConstraint(["child_release_id"], ["harness_releases.id"]),
        sa.ForeignKeyConstraint(["parent_release_id"], ["harness_releases.id"]),
        sa.PrimaryKeyConstraint("child_release_id", "parent_release_id", "relation_type"),
    )
    op.create_index(
        "ix_harness_lineage_parent", "harness_lineage_edges", ["parent_release_id"]
    )
    op.create_table(
        "harness_trials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_key", sa.String(length=128), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("champion_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenger_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_trial_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "history_partition_manifest_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("assignment_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blinding_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_contract_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_contract_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("adjudication_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blinded", sa.Boolean(), nullable=False),
        sa.Column("adjudication_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unblinded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "champion_release_id <> challenger_release_id",
            name="harness_trial_distinct_releases",
        ),
        sa.ForeignKeyConstraint(["assignment_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["adjudication_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["blinding_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["budget_contract_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["challenger_release_id"], ["harness_releases.id"]),
        sa.ForeignKeyConstraint(["champion_release_id"], ["harness_releases.id"]),
        sa.ForeignKeyConstraint(["endpoint_contract_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["history_partition_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["parent_trial_id"], ["harness_trials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trial_key"),
    )
    op.create_index(
        "ix_harness_trial_scope_phase", "harness_trials", ["scope_id", "phase"]
    )
    op.create_table(
        "harness_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_key", sa.String(length=128), nullable=False),
        sa.Column("pair_key", sa.String(length=128), nullable=False),
        sa.Column("assigned_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opaque_arm_label", sa.String(length=64), nullable=False),
        sa.Column("assignment_rank", sa.Integer(), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=True),
        sa.Column("resource_class", sa.String(length=64), nullable=False),
        sa.Column("controls_formal_action", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assigned_release_id"], ["harness_releases.id"]),
        sa.ForeignKeyConstraint(["experiment_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["trial_id"], ["harness_trials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trial_id",
            "assignment_rank",
            name="uq_harness_assignment_trial_rank",
        ),
        sa.UniqueConstraint(
            "trial_id",
            "episode_key",
            "assigned_release_id",
            name="uq_harness_assignment_episode_release",
        ),
    )
    op.create_index(
        "ix_harness_assignment_trial_pair",
        "harness_assignments",
        ["trial_id", "pair_key"],
    )
    op.create_table(
        "harness_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_family", sa.String(length=64), nullable=False),
        sa.Column("endpoint_name", sa.String(length=128), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("numeric_value", sa.Float(), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("limitations_json", _json(), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["assignment_id"], ["harness_assignments.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assignment_id",
            "endpoint_family",
            "endpoint_name",
            "tool_call_id",
            name="uq_harness_outcome_evidence",
        ),
    )
    op.create_index(
        "ix_harness_outcome_assignment_family",
        "harness_outcomes",
        ["assignment_id", "endpoint_family"],
    )
    op.create_table(
        "harness_promotion_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prospective_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("counterfactual_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shadow_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("promoted_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rollback_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["counterfactual_trial_id"], ["harness_trials.id"]),
        sa.ForeignKeyConstraint(["decision_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["promoted_release_id"], ["harness_releases.id"]),
        sa.ForeignKeyConstraint(["prospective_trial_id"], ["harness_trials.id"]),
        sa.ForeignKeyConstraint(["rollback_release_id"], ["harness_releases.id"]),
        sa.ForeignKeyConstraint(["shadow_trial_id"], ["harness_trials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_decision_id"),
        sa.UniqueConstraint("prospective_trial_id"),
    )


def downgrade() -> None:
    op.drop_table("harness_promotion_decisions")
    op.drop_index("ix_harness_outcome_assignment_family", table_name="harness_outcomes")
    op.drop_table("harness_outcomes")
    op.drop_index("ix_harness_assignment_trial_pair", table_name="harness_assignments")
    op.drop_table("harness_assignments")
    op.drop_index("ix_harness_trial_scope_phase", table_name="harness_trials")
    op.drop_table("harness_trials")
    op.drop_index("ix_harness_lineage_parent", table_name="harness_lineage_edges")
    op.drop_table("harness_lineage_edges")
    op.drop_index("ix_harness_release_scope_status", table_name="harness_releases")
    op.drop_table("harness_releases")
