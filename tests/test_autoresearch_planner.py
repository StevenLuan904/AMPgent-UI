from __future__ import annotations

from pepagent.autoresearch_closed_loop import (
    CandidateEvidence,
    MetricObservation,
    MultiFrontArchivePolicy,
    PepMLMTargetedAction,
    build_multi_front_archive,
    parse_evolution_action,
    validate_action_child,
)
from pepagent.autoresearch_planner import (
    PlannerDeltaEvidence,
    _unique_de_novo_sequence,
    build_multifront_rule_action_plan,
)
from pepagent.provenance.hashing import sha256_text
from pepagent.workers.autoresearch_activities import _compile_pepmlm_action


def _metric(value: float, direction: str) -> MetricObservation:
    return MetricObservation(
        numeric_value=value,
        direction=direction,  # type: ignore[arg-type]
        unit="dimensionless",
        version="test-v1",
    )


def _candidate(
    candidate_id: str,
    sequence: str,
    family: str,
    activity: tuple[float, float, float],
    *,
    eligible: bool = True,
) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id=candidate_id,
        sequence=sequence,
        sequence_sha256=sha256_text(sequence),
        family_key=family,
        archive_eligible=eligible,
        metrics={
            "amp_read_log10_mic_um": _metric(activity[0], "minimize"),
            "llamp_log10_mic_um": _metric(activity[1], "minimize"),
            "macrel_amp_probability": _metric(activity[2], "maximize"),
            "macrel_hemolysis_probability": _metric(0.1, "minimize"),
            "toxinpred3_hybrid_score": _metric(0.05, "minimize"),
            "guruprasad_instability_index": _metric(20.0, "minimize"),
        },
    )


def _cohort() -> tuple[CandidateEvidence, ...]:
    return (
        _candidate(
            "00000000-0000-0000-0000-000000000101",
            "ACDEFGHIKLACDEFGHIKL",
            "fam-a",
            (0.1, 2.0, 0.2),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000102",
            "LMNPQRSTVWLMNPQRSTVW",
            "fam-b",
            (2.0, 0.1, 0.3),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000103",
            "KKLLKLLKLLKKLLKLLKLL",
            "fam-c",
            (1.0, 1.1, 0.95),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000104",
            "VVVVVVKKRRVVVVVVKKRR",
            "fam-d",
            (0.7, 0.8, 0.7),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000105",
            "WWWWWWRRRRWWWWWWRRRR",
            "unsafe",
            (0.0, 0.0, 1.0),
            eligible=False,
        ),
    )


def test_multifront_rule_planner_keeps_conflicts_novelty_and_four_strategies() -> None:
    cohort = _cohort()
    snapshot = build_multi_front_archive(
        cohort,
        MultiFrontArchivePolicy(known_family_keys=("old-family",)),
        generation=0,
    )
    preferred = cohort[0]
    delta_sha = "d" * 64
    plan = build_multifront_rule_action_plan(
        candidates=cohort,
        snapshot=snapshot,
        branch_key="PBP2a",
        generation=1,
        seed=17,
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        prior_deltas=(
            PlannerDeltaEvidence(
                candidate_id=preferred.candidate_id,
                metric_name="amp_read_log10_mic_um",
                delta_sha256=delta_sha,
                improved=True,
            ),
        ),
        gold_target=50,
        de_novo_quota=0.2,
    )

    assert set(plan["strategies"]) == {
        "substitution",
        "crossover",
        "de_novo",
        "pepmlm_targeted",
    }
    assert plan["gold_target"] == 50
    assert plan["gold_shortfall"] > 0
    assert plan["no_weighted_total_score"] is True
    actions = [parse_evolution_action(item) for item in plan["actions"]]
    assert len(actions) == 4
    assert all(
        20 <= (
            action.peptide_length
            if getattr(action, "proposal_mode", None) == "de_novo"
            else len(action.proposed_sequence)
            if getattr(action, "action_type", None) == "de_novo"
            else len(
                next(
                    candidate.sequence
                    for candidate in cohort
                    if candidate.candidate_id == action.parent_candidate_id
                )
            )
        ) <= 30
        for action in actions
    )
    assert plan["gold_candidate_count"] == plan[
        "instability_score_qualified_gold_candidate_count"
    ]
    assert plan["deprecated_ood_qualified_gold_candidate_count"] == plan[
        "gold_candidate_count"
    ]
    assert plan["quality_gate"] == "literal-hard-gates+guruprasad-score-lt-50"
    assert any(delta_sha in action.evidence_sha256s for action in actions)
    serialized = "|".join(str(item.model_dump(mode="json")) for item in actions)
    assert cohort[-1].candidate_id not in serialized


def test_pepmlm_targeted_action_compiles_to_existing_cli_schema_and_validates_child() -> None:
    parent = _cohort()[0]
    action = PepMLMTargetedAction(
        branch_key="PBP2a",
        generation=1,
        seed=23,
        operator_id="pepmlm-targeted-action-v1",
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("b" * 64,),
        proposal_mode="masked_substitution",
        parent_candidate_id=parent.candidate_id,
        parent_sequence_sha256=parent.sequence_sha256,
        parent_length=len(parent.sequence),
        mutation_positions_one_based=(2,),
        top_k=7,
        temperature=0.8,
    )
    compiled = _compile_pepmlm_action(
        action=action,
        persisted_action_id="00000000-0000-0000-0000-000000000201",
        sources_by_id={parent.candidate_id: parent},
    )

    assert compiled == {
        "action_kind": "masked_substitution",
        "action_id": "00000000-0000-0000-0000-000000000201",
        "seed": 23,
        "top_k": 7,
        "temperature": 0.8,
        "expected_improvement_axes": ["macrel_amp_probability"],
        "protected_axes": ["guruprasad_instability_index"],
        "primary_parent_id": parent.candidate_id,
        "primary_parent_sequence": parent.sequence,
        "mutation_positions": [2],
    }
    assert validate_action_child(
        action,
        {parent.candidate_id: parent},
        "ARDEFGHIKLACDEFGHIKL",
    ) == "ARDEFGHIKLACDEFGHIKL"


def test_planner_accepts_short_instability_ood_parent_when_score_is_below_50() -> None:
    short = _candidate(
        "00000000-0000-0000-0000-000000000201",
        "ACDEFGHIKL",
        "short-ood",
        (0.1, 0.2, 0.9),
    ).model_copy(
        update={
            "metrics": {
                **_candidate(
                    "00000000-0000-0000-0000-000000000201",
                    "ACDEFGHIKL",
                    "short-ood",
                    (0.1, 0.2, 0.9),
                ).metrics,
                "guruprasad_instability_index": MetricObservation(
                    numeric_value=10.0,
                    direction="minimize",
                    unit="dimensionless",
                    version="test-v1",
                    out_of_domain=True,
                ),
            }
        }
    )
    snapshot = build_multi_front_archive(
        (short,),
        MultiFrontArchivePolicy(),
        generation=0,
    )

    plan = build_multifront_rule_action_plan(
        candidates=(short,),
        snapshot=snapshot,
        branch_key="PBP2a",
        generation=1,
        seed=17,
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        gold_target=50,
    )

    assert plan["instability_score_qualified_gold_candidate_count"] >= 0
    assert "substitution" in plan["strategies"]


def test_planner_rejects_parent_when_instability_score_is_not_below_50() -> None:
    candidate = _candidate(
        "00000000-0000-0000-0000-000000000202",
        "ACDEFGHIKL",
        "unstable",
        (0.1, 0.2, 0.9),
    )
    candidate = candidate.model_copy(
        update={
            "metrics": {
                **candidate.metrics,
                "guruprasad_instability_index": MetricObservation(
                    numeric_value=50.0,
                    direction="minimize",
                    unit="dimensionless",
                    version="test-v1",
                    out_of_domain=False,
                ),
            }
        }
    )
    snapshot = build_multi_front_archive((candidate,), MultiFrontArchivePolicy(), generation=0)

    try:
        build_multifront_rule_action_plan(
            candidates=(candidate,),
            snapshot=snapshot,
            branch_key="PBP2a",
            generation=1,
            seed=17,
            operator_release_sha256="a" * 64,
            target_sequence_sha256="c" * 64,
            gold_target=50,
        )
    except ValueError as error:
        assert "Guruprasad instability <50" in str(error)
    else:
        raise AssertionError("planner accepted an instability score at the hard boundary")


def test_planner_rejects_gold_target_below_branch_contract() -> None:
    cohort = _cohort()
    snapshot = build_multi_front_archive(
        cohort,
        MultiFrontArchivePolicy(),
        generation=0,
    )
    try:
        build_multifront_rule_action_plan(
            candidates=cohort,
            snapshot=snapshot,
            branch_key="PBP2a",
            generation=1,
            seed=17,
            operator_release_sha256="a" * 64,
            target_sequence_sha256="c" * 64,
            gold_target=49,
        )
    except ValueError as error:
        assert "at least 50 gold" in str(error)
    else:
        raise AssertionError("planner accepted a sub-contract gold target")


def test_rule_de_novo_skips_historical_sequence_hashes() -> None:
    first = _unique_de_novo_sequence(
        branch_key="AceA",
        seed=515155,
        known_sequences=set(),
    )
    second = _unique_de_novo_sequence(
        branch_key="AceA",
        seed=515155,
        known_sequences=set(),
        excluded_sequence_sha256s={sha256_text(first)},
    )

    assert second != first
    assert sha256_text(second) != sha256_text(first)
