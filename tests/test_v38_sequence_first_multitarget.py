from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import ValidationError

from pepagent.v38_sequence_first_multitarget import (
    DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID,
    HistoricalEvidenceSnapshot,
    HistoricalRunSummary,
    KnowledgeUseTrace,
    LabelGate,
    MetricAgreementGate,
    MetricObservation,
    MultiTargetExecutionPlan,
    NumericGate,
    SequenceCandidateEvidence,
    SequenceMaturityPolicy,
    TargetBranchSpec,
    assess_sequence_maturity,
    build_parallel_target_dispatch,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
NOW = datetime(2026, 8, 17, tzinfo=UTC)


def _history_run(*, status: str = "failed", created_day: int = 1) -> HistoricalRunSummary:
    role = {
        "succeeded": "decision_replay",
        "failed": "failure_denominator",
        "cancelled": "cancelled_denominator",
    }[status]
    return HistoricalRunSummary(
        run_id=uuid4(),
        target_id=uuid4(),
        target_name="target",
        status=status,
        spec_sha256=SHA_A,
        created_at=datetime(2026, 8, created_day, tzinfo=UTC),
        finished_at=datetime(2026, 8, created_day, 1, tzinfo=UTC),
        candidate_count=900,
        occurrence_count=9000,
        evaluation_count=9900,
        tool_call_count=100,
        succeeded_tool_call_count=99,
        failed_tool_call_count=1,
        decision_count=2,
        evidence_link_count=50,
        distinct_artifact_count=40,
        lifecycle_event_count=20,
        evidence_graph_manifest_sha256=SHA_D,
        terminal_event_type=f"run.{status}",
        historical_role=role,
    )


def _observations() -> tuple[MetricObservation, ...]:
    return (
        MetricObservation(metric_name="hydrophobicity", status="succeeded", numeric_value=0.45),
        MetricObservation(
            metric_name="hydrophobic_moment", status="succeeded", numeric_value=0.60
        ),
        MetricObservation(metric_name="net_charge", status="succeeded", numeric_value=4.0),
        MetricObservation(
            metric_name="instability_index", status="succeeded", numeric_value=22.0
        ),
        MetricObservation(metric_name="hemolysis_risk", status="succeeded", text_value="low"),
        MetricObservation(
            metric_name="llamp_log10_mic_um", status="succeeded", numeric_value=0.80
        ),
        MetricObservation(
            metric_name="amp_read_log10_mic_um", status="succeeded", numeric_value=0.95
        ),
        MetricObservation(
            metric_name="toxinpred3_hybrid_score", status="succeeded", numeric_value=0.10
        ),
        MetricObservation(
            metric_name="toxinpred3_label", status="succeeded", text_value="Non-Toxin"
        ),
    )


def _policy() -> SequenceMaturityPolicy:
    required = frozenset(item.metric_name for item in _observations())
    return SequenceMaturityPolicy(
        required_metrics=required,
        numeric_gates=(
            NumericGate(
                metric_name="llamp_log10_mic_um",
                direction="max",
                threshold=1.2,
                threshold_source="external_reference",
                evidence_sha256=SHA_A,
            ),
            NumericGate(
                metric_name="amp_read_log10_mic_um",
                direction="max",
                threshold=1.2,
                threshold_source="external_reference",
                evidence_sha256=SHA_B,
            ),
            NumericGate(
                metric_name="toxinpred3_hybrid_score",
                direction="max",
                threshold=0.5,
                threshold_source="provider_contract",
                evidence_sha256=SHA_C,
            ),
        ),
        label_gates=(
            LabelGate(
                metric_name="hemolysis_risk",
                allowed_values=frozenset({"low"}),
                evidence_sha256=SHA_A,
            ),
            LabelGate(
                metric_name="toxinpred3_label",
                allowed_values=frozenset({"Non-Toxin"}),
                evidence_sha256=SHA_B,
            ),
        ),
        agreement_gates=(
            MetricAgreementGate(
                metric_names=("llamp_log10_mic_um", "amp_read_log10_mic_um"),
                maximum_spread=0.4,
                evidence_sha256=SHA_D,
            ),
        ),
        minimum_rank_stability=0.8,
    )


def _candidate(
    *,
    observations: tuple[MetricObservation, ...] | None = None,
    parent_candidate_id: UUID | None = None,
    knowledge: tuple[KnowledgeUseTrace, ...] = (),
) -> SequenceCandidateEvidence:
    return SequenceCandidateEvidence(
        candidate_id=uuid4(),
        sequence_sha256=SHA_A,
        parent_candidate_id=parent_candidate_id,
        generation=1 if parent_candidate_id else 0,
        observations=observations or _observations(),
        rank_stability=0.9,
        knowledge_traces=knowledge,
        proposal_context_sha256=SHA_B,
    )


def _branch(name: str) -> TargetBranchSpec:
    return TargetBranchSpec(
        target_key=name,
        target_id=uuid4(),
        target_sequence_sha256=SHA_A,
        coordinate_sha256=SHA_B,
        native_pocket_sha256=SHA_C,
        wrong_pocket_sha256=SHA_D,
        qualification_witness_sha256=SHA_A,
        evidence_grade="A",
        panel_role="reference_anchor" if name.startswith("acea") else "qualified_target",
        structure_budget=48,
        boltz_seeds_per_candidate=3,
        rosetta_decoys_per_pose=16,
    )


def test_framework_contract_is_sequence_first_multitarget_and_not_authorized() -> None:
    with open(
        "config/benchmarks/amp_sequence_first_multitarget_v38.yaml",
        encoding="utf-8",
    ) as handle:
        config = yaml.safe_load(handle)
    assert config["scope"]["formal_run_authorized"] is False
    assert config["scope"]["formal_run_submitted"] is False
    assert config["history_inheritance"]["include_terminal_statuses"] == [
        "succeeded",
        "failed",
        "cancelled",
    ]
    assert config["sequence_first_agent"]["raw_proposal_policy"][
        "score_all_valid_unique_proposals_before_promotion"
    ]
    assert config["sequence_first_agent"]["gates"]["activity"][
        "dual_MIC_model_agreement_required"
    ]
    assert config["multitarget_parallelism"]["minimum_target_count"] >= 2
    assert config["knowledge_use"]["provider_task_id"] == DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID


def test_history_snapshot_preserves_all_terminal_denominators_without_output_reuse() -> None:
    runs = (
        _history_run(status="succeeded", created_day=1),
        _history_run(status="failed", created_day=2),
        _history_run(status="cancelled", created_day=3),
    )
    snapshot = HistoricalEvidenceSnapshot(
        history_cutoff_at=NOW,
        terminal_runs=runs,
        terminal_run_count=3,
        excluded_nonterminal_run_ids=(uuid4(),),
    )
    assert {item.historical_role for item in snapshot.terminal_runs} == {
        "decision_replay",
        "failure_denominator",
        "cancelled_denominator",
    }
    assert all(item.output_reuse_forbidden for item in snapshot.terminal_runs)
    assert len(snapshot.sha256()) == 64


def test_history_snapshot_rejects_duplicate_or_miscounted_runs() -> None:
    run = _history_run()
    with pytest.raises(ValidationError):
        HistoricalEvidenceSnapshot(
            history_cutoff_at=NOW,
            terminal_runs=(run, run),
            terminal_run_count=2,
        )
    with pytest.raises(ValidationError):
        HistoricalEvidenceSnapshot(
            history_cutoff_at=NOW,
            terminal_runs=(run,),
            terminal_run_count=0,
        )


def test_sequence_maturity_requires_activity_safety_developability_and_stability() -> None:
    decision = assess_sequence_maturity(_candidate(), _policy())
    assert decision.status == "mature_core"
    assert decision.structure_eligible is True

    toxic = tuple(
        item.model_copy(update={"text_value": "Toxin"})
        if item.metric_name == "toxinpred3_label"
        else item
        for item in _observations()
    )
    rejected = assess_sequence_maturity(_candidate(observations=toxic), _policy())
    assert rejected.status == "rejected"
    assert rejected.structure_eligible is False
    assert "label_gate_failed:toxinpred3_label" in rejected.reasons


def test_sequence_maturity_routes_mic_disagreement_away_from_structure() -> None:
    disagreement = tuple(
        item.model_copy(update={"numeric_value": 1.2})
        if item.metric_name == "amp_read_log10_mic_um"
        else item.model_copy(update={"numeric_value": 0.2})
        if item.metric_name == "llamp_log10_mic_um"
        else item
        for item in _observations()
    )
    decision = assess_sequence_maturity(_candidate(observations=disagreement), _policy())
    assert decision.status == "exploratory_conflict"
    assert decision.structure_eligible is False
    assert any(reason.startswith("metric_disagreement:") for reason in decision.reasons)


def test_refinement_requires_adopted_knowledge_card_trace() -> None:
    parent_id = uuid4()
    rejected = assess_sequence_maturity(
        _candidate(parent_candidate_id=parent_id),
        _policy(),
    )
    assert rejected.status == "rejected"
    trace = KnowledgeUseTrace(
        card_id="card-1",
        query_sha256=SHA_A,
        passage_sha256=SHA_B,
        decision="adopt",
        rationale="supports a charge-preserving substitution",
    )
    mature = assess_sequence_maturity(
        _candidate(parent_candidate_id=parent_id, knowledge=(trace,)),
        _policy(),
    )
    assert mature.status == "mature_core"


def test_multitarget_plan_dispatches_same_mature_sequences_in_parallel_isolation() -> None:
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-harness",
        history_snapshot_sha256=SHA_A,
        shared_sequence_cohort_sha256=SHA_B,
        sequence_maturity_decision_sha256=SHA_C,
        target_branches=(_branch("acea"), _branch("lpxc"), _branch("ftsz")),
        max_parallel_targets=3,
    )
    candidates = (uuid4(), uuid4())
    dispatches = build_parallel_target_dispatch(plan, mature_candidate_ids=candidates)
    assert len(dispatches) == 3
    assert {dispatch.parallel_wave for dispatch in dispatches} == {0}
    assert all(dispatch.candidate_ids == candidates for dispatch in dispatches)
    assert len({dispatch.evidence_namespace for dispatch in dispatches}) == 3


def test_multitarget_plan_rejects_single_target_and_unequal_science_budget() -> None:
    with pytest.raises(ValidationError):
        MultiTargetExecutionPlan(
            harness_release_id="v38-harness",
            history_snapshot_sha256=SHA_A,
            shared_sequence_cohort_sha256=SHA_B,
            sequence_maturity_decision_sha256=SHA_C,
            target_branches=(_branch("acea"),),
            max_parallel_targets=2,
        )
    with pytest.raises(ValidationError, match="anchor cannot be the only target role"):
        MultiTargetExecutionPlan(
            harness_release_id="v38-harness",
            history_snapshot_sha256=SHA_A,
            shared_sequence_cohort_sha256=SHA_B,
            sequence_maturity_decision_sha256=SHA_C,
            target_branches=(_branch("acea"), _branch("acea-control")),
            max_parallel_targets=2,
        )
    branch_a = _branch("acea")
    branch_b = _branch("lpxc").model_copy(update={"structure_budget": 24})
    with pytest.raises(ValidationError, match="equal preregistered science budget"):
        MultiTargetExecutionPlan(
            harness_release_id="v38-harness",
            history_snapshot_sha256=SHA_A,
            shared_sequence_cohort_sha256=SHA_B,
            sequence_maturity_decision_sha256=SHA_C,
            target_branches=(branch_a, branch_b),
            max_parallel_targets=2,
        )
