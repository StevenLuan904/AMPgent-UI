from __future__ import annotations

import pytest
from pydantic import ValidationError

from pepagent.autoresearch_closed_loop import (
    ARCHIVE_NAMES,
    CandidateEvidence,
    ContinuationPolicy,
    ControlledCrossoverAction,
    CrossoverFragment,
    DeNovoAction,
    MaskedSubstitutionAction,
    MetricObservation,
    MultiFrontArchivePolicy,
    ResidueSubstitution,
    apply_evolution_action,
    build_multi_front_archive,
    compute_parent_child_delta,
    parse_evolution_action,
    parse_persisted_archive_snapshot,
    update_multi_front_archive,
    validate_action_child,
)
from pepagent.provenance.hashing import sha256_text


def _metric(
    value: float,
    direction: str,
    *,
    version: str = "v1",
    unit: str = "score",
    out_of_domain: bool = False,
) -> MetricObservation:
    return MetricObservation(
        numeric_value=value,
        direction=direction,
        unit=unit,
        version=version,
        out_of_domain=out_of_domain,
    )


def _candidate(
    candidate_id: str,
    sequence: str,
    family: str,
    activity: tuple[float, float, float],
    *,
    hemolysis: float,
    toxicity: float,
    instability: float,
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
            "macrel_hemolysis_probability": _metric(hemolysis, "minimize"),
            "toxinpred3_hybrid_score": _metric(toxicity, "minimize"),
            "guruprasad_instability_index": _metric(instability, "minimize"),
        },
    )


def _archive_candidates() -> tuple[CandidateEvidence, ...]:
    return (
        _candidate(
            "A",
            "ACDEFGHIKL",
            "family-a",
            (1.0, 1.0, 0.90),
            hemolysis=0.10,
            toxicity=0.10,
            instability=20.0,
        ),
        _candidate(
            "B",
            "LMNPQRSTVW",
            "family-b",
            (0.4, 3.0, 0.40),
            hemolysis=0.05,
            toxicity=0.05,
            instability=15.0,
        ),
        _candidate(
            "C",
            "KKLLKLLKLL",
            "family-c",
            (3.0, 0.4, 0.50),
            hemolysis=0.20,
            toxicity=0.20,
            instability=30.0,
        ),
        _candidate(
            "D",
            "GIGKFLHSAK",
            "family-d",
            (2.0, 2.0, 0.98),
            hemolysis=0.40,
            toxicity=0.30,
            instability=25.0,
        ),
        _candidate(
            "E",
            "RWRWRWRWRW",
            "family-e",
            (4.0, 4.0, 0.20),
            hemolysis=0.01,
            toxicity=0.01,
            instability=10.0,
        ),
    )


def _policy() -> MultiFrontArchivePolicy:
    return MultiFrontArchivePolicy(
        consensus_rank_fraction=0.30,
        endpoint_rank_fraction=0.0,
        model_disagreement_rank_span=0.50,
        known_family_keys=("family-a",),
    )


def _action_common() -> dict[str, object]:
    return {
        "branch_key": "acea",
        "generation": 1,
        "seed": 17,
        "operator_id": "controlled-operator-v1",
        "operator_release_sha256": "a" * 64,
        "expected_improvement_metrics": ("amp_read_log10_mic_um",),
        "protected_metrics": ("macrel_amp_probability",),
        "evidence_sha256s": ("1" * 64, "2" * 64),
    }


def test_multi_front_archive_keeps_consensus_endpoints_conflicts_and_overlap() -> None:
    snapshot = build_multi_front_archive(_archive_candidates(), _policy(), generation=0)

    assert set(snapshot.archive_members) == set(ARCHIVE_NAMES)
    assert snapshot.archive_members["activity_consensus"] == ("A",)
    assert snapshot.archive_members["amp_read_endpoint"] == ("B",)
    assert snapshot.archive_members["llamp_endpoint"] == ("C",)
    assert snapshot.archive_members["macrel_endpoint"] == ("D",)
    assert set(snapshot.archive_members["model_disagreement"]) == {"B", "C", "D"}
    assert "A" in snapshot.archive_members["activity_safety_balance"]
    assert "A" in snapshot.archive_members["stability_degradation"]
    assert "A" not in snapshot.archive_members["novel_family"]
    assert set(snapshot.archive_members["novel_family"]) == {"B", "C", "D", "E"}
    assert snapshot.archive_sha256 == snapshot.model_validate(
        snapshot.model_dump(mode="json", exclude={"archive_sha256"})
    ).archive_sha256


def test_persisted_archive_hash_witness_is_verified_before_typed_ingest() -> None:
    snapshot = build_multi_front_archive(_archive_candidates(), _policy(), generation=0)
    persisted = snapshot.model_dump(mode="json")

    recovered = parse_persisted_archive_snapshot(persisted)
    assert recovered == snapshot
    assert recovered.archive_sha256 == persisted["archive_sha256"]

    drifted = dict(persisted)
    drifted["archive_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="archive SHA-256 witness drifted"):
        parse_persisted_archive_snapshot(drifted)


def test_archive_rejects_weighted_total_and_duplicate_sequence_identity() -> None:
    candidate = _archive_candidates()[0]
    payload = candidate.model_dump(mode="python")
    payload["metrics"]["weighted_score"] = _metric(1.0, "maximize")
    with pytest.raises(ValidationError, match="weighted scalar metrics are forbidden"):
        CandidateEvidence.model_validate(payload)

    duplicate = candidate.model_copy(update={"candidate_id": "duplicate"})
    with pytest.raises(ValueError, match="globally sequence-deduplicated"):
        build_multi_front_archive((candidate, duplicate), _policy(), generation=0)


def test_replayable_masked_substitution_crossover_and_de_novo_actions() -> None:
    parent, donor = _archive_candidates()[:2]
    candidates = {parent.candidate_id: parent, donor.candidate_id: donor}
    masked = MaskedSubstitutionAction(
        **_action_common(),
        parent_candidate_id=parent.candidate_id,
        parent_sequence_sha256=parent.sequence_sha256,
        substitutions=(
            ResidueSubstitution(
                position_zero_based=1, from_residue="C", to_residue="R"
            ),
            ResidueSubstitution(
                position_zero_based=4, from_residue="F", to_residue="W"
            ),
        ),
    )
    assert apply_evolution_action(masked, candidates) == "ARDEWGHIKL"
    serialized = masked.model_dump(mode="json")
    assert serialized["action_sha256"] == masked.action_sha256
    assert parse_evolution_action(serialized) == masked

    crossover = ControlledCrossoverAction(
        **_action_common(),
        parent_candidate_id=parent.candidate_id,
        parent_sequence_sha256=parent.sequence_sha256,
        donor_candidate_id=donor.candidate_id,
        donor_sequence_sha256=donor.sequence_sha256,
        fragments=(
            CrossoverFragment(
                source_role="primary_parent",
                source_start_zero_based=0,
                source_end_exclusive=5,
            ),
            CrossoverFragment(
                source_role="donor_parent",
                source_start_zero_based=5,
                source_end_exclusive=10,
            ),
        ),
        post_crossover_substitutions=(
            ResidueSubstitution(
                position_zero_based=5, from_residue="R", to_residue="K"
            ),
        ),
    )
    assert apply_evolution_action(crossover, candidates) == "ACDEFKSTVW"

    de_novo = DeNovoAction(
        **_action_common(), peptide_length=10, proposed_sequence="KKLLKLLKLL"
    )
    assert apply_evolution_action(de_novo, candidates) == "KKLLKLLKLL"
    with pytest.raises(ValueError, match="does not replay"):
        validate_action_child(masked, candidates, "ACDEFGHIKL")


def test_action_rejects_hash_drift_hidden_edits_and_weighted_objective() -> None:
    parent = _archive_candidates()[0]
    action = MaskedSubstitutionAction(
        **_action_common(),
        parent_candidate_id=parent.candidate_id,
        parent_sequence_sha256=parent.sequence_sha256,
        substitutions=(
            ResidueSubstitution(
                position_zero_based=1, from_residue="C", to_residue="R"
            ),
        ),
    )
    serialized = action.model_dump(mode="json")
    serialized["seed"] = 18
    with pytest.raises(ValueError, match="SHA-256 drifted"):
        parse_evolution_action(serialized)

    bad_common = _action_common()
    bad_common["expected_improvement_metrics"] = ("weighted_score",)
    with pytest.raises(ValidationError, match="weighted scalar"):
        MaskedSubstitutionAction(
            **bad_common,
            parent_candidate_id=parent.candidate_id,
            parent_sequence_sha256=parent.sequence_sha256,
            substitutions=(
                ResidueSubstitution(
                    position_zero_based=1, from_residue="C", to_residue="R"
                ),
            ),
        )


def test_parent_child_delta_tracks_improvement_regression_and_contract_mismatch() -> None:
    parent = _archive_candidates()[0].model_copy(
        update={
            "metrics": {
                "amp_read_log10_mic_um": _metric(2.0, "minimize", unit="log10_uM"),
                "macrel_amp_probability": _metric(0.8, "maximize", unit="probability"),
            }
        }
    )
    action = MaskedSubstitutionAction(
        **_action_common(),
        parent_candidate_id=parent.candidate_id,
        parent_sequence_sha256=parent.sequence_sha256,
        substitutions=(
            ResidueSubstitution(
                position_zero_based=1, from_residue="C", to_residue="R"
            ),
            ResidueSubstitution(
                position_zero_based=4, from_residue="F", to_residue="W"
            ),
        ),
    )
    child_sequence = "ARDEWGHIKL"
    child = CandidateEvidence(
        candidate_id="child",
        sequence=child_sequence,
        sequence_sha256=sha256_text(child_sequence),
        family_key="child-family",
        metrics={
            "amp_read_log10_mic_um": _metric(1.0, "minimize", unit="log10_uM"),
            "macrel_amp_probability": _metric(0.7, "maximize", unit="probability"),
        },
    )
    delta = compute_parent_child_delta(action, child, {parent.candidate_id: parent})
    metrics = {item.metric_name: item for item in delta.baselines[0].metrics}
    assert metrics["amp_read_log10_mic_um"].raw_delta_child_minus_parent == -1.0
    assert metrics["amp_read_log10_mic_um"].improvement_delta == 1.0
    assert metrics["macrel_amp_probability"].improvement_delta == pytest.approx(-0.1)
    assert delta.expected_metrics_improved == ("amp_read_log10_mic_um",)
    assert delta.protected_metrics_regressed == ("macrel_amp_probability",)

    mismatched = child.model_copy(
        update={
            "metrics": {
                **child.metrics,
                "amp_read_log10_mic_um": _metric(
                    1.0, "minimize", unit="log10_uM", version="v2"
                ),
            }
        }
    )
    mismatch_delta = compute_parent_child_delta(
        action, mismatched, {parent.candidate_id: parent}
    )
    assert mismatch_delta.expected_metrics_incomparable == (
        "amp_read_log10_mic_um",
    )


def test_archive_update_continues_switches_and_freezes_successor_without_stopping() -> None:
    candidates = _archive_candidates()
    policy = _policy()
    previous = build_multi_front_archive(candidates[:1], policy, generation=0)
    continuation = ContinuationPolicy(
        maximum_generations_per_run=3,
        minimum_high_quality_candidates=10,
        stagnation_patience_generations=2,
    )
    gained = update_multi_front_archive(
        previous,
        candidates,
        policy,
        continuation,
        generation=1,
    )
    assert gained.new_candidate_ids == ("B", "C", "D", "E")
    assert gained.new_family_count == 4
    assert gained.continuation.next_action == "continue_evolution"
    assert gained.continuation.continue_required is True
    assert gained.continuation.high_quality_candidate_count == 0
    assert gained.continuation.literal_high_quality_candidate_count > 0
    assert gained.continuation.quality_gate == "ood-qualified-wetlab-20-to-30-aa"

    stalled = update_multi_front_archive(
        gained.current,
        candidates,
        policy,
        continuation,
        generation=2,
        prior_consecutive_stagnant_generations=1,
    )
    assert stalled.continuation.next_action == "switch_strategy"
    assert stalled.continuation.continue_required is True

    successor = update_multi_front_archive(
        stalled.current,
        candidates,
        policy,
        continuation,
        generation=3,
        prior_consecutive_stagnant_generations=2,
    )
    assert successor.continuation.next_action == "freeze_successor_run"
    assert successor.continuation.continue_required is True
    assert successor.update_sha256
