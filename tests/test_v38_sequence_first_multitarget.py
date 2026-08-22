from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import ValidationError

from pepagent.provenance.hashing import sha256_text
from pepagent.v38_sequence_first_multitarget import (
    DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID,
    ExplorationPolicy,
    HistoricalEvidenceSnapshot,
    HistoricalRunSummary,
    KnowledgeUseTrace,
    LabelGate,
    MetricAgreementGate,
    MetricObservation,
    MultiTargetBoltzEvidence,
    MultiTargetExecutionPlan,
    MultiTargetRosettaEvidence,
    NumericGate,
    ParetoObjective,
    RefinementPolicy,
    RosettaDecoyEvidence,
    SequenceCandidateEvidence,
    SequenceMaturityPolicy,
    TargetBranchSpec,
    TargetQualificationWitness,
    admit_sequence_cohort,
    assess_sequence_maturity,
    build_default_v38_maturity_policy,
    build_multitarget_structure_tasks,
    build_parallel_target_dispatch,
    build_sequence_refinement_plan,
    compute_leave_one_objective_out_rank_stability,
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
        MetricObservation(metric_name="hydrophobic_moment", status="succeeded", numeric_value=0.60),
        MetricObservation(metric_name="net_charge", status="succeeded", numeric_value=4.0),
        MetricObservation(metric_name="instability_index", status="succeeded", numeric_value=22.0),
        MetricObservation(metric_name="hemolysis_risk", status="succeeded", text_value="low"),
        MetricObservation(metric_name="llamp_log10_mic_um", status="succeeded", numeric_value=0.80),
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
                threshold=4.0,
                purpose="validity",
                threshold_source="operational_guard",
                evidence_sha256=SHA_A,
            ),
            NumericGate(
                metric_name="amp_read_log10_mic_um",
                direction="max",
                threshold=4.0,
                purpose="validity",
                threshold_source="operational_guard",
                evidence_sha256=SHA_B,
            ),
            NumericGate(
                metric_name="toxinpred3_hybrid_score",
                direction="max",
                threshold=0.5,
                purpose="safety",
                threshold_source="provider_contract",
                evidence_sha256=SHA_C,
            ),
        ),
        label_gates=(
            LabelGate(
                metric_name="hemolysis_risk",
                allowed_values=frozenset({"low"}),
                purpose="safety",
                evidence_sha256=SHA_A,
            ),
            LabelGate(
                metric_name="toxinpred3_label",
                allowed_values=frozenset({"Non-Toxin"}),
                purpose="safety",
                evidence_sha256=SHA_B,
            ),
        ),
        pareto_objectives=(
            ParetoObjective(metric_name="llamp_log10_mic_um", direction="min"),
            ParetoObjective(metric_name="amp_read_log10_mic_um", direction="min"),
            ParetoObjective(metric_name="instability_index", direction="min"),
            ParetoObjective(metric_name="hydrophobic_moment", direction="max"),
        ),
        agreement_gates=(
            MetricAgreementGate(
                metric_names=("llamp_log10_mic_um", "amp_read_log10_mic_um"),
                maximum_spread=0.4,
                evidence_sha256=SHA_D,
            ),
        ),
        minimum_rank_stability=0.8,
        structure_budget=10,
        exploration=ExplorationPolicy(maximum_fraction_of_structure_budget=0.2),
        refinement=RefinementPolicy(
            maximum_rounds=3,
            minimum_mature_core_size=2,
            children_per_parent=2,
        ),
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


def _with_numeric_metrics(**updates: float) -> tuple[MetricObservation, ...]:
    return tuple(
        item.model_copy(update={"numeric_value": updates[item.metric_name]})
        if item.metric_name in updates
        else item
        for item in _observations()
    )


def test_rank_stability_uses_leave_one_objective_out_pareto_membership() -> None:
    left = _candidate(
        observations=_with_numeric_metrics(
            llamp_log10_mic_um=0.0,
            amp_read_log10_mic_um=2.0,
            instability_index=0.0,
            hydrophobic_moment=1.0,
        )
    )
    right = _candidate(
        observations=_with_numeric_metrics(
            llamp_log10_mic_um=2.0,
            amp_read_log10_mic_um=0.0,
            instability_index=0.0,
            hydrophobic_moment=1.0,
        )
    )
    stability = compute_leave_one_objective_out_rank_stability(
        (left, right), _policy()
    )
    assert stability[left.candidate_id] == pytest.approx(0.8)
    assert stability[right.candidate_id] == pytest.approx(0.8)


def test_rank_stability_rejects_duplicate_candidate_identity() -> None:
    candidate = _candidate()
    with pytest.raises(ValueError, match="duplicate"):
        compute_leave_one_objective_out_rank_stability(
            (candidate, candidate), _policy()
        )


def test_explicit_non_gating_ood_metric_does_not_reject_candidate() -> None:
    policy = _policy().model_copy(
        update={"non_gating_out_of_domain_metrics": frozenset({"instability_index"})}
    )
    observations = tuple(
        item.model_copy(update={"out_of_domain": True})
        if item.metric_name == "instability_index"
        else item
        for item in _observations()
    )
    decision = assess_sequence_maturity(
        _candidate(observations=observations),
        policy,
    )
    assert decision.status == "pareto_eligible"
    assert not any(reason.startswith("out_of_domain:") for reason in decision.reasons)


def test_default_policy_uses_instability_as_non_gating_pareto_axis() -> None:
    policy = build_default_v38_maturity_policy()
    assert "guruprasad_instability_index" in policy.required_metrics
    assert policy.non_gating_out_of_domain_metrics == frozenset(
        {"guruprasad_instability_index"}
    )
    assert any(
        objective.metric_name == "guruprasad_instability_index"
        and objective.direction == "min"
        for objective in policy.pareto_objectives
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


def test_framework_contract_is_sequence_first_multitarget_and_staged_authorized() -> None:
    with open(
        "config/benchmarks/amp_sequence_first_multitarget_v38.yaml",
        encoding="utf-8",
    ) as handle:
        config = yaml.safe_load(handle)
    assert config["scope"]["formal_run_authorized"] is True
    assert config["scope"]["formal_run_submitted"] is False
    assert config["history_inheritance"]["include_terminal_statuses"] == [
        "succeeded",
        "failed",
        "cancelled",
    ]
    assert config["sequence_first_agent"]["raw_proposal_policy"][
        "score_all_valid_unique_proposals_before_promotion"
    ]
    activity = config["sequence_first_agent"]["gates"]["activity"]
    assert activity["dual_MIC_models_must_both_succeed_and_remain_independent_pareto_axes"]
    assert activity["fixed_numeric_MIC_agreement_cutoff_forbidden"]
    assert activity["arbitrary_absolute_activity_threshold_forbidden"]
    assert (
        config["sequence_first_agent"]["structure_admission"]["zero_mature_core_action"]
        == "refine_without_lowering_safety"
    )
    assert config["run_control"]["operator_review_seconds"] == 7200
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


def test_sequence_maturity_hard_gates_only_safety_and_validity() -> None:
    decision = assess_sequence_maturity(_candidate(), _policy())
    assert decision.status == "pareto_eligible"
    assert decision.structure_eligible is False

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
    assert decision.status == "promising_uncertain"
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
    assert mature.status == "pareto_eligible"


def test_cohort_uses_pareto_core_and_fixed_safe_exploration_without_forced_fill() -> None:
    candidates = tuple(_candidate() for _ in range(4))
    admission = admit_sequence_cohort(candidates, _policy(), refinement_round=0)
    assert admission.structure_dispatch_allowed is True
    assert admission.refinement_required is False
    assert len(admission.mature_core_candidate_ids) == 4
    assert admission.exploration_candidate_ids == ()
    assert admission.unused_structure_slots == 6
    assert admission.safety_thresholds_lowered is False
    assert admission.forced_fill_used is False


def test_dominated_safe_candidate_is_not_promoted_into_mature_core() -> None:
    strong = _candidate()
    weak_observations = tuple(
        item.model_copy(update={"numeric_value": item.numeric_value + 1.0})
        if item.metric_name in {"llamp_log10_mic_um", "amp_read_log10_mic_um"}
        else item.model_copy(update={"numeric_value": item.numeric_value + 10.0})
        if item.metric_name == "instability_index"
        else item.model_copy(update={"numeric_value": item.numeric_value - 0.2})
        if item.metric_name == "hydrophobic_moment"
        else item
        for item in _observations()
    )
    weak = _candidate(observations=weak_observations)
    admission = admit_sequence_cohort((strong, weak), _policy(), refinement_round=0)
    assert admission.mature_core_candidate_ids == (strong.candidate_id,)
    assert weak.candidate_id in admission.exploration_candidate_ids
    assert admission.refinement_required is True
    assert admission.structure_dispatch_allowed is False


def test_refinement_plan_is_bounded_knowledge_traced_and_keeps_parent_control() -> None:
    strong = _candidate()
    weak_observations = tuple(
        item.model_copy(update={"numeric_value": item.numeric_value + 1.0})
        if item.metric_name in {"llamp_log10_mic_um", "amp_read_log10_mic_um"}
        else item
        for item in _observations()
    )
    weak = _candidate(observations=weak_observations)
    strong_sequence = "ACDEFGHIKLMN"
    weak_sequence = "ACDEFGHIKLMP"
    strong = strong.model_copy(update={"sequence_sha256": sha256_text(strong_sequence)})
    weak = weak.model_copy(update={"sequence_sha256": sha256_text(weak_sequence)})
    candidates = (strong, weak)
    admission = admit_sequence_cohort(candidates, _policy(), refinement_round=0)
    plan = build_sequence_refinement_plan(
        admission=admission,
        candidates=candidates,
        parent_sequences={
            strong.candidate_id: strong_sequence,
            weak.candidate_id: weak_sequence,
        },
        policy=_policy(),
        knowledge_context_pack_sha256=SHA_D,
    )
    assert plan.refinement_round == 1
    assert len(plan.tasks) == 1
    assert plan.tasks[0].parent_candidate_id == weak.candidate_id
    assert plan.tasks[0].provider_task_id == DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID
    assert plan.tasks[0].requested_children == 2
    assert plan.structure_dispatch_forbidden_until_readmission is True
    assert plan.safety_thresholds_lowered is False
    assert len(plan.sha256()) == 64


def test_default_policy_has_no_absolute_mic_cutoff_and_uses_both_mic_objectives() -> None:
    policy = build_default_v38_maturity_policy()
    assert policy.numeric_gates == ()
    assert policy.agreement_gates == ()
    objectives = {item.metric_name for item in policy.pareto_objectives}
    assert {"llamp_log10_mic_um", "amp_read_log10_mic_um"} <= objectives
    assert {item.metric_name for item in policy.label_gates} == {
        "macrel_hemolysis_label",
        "toxinpred3_label",
    }


def test_zero_safe_core_triggers_refinement_without_lowering_safety() -> None:
    toxic = tuple(
        item.model_copy(update={"text_value": "Toxin"})
        if item.metric_name == "toxinpred3_label"
        else item
        for item in _observations()
    )
    candidates = tuple(_candidate(observations=toxic) for _ in range(3))
    admission = admit_sequence_cohort(candidates, _policy(), refinement_round=0)
    assert admission.mature_core_candidate_ids == ()
    assert admission.exploration_candidate_ids == ()
    assert admission.refinement_required is True
    assert admission.structure_dispatch_allowed is False
    assert admission.unused_structure_slots == 10


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


def test_multitarget_structure_tasks_expand_both_control_lanes_and_all_seeds() -> None:
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-harness",
        history_snapshot_sha256=SHA_A,
        shared_sequence_cohort_sha256=SHA_B,
        sequence_maturity_decision_sha256=SHA_C,
        target_branches=(_branch("acea"), _branch("lpxc")),
        max_parallel_targets=2,
    )
    candidates = (uuid4(), uuid4())
    dispatches = build_parallel_target_dispatch(plan, mature_candidate_ids=candidates)
    tasks = build_multitarget_structure_tasks(
        plan,
        dispatches=dispatches,
        boltz_seeds=(20270380, 20270381, 20270382),
    )

    assert len(tasks) == 2 * 2 * 2 * 3
    assert tuple(task.ordinal for task in tasks) == tuple(range(len(tasks)))
    assert {task.control_lane for task in tasks} == {"native", "wrong_pocket"}
    assert {task.boltz_seed for task in tasks} == {20270380, 20270381, 20270382}
    assert all(task.rosetta_decoys_per_pose == 16 for task in tasks)
    expected_target_keys = {branch.target_key for branch in plan.target_branches}
    for start in range(0, len(tasks), plan.max_parallel_targets):
        parallel_batch = tasks[start : start + plan.max_parallel_targets]
        assert {task.target_key for task in parallel_batch} == expected_target_keys
        assert len({task.candidate_id for task in parallel_batch}) == 1
        assert len({task.control_lane for task in parallel_batch}) == 1
        assert len({task.boltz_seed for task in parallel_batch}) == 1
    for branch in plan.target_branches:
        branch_tasks = [task for task in tasks if task.target_key == branch.target_key]
        assert {task.pocket_sha256 for task in branch_tasks} == {
            branch.native_pocket_sha256,
            branch.wrong_pocket_sha256,
        }
        assert len({task.evidence_namespace for task in branch_tasks}) == 2


def test_multitarget_structure_tasks_reject_incomplete_seed_budget() -> None:
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-harness",
        history_snapshot_sha256=SHA_A,
        shared_sequence_cohort_sha256=SHA_B,
        sequence_maturity_decision_sha256=SHA_C,
        target_branches=(_branch("acea"), _branch("lpxc")),
        max_parallel_targets=2,
    )
    dispatches = build_parallel_target_dispatch(plan, mature_candidate_ids=(uuid4(),))
    with pytest.raises(ValueError, match="seed count"):
        build_multitarget_structure_tasks(
            plan,
            dispatches=dispatches,
            boltz_seeds=(20270380, 20270381),
        )


def test_multitarget_structure_evidence_binds_pose_and_all_decoy_hashes() -> None:
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-harness",
        history_snapshot_sha256=SHA_A,
        shared_sequence_cohort_sha256=SHA_B,
        sequence_maturity_decision_sha256=SHA_C,
        target_branches=(_branch("acea"), _branch("lpxc")),
        max_parallel_targets=2,
    )
    dispatches = build_parallel_target_dispatch(plan, mature_candidate_ids=(uuid4(),))
    task = build_multitarget_structure_tasks(
        plan,
        dispatches=dispatches,
        boltz_seeds=(20270380, 20270381, 20270382),
    )[0]
    boltz = MultiTargetBoltzEvidence(
        task=task,
        task_sha256=task.sha256(),
        tool_call_id=uuid4(),
        coordinate_artifact_sha256=SHA_A,
        raw_result_artifact_sha256=SHA_B,
        parameters_sha256=SHA_C,
    )
    decoys = tuple(
        RosettaDecoyEvidence(
            decoy_ordinal=index,
            input_structure_sha256=SHA_D,
            output_structure_sha256=f"{index + 1:064x}",
            score_record_sha256=f"{index + 100:064x}",
            total_score=float(index),
        )
        for index in range(16)
    )
    rosetta = MultiTargetRosettaEvidence(
        task=task,
        task_sha256=task.sha256(),
        boltz_evidence_sha256=boltz.sha256(),
        boltz_coordinate_artifact_sha256=SHA_A,
        converted_input_artifact_sha256=SHA_B,
        prepared_input_artifact_sha256=SHA_C,
        prepacked_input_artifact_sha256=SHA_D,
        tool_call_id=uuid4(),
        raw_result_artifact_sha256=SHA_D,
        decoys=decoys,
    )

    assert rosetta.task.control_lane == "native"
    assert rosetta.task.target_id == plan.target_branches[0].target_id
    assert len(rosetta.decoys) == 16
    assert len(rosetta.sha256()) == 64


def test_multitarget_structure_evidence_rejects_task_or_decoy_drift() -> None:
    plan = MultiTargetExecutionPlan(
        harness_release_id="v38-harness",
        history_snapshot_sha256=SHA_A,
        shared_sequence_cohort_sha256=SHA_B,
        sequence_maturity_decision_sha256=SHA_C,
        target_branches=(_branch("acea"), _branch("lpxc")),
        max_parallel_targets=2,
    )
    dispatches = build_parallel_target_dispatch(plan, mature_candidate_ids=(uuid4(),))
    task = build_multitarget_structure_tasks(
        plan,
        dispatches=dispatches,
        boltz_seeds=(20270380, 20270381, 20270382),
    )[0]
    with pytest.raises(ValidationError, match="exact v38 structure task"):
        MultiTargetBoltzEvidence(
            task=task,
            task_sha256=SHA_A,
            tool_call_id=uuid4(),
            coordinate_artifact_sha256=SHA_A,
            raw_result_artifact_sha256=SHA_B,
            parameters_sha256=SHA_C,
        )
    short_decoys = tuple(
        RosettaDecoyEvidence(
            decoy_ordinal=index,
            input_structure_sha256=SHA_D,
            output_structure_sha256=f"{index + 1:064x}",
            score_record_sha256=f"{index + 100:064x}",
            total_score=float(index),
        )
        for index in range(15)
    )
    with pytest.raises(ValidationError, match="decoy count"):
        MultiTargetRosettaEvidence(
            task=task,
            task_sha256=task.sha256(),
            boltz_evidence_sha256=SHA_B,
            boltz_coordinate_artifact_sha256=SHA_A,
            converted_input_artifact_sha256=SHA_B,
            prepared_input_artifact_sha256=SHA_C,
            prepacked_input_artifact_sha256=SHA_D,
            tool_call_id=uuid4(),
            raw_result_artifact_sha256=SHA_D,
            decoys=short_decoys,
        )


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


def test_real_v38_panel_has_two_qualified_targets_and_distinct_controls() -> None:
    with open("config/targets/amp_multitarget_panel_v38.yaml", encoding="utf-8") as handle:
        panel = yaml.safe_load(handle)
    witnesses = tuple(TargetQualificationWitness.model_validate(item) for item in panel["branches"])
    assert len(witnesses) == 2
    assert {item.coordinate_source_accession for item in witnesses} == {"8QQI", "3ZFZ"}
    assert all(item.primary_pocket_id != item.wrong_pocket_id for item in witnesses)
    assert all(item.primary_pocket_grade in {"A", "B"} for item in witnesses)
    assert all(len(item.sha256()) == 64 for item in witnesses)
