"""Add evidence-gated Skill Evolution aggregates and action bindings.

Revision ID: 0018_skill_evolution
Revises: 0017_artifact_location_witnesses
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_skill_evolution"
down_revision = "0017_artifact_location_witnesses"
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
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("skill_kind", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False),
        sa.Column("semantic_spec_json", _json(), nullable=False),
        sa.Column("semantic_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_spec_json", _json(), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_effect_spec_json", _json(), nullable=False),
        sa.Column("expected_effect_sha256", sa.String(length=64), nullable=False),
        sa.Column("compiled_ir_json", _json(), nullable=True),
        sa.Column("compiler_version", sa.String(length=128), nullable=True),
        sa.Column("compiler_environment_sha256", sa.String(length=64), nullable=True),
        sa.Column("program_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("family_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("canonical_skill_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supersedes_skill_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("history_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_agent_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("promotion_scope_json", _json(), nullable=True),
        sa.Column("promotion_decision_json", _json(), nullable=True),
        sa.Column("promotion_decision_sha256", sa.String(length=64), nullable=True),
        sa.Column("active_library_sha256", sa.String(length=64), nullable=True),
        sa.Column("planner_policy_json", _json(), nullable=True),
        sa.Column("planner_policy_sha256", sa.String(length=64), nullable=True),
        sa.Column("planner_policy_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "metadata_json",
            _json(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision > 0", name="skill_positive_revision"),
        sa.CheckConstraint("state_version > 0", name="skill_positive_state_version"),
        sa.CheckConstraint(
            "skill_kind IN ("
            "'edit_program', 'search_program', 'search_policy', 'generator_tool', 'composite'"
            ")",
            name="skill_kind",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ("
            "'proposed', 'compiled', 'testing', 'promoted', 'active', "
            "'rejected', 'suspended', 'retired'"
            ")",
            name="skill_lifecycle_state",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('proposed', 'rejected') OR ("
            "compiled_ir_json IS NOT NULL AND compiler_version IS NOT NULL AND "
            "program_fingerprint IS NOT NULL AND family_fingerprint IS NOT NULL"
            ")",
            name="skill_compilation_state",
        ),
        sa.CheckConstraint(
            "lifecycle_state NOT IN ('promoted', 'active', 'suspended', 'retired') OR "
            "(promotion_scope_json IS NOT NULL AND promotion_decision_json IS NOT NULL AND "
            "promotion_decision_sha256 IS NOT NULL)",
            name="skill_promotion_scope",
        ),
        sa.CheckConstraint(
            "lifecycle_state <> 'active' OR active_library_sha256 IS NOT NULL",
            name="skill_active_library",
        ),
        sa.CheckConstraint(
            "(planner_policy_json IS NULL AND planner_policy_sha256 IS NULL AND "
            "planner_policy_artifact_id IS NULL) OR "
            "(planner_policy_json IS NOT NULL AND planner_policy_sha256 IS NOT NULL AND "
            "planner_policy_artifact_id IS NOT NULL)",
            name="skill_planner_policy_binding",
        ),
        sa.CheckConstraint(
            "canonical_skill_revision_id IS NULL OR canonical_skill_revision_id <> id",
            name="skill_canonical_not_self",
        ),
        sa.CheckConstraint(
            "supersedes_skill_revision_id IS NULL OR supersedes_skill_revision_id <> id",
            name="skill_supersedes_not_self",
        ),
        sa.ForeignKeyConstraint(["canonical_skill_revision_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["created_by_agent_decision_id"], ["agent_decisions.id"]),
        sa.ForeignKeyConstraint(["planner_policy_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["source_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["supersedes_skill_revision_id"], ["skills.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("program_fingerprint", name="uq_skill_program_fingerprint"),
        sa.UniqueConstraint("skill_key", "revision", name="uq_skill_key_revision"),
    )
    op.create_index("ix_skill_family_fingerprint", "skills", ["family_fingerprint"])
    op.create_index("ix_skill_state_kind", "skills", ["lifecycle_state", "skill_kind"])

    op.create_table(
        "skill_experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_key", sa.String(length=128), nullable=False),
        sa.Column("skill_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_type", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("testing_state_version", sa.Integer(), nullable=True),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("context_spec_json", _json(), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("hypothesis_json", _json(), nullable=False),
        sa.Column("baseline_program_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("treatment_program_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("history_partition_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "pair_or_seed_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("budget_contract_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint_contract_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evaluator_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blinding_manifest_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("randomization_seed", sa.BigInteger(), nullable=True),
        sa.Column("baseline_scorer_unit_budget", sa.Integer(), nullable=False),
        sa.Column("treatment_scorer_unit_budget", sa.Integer(), nullable=False),
        sa.Column("baseline_atomic_scorer_budget_json", _json(), nullable=False),
        sa.Column("treatment_atomic_scorer_budget_json", _json(), nullable=False),
        sa.Column("baseline_proposal_budget", sa.Integer(), nullable=False),
        sa.Column("treatment_proposal_budget", sa.Integer(), nullable=False),
        sa.Column("baseline_generator_tool_call_budget", sa.Integer(), nullable=False),
        sa.Column("treatment_generator_tool_call_budget", sa.Integer(), nullable=False),
        sa.Column("baseline_scorer_units_consumed", sa.Integer(), nullable=True),
        sa.Column("treatment_scorer_units_consumed", sa.Integer(), nullable=True),
        sa.Column("baseline_atomic_scorer_usage_json", _json(), nullable=True),
        sa.Column("treatment_atomic_scorer_usage_json", _json(), nullable=True),
        sa.Column("baseline_proposals_consumed", sa.Integer(), nullable=True),
        sa.Column("treatment_proposals_consumed", sa.Integer(), nullable=True),
        sa.Column("baseline_generator_tool_calls_consumed", sa.Integer(), nullable=True),
        sa.Column("treatment_generator_tool_calls_consumed", sa.Integer(), nullable=True),
        sa.Column("scorer_usage_ledger_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "budget_shortfall_json",
            _json(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("baseline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("treatment_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("harness_trial_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preregistered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replay_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "denominator_complete",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "replay_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            _json(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _created_at(),
        sa.CheckConstraint(
            "experiment_type IN ('paired_edit', 'campaign_ab')",
            name="skill_experiment_type",
        ),
        sa.CheckConstraint(
            "(experiment_type = 'campaign_ab' AND baseline_run_id IS NOT NULL AND "
            "treatment_run_id IS NOT NULL AND harness_trial_id IS NOT NULL AND "
            "baseline_run_id <> treatment_run_id) OR "
            "(experiment_type = 'paired_edit' AND baseline_run_id IS NULL AND "
            "treatment_run_id IS NULL AND harness_trial_id IS NULL)",
            name="skill_experiment_type_campaign_bindings",
        ),
        sa.CheckConstraint(
            "phase IN ('historical_replay', 'counterfactual', 'shadow', 'prospective')",
            name="skill_experiment_phase",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'preregistered', 'running', 'completed', 'failed', 'invalidated')",
            name="skill_experiment_status",
        ),
        sa.CheckConstraint(
            "baseline_program_fingerprint <> treatment_program_fingerprint",
            name="skill_experiment_distinct_programs",
        ),
        sa.CheckConstraint(
            "baseline_scorer_unit_budget >= 0 AND treatment_scorer_unit_budget >= 0 AND "
            "baseline_proposal_budget >= 0 AND treatment_proposal_budget >= 0 AND "
            "baseline_generator_tool_call_budget >= 0 AND "
            "treatment_generator_tool_call_budget >= 0",
            name="skill_experiment_nonnegative_budgets",
        ),
        sa.CheckConstraint(
            "phase <> 'prospective' OR (testing_state_version IS NOT NULL AND "
            "testing_state_version > 0 AND blinding_manifest_artifact_id IS NOT NULL AND "
            "preregistered_at IS NOT NULL)",
            name="skill_experiment_prospective_testing_version",
        ),
        sa.CheckConstraint(
            "baseline_scorer_units_consumed IS NULL OR baseline_scorer_units_consumed >= 0",
            name="skill_experiment_nonnegative_baseline_scorer_usage",
        ),
        sa.CheckConstraint(
            "treatment_scorer_units_consumed IS NULL OR treatment_scorer_units_consumed >= 0",
            name="skill_experiment_nonnegative_treatment_scorer_usage",
        ),
        sa.CheckConstraint(
            "baseline_proposals_consumed IS NULL OR baseline_proposals_consumed >= 0",
            name="skill_experiment_nonnegative_baseline_proposal_usage",
        ),
        sa.CheckConstraint(
            "treatment_proposals_consumed IS NULL OR treatment_proposals_consumed >= 0",
            name="skill_experiment_nonnegative_treatment_proposal_usage",
        ),
        sa.CheckConstraint(
            "baseline_generator_tool_calls_consumed IS NULL OR "
            "baseline_generator_tool_calls_consumed >= 0",
            name="skill_experiment_nonnegative_baseline_generator_usage",
        ),
        sa.CheckConstraint(
            "treatment_generator_tool_calls_consumed IS NULL OR "
            "treatment_generator_tool_calls_consumed >= 0",
            name="skill_experiment_nonnegative_treatment_generator_usage",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR ("
            "baseline_scorer_units_consumed IS NOT NULL AND "
            "treatment_scorer_units_consumed IS NOT NULL AND "
            "baseline_proposals_consumed IS NOT NULL AND "
            "treatment_proposals_consumed IS NOT NULL AND "
            "baseline_generator_tool_calls_consumed IS NOT NULL AND "
            "treatment_generator_tool_calls_consumed IS NOT NULL AND "
            "baseline_atomic_scorer_usage_json IS NOT NULL AND "
            "treatment_atomic_scorer_usage_json IS NOT NULL AND "
            "scorer_usage_ledger_artifact_id IS NOT NULL AND "
            "result_artifact_id IS NOT NULL AND "
            "replay_artifact_id IS NOT NULL AND "
            "denominator_complete = true AND replay_verified = true"
            ")",
            name="skill_experiment_completed_usage_ledger",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR (receipt_sha256 IS NOT NULL AND finished_at IS NOT NULL)",
            name="skill_experiment_completed_receipt",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR ("
            "scorer_usage_ledger_artifact_id <> result_artifact_id AND "
            "scorer_usage_ledger_artifact_id <> replay_artifact_id AND "
            "result_artifact_id <> replay_artifact_id)",
            name="skill_experiment_distinct_terminal_artifacts",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR phase <> 'prospective' OR ("
            "baseline_scorer_units_consumed = baseline_scorer_unit_budget AND "
            "treatment_scorer_units_consumed = treatment_scorer_unit_budget"
            ")",
            name="skill_experiment_completed_prospective_budget_consumed",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR ("
            "baseline_atomic_scorer_usage_json = baseline_atomic_scorer_budget_json AND "
            "treatment_atomic_scorer_usage_json = treatment_atomic_scorer_budget_json"
            ")",
            name="skill_experiment_completed_atomic_budget_consumed",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR phase <> 'prospective' OR ("
            "baseline_proposals_consumed = baseline_proposal_budget AND "
            "treatment_proposals_consumed = treatment_proposal_budget AND "
            "baseline_generator_tool_calls_consumed = "
            "baseline_generator_tool_call_budget AND "
            "treatment_generator_tool_calls_consumed = "
            "treatment_generator_tool_call_budget"
            ")",
            name="skill_experiment_completed_prospective_proposals_consumed",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR ("
            "history_partition_artifact_id IS NOT NULL AND "
            "pair_or_seed_manifest_artifact_id IS NOT NULL AND "
            "budget_contract_artifact_id IS NOT NULL AND "
            "endpoint_contract_artifact_id IS NOT NULL AND "
            "evaluator_manifest_artifact_id IS NOT NULL"
            ")",
            name="skill_experiment_preregistration_artifacts",
        ),
        sa.ForeignKeyConstraint(["baseline_run_id"], ["experiment_runs.id"]),
        sa.ForeignKeyConstraint(["blinding_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["budget_contract_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["endpoint_contract_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["evaluator_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["harness_trial_id"], ["harness_trials.id"]),
        sa.ForeignKeyConstraint(["history_partition_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["pair_or_seed_manifest_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["replay_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["result_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["scorer_usage_ledger_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["skill_revision_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["treatment_run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_key"),
        sa.UniqueConstraint("receipt_sha256"),
    )
    op.create_index(
        "ix_skill_experiment_skill_phase",
        "skill_experiments",
        ["skill_revision_id", "phase", "testing_state_version", "status"],
    )

    op.create_table(
        "skill_campaign_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("independent_unit_id", sa.String(length=255), nullable=False),
        sa.Column("endpoint_name", sa.String(length=128), nullable=False),
        sa.Column("endpoint_family", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("metric_version", sa.String(length=128), nullable=False),
        sa.Column("initial_pool_sha256", sa.String(length=64), nullable=False),
        sa.Column("seed_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("budget_contract_sha256", sa.String(length=64), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("baseline_program_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("treatment_program_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("baseline_frozen_budget_json", _json(), nullable=False),
        sa.Column("treatment_frozen_budget_json", _json(), nullable=False),
        sa.Column(
            "baseline_harness_outcome_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "treatment_harness_outcome_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "baseline_outcome_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "treatment_outcome_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "baseline_receipt_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "treatment_receipt_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "baseline_search_tool_call_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "treatment_search_tool_call_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "baseline_portfolio_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "treatment_portfolio_artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("baseline_raw_outcome", sa.Float(), nullable=True),
        sa.Column("treatment_raw_outcome", sa.Float(), nullable=True),
        sa.Column("raw_delta", sa.Float(), nullable=True),
        sa.Column("improvement_delta", sa.Float(), nullable=True),
        sa.Column("denominator_complete", sa.Boolean(), nullable=False),
        sa.Column("replay_verified", sa.Boolean(), nullable=False),
        sa.Column("comparison_sha256", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "baseline_program_fingerprint <> treatment_program_fingerprint",
            name="skill_campaign_distinct_programs",
        ),
        sa.CheckConstraint(
            "direction IN ('minimize', 'maximize')",
            name="skill_campaign_direction",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'incomparable')",
            name="skill_campaign_status",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND baseline_raw_outcome IS NOT NULL AND "
            "treatment_raw_outcome IS NOT NULL AND raw_delta IS NOT NULL AND "
            "improvement_delta IS NOT NULL) OR "
            "(status <> 'succeeded' AND baseline_raw_outcome IS NULL AND "
            "treatment_raw_outcome IS NULL AND raw_delta IS NULL AND "
            "improvement_delta IS NULL)",
            name="skill_campaign_effect_value_semantics",
        ),
        sa.CheckConstraint(
            "baseline_outcome_artifact_id <> treatment_outcome_artifact_id AND "
            "baseline_receipt_artifact_id <> treatment_receipt_artifact_id AND "
            "baseline_harness_outcome_id <> treatment_harness_outcome_id AND "
            "baseline_search_tool_call_id <> treatment_search_tool_call_id AND "
            "baseline_portfolio_artifact_id <> treatment_portfolio_artifact_id",
            name="skill_campaign_distinct_arm_artifacts",
        ),
        sa.CheckConstraint(
            "denominator_complete = true AND replay_verified = true",
            name="skill_campaign_complete_replay",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["skill_experiments.id"]),
        sa.ForeignKeyConstraint(["baseline_harness_outcome_id"], ["harness_outcomes.id"]),
        sa.ForeignKeyConstraint(["treatment_harness_outcome_id"], ["harness_outcomes.id"]),
        sa.ForeignKeyConstraint(["baseline_outcome_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["treatment_outcome_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["baseline_receipt_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["treatment_receipt_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["baseline_search_tool_call_id"], ["tool_calls.id"]),
        sa.ForeignKeyConstraint(["treatment_search_tool_call_id"], ["tool_calls.id"]),
        sa.ForeignKeyConstraint(["baseline_portfolio_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["treatment_portfolio_artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "independent_unit_id",
            "endpoint_name",
            name="uq_skill_campaign_comparison_cell",
        ),
        sa.UniqueConstraint("comparison_sha256"),
    )
    op.create_index(
        "ix_skill_campaign_comparison_experiment",
        "skill_campaign_comparisons",
        ["experiment_id", "independent_unit_id"],
    )

    op.create_table(
        "skill_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_kind", sa.String(length=64), nullable=False),
        sa.Column("evidence_grade", sa.String(length=32), nullable=False),
        sa.Column("promotion_eligible", sa.Boolean(), nullable=False),
        sa.Column("context_json", _json(), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("program_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("endpoint_family", sa.String(length=64), nullable=False),
        sa.Column("endpoint_name", sa.String(length=128), nullable=False),
        sa.Column("independent_unit_id", sa.String(length=255), nullable=False),
        sa.Column("metric_version", sa.String(length=128), nullable=False),
        sa.Column("comparison_kind", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("effect_value", sa.Float(), nullable=True),
        sa.Column("raw_effect_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("denominator", sa.Integer(), nullable=False),
        sa.Column("uncertainty_json", _json(), nullable=False),
        sa.Column("limitations_json", _json(), nullable=False),
        sa.Column(
            "source_autoresearch_metric_delta_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "source_skill_campaign_comparison_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("source_harness_outcome_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effect_model_version", sa.String(length=128), nullable=True),
        sa.Column("effect_model_config_sha256", sa.String(length=64), nullable=True),
        sa.Column("replay_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "evidence_kind IN ("
            "'historical_hypothesis', 'paired_edit_effect', 'campaign_effect', "
            "'failure_observation', 'equivalence_assertion', "
            "'effect_estimate_snapshot', 'promotion_decision'"
            ")",
            name="skill_evidence_kind",
        ),
        sa.CheckConstraint(
            "evidence_grade IN ("
            "'retrospective_observational', 'counterfactual', 'shadow', "
            "'prospective_controlled', 'external_experimental'"
            ")",
            name="skill_evidence_grade",
        ),
        sa.CheckConstraint(
            "comparison_kind IN ('numeric_delta', 'categorical_transition', 'set_outcome')",
            name="skill_evidence_comparison_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('minimize', 'maximize', 'audit', 'categorical')",
            name="skill_evidence_direction",
        ),
        sa.CheckConstraint(
            "sample_size >= 0 AND denominator >= sample_size",
            name="skill_evidence_complete_denominator",
        ),
        sa.CheckConstraint(
            "promotion_eligible = false OR evidence_grade = 'prospective_controlled'",
            name="skill_evidence_promotion_grade",
        ),
        sa.CheckConstraint(
            "promotion_eligible = false OR (experiment_id IS NOT NULL AND "
            "replay_artifact_id IS NOT NULL AND "
            "((source_autoresearch_metric_delta_id IS NOT NULL AND "
            "source_skill_campaign_comparison_id IS NULL AND "
            "source_harness_outcome_id IS NULL) OR "
            "(source_autoresearch_metric_delta_id IS NULL AND "
            "source_skill_campaign_comparison_id IS NOT NULL AND "
            "source_harness_outcome_id IS NULL)))",
            name="skill_evidence_promotion_typed_source",
        ),
        sa.CheckConstraint(
            "(source_autoresearch_metric_delta_id IS NULL AND "
            "source_skill_campaign_comparison_id IS NULL) OR "
            "(source_autoresearch_metric_delta_id IS NULL AND "
            "source_harness_outcome_id IS NULL) OR "
            "(source_skill_campaign_comparison_id IS NULL AND "
            "source_harness_outcome_id IS NULL)",
            name="skill_evidence_at_most_one_typed_source",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["skill_experiments.id"]),
        sa.ForeignKeyConstraint(["replay_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["skill_revision_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(
            ["source_autoresearch_metric_delta_id"], ["autoresearch_metric_deltas.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_skill_campaign_comparison_id"],
            ["skill_campaign_comparisons.id"],
        ),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["source_harness_outcome_id"], ["harness_outcomes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "independent_unit_id",
            "endpoint_name",
            name="uq_skill_evidence_experiment_cell",
        ),
        sa.UniqueConstraint("evidence_sha256"),
    )
    op.create_index(
        "ix_skill_evidence_effect_context",
        "skill_evidence",
        ["skill_revision_id", "context_sha256", "endpoint_name"],
    )

    op.add_column(
        "autoresearch_actions",
        sa.Column("skill_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "autoresearch_actions",
        sa.Column("skill_experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "autoresearch_action_skill_binding_pair",
        "autoresearch_actions",
        "(skill_revision_id IS NULL AND skill_experiment_id IS NULL) OR "
        "(skill_revision_id IS NOT NULL AND skill_experiment_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_autoresearch_actions_skill_revision_id_skills",
        "autoresearch_actions",
        "skills",
        ["skill_revision_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_autoresearch_actions_skill_experiment_id_skill_experiments",
        "autoresearch_actions",
        "skill_experiments",
        ["skill_experiment_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "autoresearch_action_skill_binding_pair",
        "autoresearch_actions",
        type_="check",
    )
    op.drop_constraint(
        "fk_autoresearch_actions_skill_experiment_id_skill_experiments",
        "autoresearch_actions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_autoresearch_actions_skill_revision_id_skills",
        "autoresearch_actions",
        type_="foreignkey",
    )
    op.drop_column("autoresearch_actions", "skill_experiment_id")
    op.drop_column("autoresearch_actions", "skill_revision_id")
    op.drop_index("ix_skill_evidence_effect_context", table_name="skill_evidence")
    op.drop_table("skill_evidence")
    op.drop_index(
        "ix_skill_campaign_comparison_experiment",
        table_name="skill_campaign_comparisons",
    )
    op.drop_table("skill_campaign_comparisons")
    op.drop_index("ix_skill_experiment_skill_phase", table_name="skill_experiments")
    op.drop_table("skill_experiments")
    op.drop_index("ix_skill_state_kind", table_name="skills")
    op.drop_index("ix_skill_family_fingerprint", table_name="skills")
    op.drop_table("skills")
