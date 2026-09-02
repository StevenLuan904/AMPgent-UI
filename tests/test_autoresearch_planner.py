from __future__ import annotations

import inspect
from itertools import product

from pepagent.autoresearch_closed_loop import (
    CandidateEvidence,
    ControlledCrossoverAction,
    MaskedSubstitutionAction,
    MetricObservation,
    MultiFrontArchivePolicy,
    PepMLMTargetedAction,
    apply_evolution_action,
    build_multi_front_archive,
    parse_evolution_action,
    validate_action_child,
)
from pepagent.autoresearch_planner import (
    PlannerDeltaEvidence,
    _adaptive_de_novo_alphabet,
    _adaptive_de_novo_transition_alphabets,
    _de_novo_prescreen_passes,
    _family_balanced_de_novo_profile,
    _hydrophobic_fraction,
    _proposal_quality_gate_passes,
    _sequence_prescreen,
    _SequenceFamilyReferenceIndex,
    _shares_sequence_family,
    _unique_de_novo_sequence,
    build_multifront_rule_action_plan,
)
from pepagent.provenance.hashing import sha256_text
from pepagent.sequence_family import cluster_sequence_families, ungapped_identity_and_coverage
from pepagent.workers import autoresearch_activities
from pepagent.workers.autoresearch_activities import (
    _compile_pepmlm_action,
    _heartbeat_activity_stage,
    plan_autoresearch_actions,
)


def test_proposal_gate_keeps_hydrophobic_descriptors_without_rejecting_them() -> None:
    sequence = "KKKLLLLAAAAKKK"

    _, maximum_run, _ = _sequence_prescreen(sequence)

    assert maximum_run == 8
    assert _hydrophobic_fraction(sequence) > 0.45
    assert _proposal_quality_gate_passes(sequence) is True


def _metric(value: float, direction: str) -> MetricObservation:
    return MetricObservation(
        numeric_value=value,
        direction=direction,  # type: ignore[arg-type]
        unit="dimensionless",
        version="test-v1",
    )


def test_family_edge_prefilter_is_exact_on_exhaustive_short_sequences() -> None:
    sequences = [
        "".join(chars)
        for length in range(1, 8)
        for chars in product("AC", repeat=length)
    ]
    for left in sequences:
        for right in sequences:
            identity, coverage = ungapped_identity_and_coverage(left, right)
            assert _shares_sequence_family(left, (right,)) is (
                identity >= 0.8 and coverage >= 0.8
            )


def test_family_reference_index_matches_exact_scan_and_supports_updates() -> None:
    references = {
        "ACDEFGHIKL",
        "KKKAAASSSGGG",
        "RSTNQKRSTNQK",
        "VVVAAAKKKRRR",
    }
    queries = {
        *references,
        "ACDEFGHIKM",
        "KKKAAATTTGGG",
        "RSTNQKRATNQK",
        "DEDEDEKKKKKK",
        "GGGSSSAAAKKK",
    }
    index = _SequenceFamilyReferenceIndex(references)
    for query in queries:
        assert index.shares_family(query) is _shares_sequence_family(query, references)

    added = "DEDEDEKKKKKK"
    index.update((added,))
    assert index.shares_family(added)


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
            "KKLLKLLKLLKKLLKLLKLL",
            "fam-a",
            (0.1, 2.0, 0.2),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000102",
            "RKWLKLIRKKRKWLKLIRKK",
            "fam-b",
            (2.0, 0.1, 0.3),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000103",
            "KWKLFKKIGKKWKLFKKIGK",
            "fam-c",
            (1.0, 1.1, 0.95),
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000104",
            "RLLRKWLKKLRLLRKWLKKL",
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
    assert plan["requires_generator_gpu"] is True
    assert plan["action_execution_mode"] == "generator_gpu"
    actions = [parse_evolution_action(item) for item in plan["actions"]]
    assert len(actions) == 4
    assert any(
        isinstance(action, MaskedSubstitutionAction)
        and action.operator_id == "autoresearch-rule-substitution-v3"
        for action in actions
    )
    assert any(
        isinstance(action, ControlledCrossoverAction)
        and action.operator_id == "autoresearch-rule-crossover-v3"
        for action in actions
    )
    assert all(
        20
        <= (
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
        )
        <= 30
        for action in actions
    )
    assert plan["gold_candidate_count"] == plan["instability_score_qualified_gold_candidate_count"]
    assert plan["deprecated_ood_qualified_gold_candidate_count"] == plan["gold_candidate_count"]
    assert plan["quality_gate"] == "literal-hard-gates+guruprasad-score-lte-50"
    assert any(delta_sha in action.evidence_sha256s for action in actions)
    serialized = "|".join(str(item.model_dump(mode="json")) for item in actions)
    assert cohort[-1].candidate_id not in serialized
    candidates_by_id = {item.candidate_id: item for item in cohort}
    for action in actions:
        if not isinstance(action, (MaskedSubstitutionAction, ControlledCrossoverAction)):
            continue
        child = apply_evolution_action(action, candidates_by_id)
        instability, hydrophobic_run, charge = _sequence_prescreen(child)
        hydrophobic_fraction = sum(residue in "AVILMFWYC" for residue in child) / len(child)
        assert instability <= 50.0
        assert hydrophobic_run >= 0
        assert 0.0 <= hydrophobic_fraction <= 1.0
        assert charge >= 3.0
        if isinstance(action, ControlledCrossoverAction):
            parent = candidates_by_id[action.parent_candidate_id]
            donor = candidates_by_id[action.donor_candidate_id]
            _, _, parent_charge = _sequence_prescreen(parent.sequence)
            _, _, donor_charge = _sequence_prescreen(donor.sequence)
            assert charge >= max(3.0, min(parent_charge, donor_charge) - 1.0)


def test_multifront_rule_planner_can_freeze_a_cpu_only_action_batch() -> None:
    cohort = _cohort()
    snapshot = build_multi_front_archive(
        cohort,
        MultiFrontArchivePolicy(known_family_keys=("old-family",)),
        generation=0,
    )

    plan = build_multifront_rule_action_plan(
        candidates=cohort,
        snapshot=snapshot,
        branch_key="PBP2a",
        generation=1,
        seed=17,
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        gold_target=50,
        de_novo_quota=0.2,
        pepmlm_targeted_enabled=False,
    )

    assert set(plan["strategies"]) == {"substitution", "crossover", "de_novo"}
    assert plan["pepmlm_targeted_enabled"] is False
    assert plan["requires_generator_gpu"] is False
    assert plan["action_execution_mode"] == "cpu_rule_only"
    assert not any(
        isinstance(parse_evolution_action(item), PepMLMTargetedAction) for item in plan["actions"]
    )


def test_planner_materializes_an_explicit_family_coverage_parent() -> None:
    cohort = _cohort()
    snapshot = build_multi_front_archive(
        cohort,
        MultiFrontArchivePolicy(known_family_keys=("old-family",)),
        generation=0,
    )
    required_parent_id = cohort[2].candidate_id

    plan = build_multifront_rule_action_plan(
        candidates=cohort,
        snapshot=snapshot,
        branch_key="AceA",
        generation=2,
        seed=31,
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        de_novo_quota=0.2,
        pepmlm_targeted_enabled=False,
        required_parent_candidate_ids=(required_parent_id,),
    )

    assert plan["required_parent_candidate_ids"] == [required_parent_id]
    assert plan["forced_parent_action_count"] == 1
    assert plan["unmaterialized_forced_parent_candidate_ids"] == []
    action_sha = plan["forced_parent_action_sha256s"][required_parent_id]
    action = next(
        parse_evolution_action(payload)
        for payload in plan["actions"]
        if payload["action_sha256"] == action_sha
    )
    assert isinstance(action, MaskedSubstitutionAction)
    assert action.parent_candidate_id == required_parent_id
    assert action.operator_id == "autoresearch-rule-family-coverage-substitution-v1"


def test_cpu_only_planner_fills_a_fifty_percent_de_novo_quota() -> None:
    cohort = _cohort()
    snapshot = build_multi_front_archive(
        cohort,
        MultiFrontArchivePolicy(known_family_keys=("old-family",)),
        generation=0,
    )

    plan = build_multifront_rule_action_plan(
        candidates=cohort,
        snapshot=snapshot,
        branch_key="VEGFA",
        generation=2,
        seed=29,
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        gold_target=50,
        de_novo_quota=0.5,
        pepmlm_targeted_enabled=False,
    )

    actions = [parse_evolution_action(item) for item in plan["actions"]]
    de_novo_actions = [
        action for action in actions if getattr(action, "action_type", None) == "de_novo"
    ]
    assert plan["requires_generator_gpu"] is False
    assert plan["action_execution_mode"] == "cpu_rule_only"
    assert plan["de_novo_action_count"] == len(de_novo_actions)
    assert plan["required_de_novo_action_count"] == len(de_novo_actions)
    assert len(de_novo_actions) * 2 >= len(actions)
    assert len({action.proposed_sequence for action in de_novo_actions}) == len(de_novo_actions)
    assert all(action.operator_id == "autoresearch-rule-de-novo-v8" for action in de_novo_actions)
    assert plan["de_novo_profile_policy"]["family_balanced"] is True
    assert plan["de_novo_profile_policy"]["empirical_profile_weight"] == 0.5
    assert plan["de_novo_profile_policy"]["minimum_gold_profile_count"] == 3
    assert plan["de_novo_profile_policy"]["first_order_transition_profile"] is True
    assert all(_de_novo_prescreen_passes(action.proposed_sequence) for action in de_novo_actions)
    assert plan["sequence_prescreen_policy"] == {
        "instability_method": "Guruprasad-Reddy-Pandit-1990-via-Biopython-ProtParam",
        "instability_max_inclusive": 50.0,
        "all_rule_proposals_share_quality_gate": True,
        "hydrophobic_run_is_descriptor_not_gate": True,
        "hydrophobic_fraction_is_descriptor_not_gate": True,
        "mutation_net_charge_minimum": 3.0,
        "crossover_charge_loss_max": 1.0,
        "crossover_net_charge_minimum": 3.0,
        "de_novo_instability_max_inclusive": 50.0,
        "de_novo_net_charge_minimum": 3.0,
        "de_novo_net_charge_maximum": 10.0,
        "de_novo_histidine_fraction_maximum": 0.12,
        "toxin_and_hemolysis_remain_score_all_only": True,
    }


def test_rule_de_novo_prescreen_is_stable_across_all_six_target_branches() -> None:
    for branch_key in ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1"):
        known_sequences: set[str] = set()
        for seed in range(64):
            sequence = _unique_de_novo_sequence(
                branch_key=branch_key,
                seed=seed,
                known_sequences=known_sequences,
            )
            known_sequences.add(sequence)
            instability, hydrophobic_run, charge = _sequence_prescreen(sequence)
            hydrophobic_fraction = sum(residue in "AVILMFWYC" for residue in sequence) / len(
                sequence
            )

            assert instability <= 50.0
            assert hydrophobic_run >= 0
            assert 0.0 <= hydrophobic_fraction <= 1.0
            assert charge >= 3.0
            assert charge <= 10.0
            assert sequence.count("H") / len(sequence) <= 0.12

        assignments = cluster_sequence_families(known_sequences)
        assert len({item.family_key for item in assignments}) == 64


def test_rule_de_novo_avoids_historical_family_representatives() -> None:
    historical_representative = "KRHNETAILVKRHNETAILV"
    sequence = _unique_de_novo_sequence(
        branch_key="VEGFA",
        seed=31,
        known_sequences=set(),
        family_reference_sequences=(historical_representative,),
    )

    assignments = cluster_sequence_families((historical_representative, sequence))
    assert len({item.family_key for item in assignments}) == 2


def test_adaptive_de_novo_alphabet_is_deterministic_and_profile_weighted() -> None:
    profile = ("KKKKRRHNQSTAG", "KKKRRHNQSTAG")
    alphabet = _adaptive_de_novo_alphabet(profile)

    assert alphabet == _adaptive_de_novo_alphabet(tuple(reversed(profile)))
    assert alphabet.count("K") > alphabet.count("A")
    assert alphabet.count("R") > alphabet.count("A")
    sequence = _unique_de_novo_sequence(
        branch_key="VEGFA",
        seed=41,
        known_sequences=set(),
        residue_alphabet=alphabet,
    )
    assert _de_novo_prescreen_passes(sequence)


def test_de_novo_transition_profile_preserves_elite_local_context() -> None:
    profile = ("KKGSKKGSKKGS", "RRGSRRGSRRGS")

    transitions = _adaptive_de_novo_transition_alphabets(profile)
    sequence = _unique_de_novo_sequence(
        branch_key="VEGFA",
        seed=43,
        known_sequences=set(),
        residue_alphabet=_adaptive_de_novo_alphabet(profile),
        transition_alphabets=transitions,
    )

    assert transitions == _adaptive_de_novo_transition_alphabets(tuple(reversed(profile)))
    assert transitions["K"].count("G") > transitions["K"].count("D")
    assert transitions["G"].count("S") > transitions["G"].count("D")
    assert _de_novo_prescreen_passes(sequence)


def test_de_novo_profile_weights_families_instead_of_family_population() -> None:
    candidates = (
        _candidate("a", "KKKKGGGGSSSSTTTTAAAA", "fam-a", (0.1, 0.2, 0.3)),
        _candidate("b", "KKKKGGGGSSSSTTTTAAAR", "fam-a", (0.1, 0.2, 0.3)),
        _candidate("c", "RRRRGGGGNNNNQQQQVVVV", "fam-b", (0.1, 0.2, 0.3)),
    )

    profile = _family_balanced_de_novo_profile(candidates)

    assert profile == (
        "KKKKGGGGSSSSTTTTAAAA",
        "RRRRGGGGNNNNQQQQVVVV",
    )


def test_planner_emits_progress_heartbeats_between_expensive_stages(monkeypatch) -> None:
    heartbeats: list[dict[str, object]] = []
    monkeypatch.setattr(
        autoresearch_activities.activity,
        "heartbeat",
        lambda payload: heartbeats.append(payload),
    )

    _heartbeat_activity_stage("planner_candidates_loaded", candidate_count=768)

    assert heartbeats == [{"stage": "planner_candidates_loaded", "candidate_count": 768}]
    source = inspect.getsource(plan_autoresearch_actions)
    for stage in (
        "planner_hydrate_request",
        "planner_candidates_loaded",
        "planner_evaluations_loaded",
        "planner_complete_evidence_selected",
        "planner_archive_inputs_loaded",
        "planner_action_plan_built",
        "planner_evidence_stored",
    ):
        assert stage in source


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
    assert (
        validate_action_child(
            action,
            {parent.candidate_id: parent},
            "KRLLKLLKLLKKLLKLLKLL",
        )
        == "KRLLKLLKLLKKLLKLLKLL"
    )


def test_planner_accepts_short_instability_ood_parent_when_score_is_below_50() -> None:
    short = _candidate(
        "00000000-0000-0000-0000-000000000201",
        "KKLKKLKKLK",
        "short-ood",
        (0.1, 0.2, 0.9),
    ).model_copy(
        update={
            "metrics": {
                **_candidate(
                    "00000000-0000-0000-0000-000000000201",
                    "KKLKKLKKLK",
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


def test_planner_rejects_parent_when_instability_score_is_above_50() -> None:
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
                    numeric_value=50.000001,
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
        assert "Guruprasad instability <=50" in str(error)
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
