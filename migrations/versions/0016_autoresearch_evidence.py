"""Add typed AutoResearch actions, lineage, deltas, archives, and checkpoints.

Revision ID: 0016_autoresearch_evidence
Revises: 0015_multitarget_structure_evidence
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_autoresearch_evidence"
down_revision = "0015_multitarget_structure_evidence"
branch_labels = None
depends_on = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "autoresearch_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("iteration_no", sa.Integer(), nullable=False),
        sa.Column("branch_key", sa.String(length=128), nullable=False),
        sa.Column("action_ordinal", sa.Integer(), nullable=False),
        sa.Column("action_kind", sa.String(length=32), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("agent_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rationale_text", sa.Text(), nullable=False),
        sa.Column("expected_objectives_json", _json(), nullable=False),
        sa.Column("forbidden_changes_json", _json(), nullable=False),
        sa.Column("action_spec_json", _json(), nullable=False),
        sa.Column("action_sha256", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "iteration_no >= 0",
            name="autoresearch_action_nonnegative_iteration",
        ),
        sa.CheckConstraint(
            "action_ordinal > 0",
            name="autoresearch_action_positive_ordinal",
        ),
        sa.CheckConstraint(
            "action_kind IN ('point_edit', 'controlled_mix', 'de_novo')",
            name="autoresearch_action_kind",
        ),
        sa.ForeignKeyConstraint(["agent_decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_sha256"),
        sa.UniqueConstraint(
            "run_id",
            "iteration_no",
            "branch_key",
            "action_ordinal",
            name="uq_autoresearch_action_slot",
        ),
    )
    op.create_index(
        "ix_autoresearch_action_iteration",
        "autoresearch_actions",
        ["run_id", "iteration_no", "branch_key"],
    )

    op.create_table(
        "candidate_lineage_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("relation_role", sa.String(length=32), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_spans_json", _json(), nullable=False),
        sa.Column("edge_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "source_ordinal > 0",
            name="candidate_lineage_positive_ordinal",
        ),
        sa.CheckConstraint(
            "relation_role IN ("
            "'de_novo_origin', 'primary_parent', 'donor', 'backbone', 'target_module'"
            ")",
            name="candidate_lineage_role",
        ),
        sa.CheckConstraint(
            "(relation_role = 'de_novo_origin' AND parent_candidate_id IS NULL) OR "
            "(relation_role <> 'de_novo_origin' AND parent_candidate_id IS NOT NULL)",
            name="candidate_lineage_parent_semantics",
        ),
        sa.CheckConstraint(
            "parent_candidate_id IS NULL OR child_candidate_id <> parent_candidate_id",
            name="candidate_lineage_not_self",
        ),
        sa.ForeignKeyConstraint(["action_id"], ["autoresearch_actions.id"]),
        sa.ForeignKeyConstraint(["child_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edge_sha256"),
        sa.UniqueConstraint(
            "action_id",
            "child_candidate_id",
            "relation_role",
            "source_ordinal",
            name="uq_candidate_lineage_action_child_source",
        ),
    )
    op.create_index(
        "ix_candidate_lineage_child",
        "candidate_lineage_edges",
        ["child_candidate_id"],
    )
    op.create_index(
        "ix_candidate_lineage_parent",
        "candidate_lineage_edges",
        ["parent_candidate_id"],
    )

    op.create_table(
        "autoresearch_metric_deltas",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comparator_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("parent_evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comparison_kind", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("numeric_delta", sa.Float(), nullable=True),
        sa.Column("improved", sa.Boolean(), nullable=True),
        sa.Column("comparison_json", _json(), nullable=False),
        sa.Column("delta_sha256", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "child_candidate_id <> comparator_candidate_id",
            name="autoresearch_metric_delta_distinct_candidates",
        ),
        sa.CheckConstraint(
            "parent_evaluation_id <> child_evaluation_id",
            name="autoresearch_metric_delta_distinct_evaluations",
        ),
        sa.CheckConstraint(
            "comparison_kind IN ('numeric_delta', 'categorical_transition')",
            name="autoresearch_metric_delta_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('minimize', 'maximize', 'audit', 'categorical')",
            name="autoresearch_metric_delta_direction",
        ),
        sa.CheckConstraint(
            "(comparison_kind = 'numeric_delta' AND numeric_delta IS NOT NULL) OR "
            "(comparison_kind = 'categorical_transition' AND numeric_delta IS NULL)",
            name="autoresearch_metric_delta_value_semantics",
        ),
        sa.ForeignKeyConstraint(["action_id"], ["autoresearch_actions.id"]),
        sa.ForeignKeyConstraint(["child_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["child_evaluation_id"], ["evaluations.id"]),
        sa.ForeignKeyConstraint(["comparator_candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["parent_evaluation_id"], ["evaluations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delta_sha256"),
        sa.UniqueConstraint(
            "action_id",
            "child_candidate_id",
            "comparator_candidate_id",
            "metric_name",
            name="uq_autoresearch_metric_delta_identity",
        ),
    )
    op.create_index(
        "ix_autoresearch_metric_delta_child",
        "autoresearch_metric_deltas",
        ["child_candidate_id", "metric_name"],
    )

    op.create_table(
        "autoresearch_archive_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("iteration_no", sa.Integer(), nullable=False),
        sa.Column("branch_key", sa.String(length=128), nullable=False),
        sa.Column("archive_name", sa.String(length=128), nullable=False),
        sa.Column("previous_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "iteration_no >= 0",
            name="autoresearch_archive_nonnegative_iteration",
        ),
        sa.ForeignKeyConstraint(["previous_version_id"], ["autoresearch_archive_versions.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["snapshot_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "iteration_no",
            "branch_key",
            "archive_name",
            name="uq_autoresearch_archive_version_identity",
        ),
    )
    op.create_index(
        "ix_autoresearch_archive_latest",
        "autoresearch_archive_versions",
        ["run_id", "branch_key", "archive_name", "iteration_no"],
    )

    op.create_table(
        "autoresearch_archive_memberships",
        sa.Column("archive_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("change_kind", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("member_ordinal", sa.Integer(), nullable=True),
        sa.Column("source_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("witness_candidate_ids_json", _json(), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "change_kind IN ('add', 'retain', 'remove')",
            name="autoresearch_archive_membership_change",
        ),
        sa.CheckConstraint(
            "(change_kind = 'remove' AND is_active = false AND member_ordinal IS NULL) OR "
            "(change_kind IN ('add', 'retain') AND is_active = true AND member_ordinal > 0)",
            name="autoresearch_archive_membership_state",
        ),
        sa.ForeignKeyConstraint(["archive_version_id"], ["autoresearch_archive_versions.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["source_action_id"], ["autoresearch_actions.id"]),
        sa.PrimaryKeyConstraint("archive_version_id", "candidate_id"),
        sa.UniqueConstraint(
            "archive_version_id",
            "member_ordinal",
            name="uq_autoresearch_archive_member_ordinal",
        ),
    )
    op.create_index(
        "ix_autoresearch_archive_membership_candidate",
        "autoresearch_archive_memberships",
        ["candidate_id"],
    )

    op.create_table(
        "autoresearch_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("iteration_no", sa.Integer(), nullable=False),
        sa.Column("run_stage_checkpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_before_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_after_sha256", sa.String(length=64), nullable=False),
        sa.Column("score_all_candidate_count", sa.Integer(), nullable=False),
        sa.Column("score_all_required_metric_count", sa.Integer(), nullable=False),
        sa.Column("score_all_expected_evaluation_count", sa.Integer(), nullable=False),
        sa.Column("score_all_completed_evaluation_count", sa.Integer(), nullable=False),
        sa.Column("next_controller_action", sa.String(length=64), nullable=False),
        sa.Column("replay_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("replay_sha256", sa.String(length=64), nullable=False),
        sa.Column("replay_verified", sa.Boolean(), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", _json(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "iteration_no >= 0",
            name="autoresearch_checkpoint_nonnegative_iteration",
        ),
        sa.CheckConstraint(
            "score_all_candidate_count > 0 AND score_all_required_metric_count > 0",
            name="autoresearch_checkpoint_positive_score_all_counts",
        ),
        sa.CheckConstraint(
            "score_all_expected_evaluation_count = "
            "score_all_candidate_count * score_all_required_metric_count",
            name="autoresearch_checkpoint_expected_score_all_count",
        ),
        sa.CheckConstraint(
            "score_all_completed_evaluation_count = score_all_expected_evaluation_count",
            name="autoresearch_checkpoint_complete_score_all",
        ),
        sa.CheckConstraint(
            "replay_verified = true",
            name="autoresearch_checkpoint_replay_verified",
        ),
        sa.ForeignKeyConstraint(["agent_decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["replay_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["run_stage_checkpoint_id"], ["run_stage_checkpoints.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_sha256"),
        sa.UniqueConstraint("run_stage_checkpoint_id"),
        sa.UniqueConstraint(
            "run_id",
            "iteration_no",
            name="uq_autoresearch_checkpoint_iteration",
        ),
    )


def downgrade() -> None:
    op.drop_table("autoresearch_checkpoints")
    op.drop_index(
        "ix_autoresearch_archive_membership_candidate",
        table_name="autoresearch_archive_memberships",
    )
    op.drop_table("autoresearch_archive_memberships")
    op.drop_index(
        "ix_autoresearch_archive_latest",
        table_name="autoresearch_archive_versions",
    )
    op.drop_table("autoresearch_archive_versions")
    op.drop_index(
        "ix_autoresearch_metric_delta_child",
        table_name="autoresearch_metric_deltas",
    )
    op.drop_table("autoresearch_metric_deltas")
    op.drop_index("ix_candidate_lineage_parent", table_name="candidate_lineage_edges")
    op.drop_index("ix_candidate_lineage_child", table_name="candidate_lineage_edges")
    op.drop_table("candidate_lineage_edges")
    op.drop_index("ix_autoresearch_action_iteration", table_name="autoresearch_actions")
    op.drop_table("autoresearch_actions")
