from __future__ import annotations

from pepagent.autoresearch_quality_diversity import (
    BehaviorSpacePolicy,
    BehaviorVector,
    QualityDiversityCandidate,
    alpha_helix_hydrophobic_moment,
    behavior_cell_id,
    build_quality_diversity_archive,
)


def _candidate(
    candidate_id: str,
    *,
    charge_density: float,
    hydrophobicity: float,
    quality: float,
    operator: str = "mutation",
    display_eligible: bool = True,
) -> QualityDiversityCandidate:
    return QualityDiversityCandidate(
        candidate_id=candidate_id,
        sequence="KKKAAAGGGSSS",
        behavior=BehaviorVector(
            charge_density=charge_density,
            hydrophobicity=hydrophobicity,
            hydrophobic_moment=0.2,
            length=12,
        ),
        quality=quality,
        display_eligible=display_eligible,
        activity_support_count=2,
        hemolysis_probability=0.1,
        hemolysis_label="low",
        operator_name=operator,
        parent_behavior=BehaviorVector(
            charge_density=charge_density + 0.1,
            hydrophobicity=hydrophobicity - 0.1,
            hydrophobic_moment=0.1,
            length=11,
        ),
    )


def test_quality_diversity_archive_separates_new_cells_replacements_and_quality() -> None:
    policy = BehaviorSpacePolicy(
        charge_density_edges=(-1.0, 0.0, 0.5, 1.0),
        hydrophobicity_edges=(0.0, 0.5, 1.0),
        hydrophobic_moment_edges=(0.0, 0.5, 2.0),
        length_edges=(10.0, 20.0, 31.0),
    )
    prior = [_candidate("prior", charge_density=0.2, hydrophobicity=0.2, quality=0.4)]
    batch = [
        _candidate("replace", charge_density=0.2, hydrophobicity=0.2, quality=0.8),
        _candidate("new-best", charge_density=0.7, hydrophobicity=0.7, quality=0.7),
        _candidate("new-loser", charge_density=0.7, hydrophobicity=0.7, quality=0.6),
        _candidate(
            "invalid",
            charge_density=-0.2,
            hydrophobicity=0.2,
            quality=0.9,
            display_eligible=False,
        ),
    ]

    state = build_quality_diversity_archive(prior, batch, policy)
    contributions = {item.candidate_id: item for item in state.contributions}

    assert contributions["replace"].contribution == "incumbent_replacement"
    assert contributions["new-best"].contribution == "empty_cell"
    assert contributions["new-loser"].contribution == "same_cell_non_elite"
    assert contributions["invalid"].contribution == "quality_gate_failed"
    assert state.diversity_gain == 1
    assert state.incumbent_replacement_count == 1
    assert state.eligible_batch_candidate_count == 3
    assert state.best_peptide_quality == 0.8
    assert state.mean_peptide_quality == 0.75
    assert state.archive_qd_score == 1.5
    assert state.valid_cell_coverage == 2 / policy.total_cell_count
    assert state.maximum_cell_concentration == 2 / 3
    assert state.archive_relative_novelty == 1 / 3
    collision = next(item for item in state.batch_cell_occupancies if len(item.candidate_ids) == 2)
    assert collision.candidate_ids == ("new-best", "new-loser")
    assert collision.batch_elite_candidate_id == "new-best"
    mutation = state.operator_effects[0]
    assert mutation.new_cell_count == 1
    assert mutation.incumbent_replacement_count == 1
    assert mutation.repeated_cell_count == 1
    assert mutation.mean_property_displacement is not None
    assert mutation.mean_property_displacement.charge_density == -0.1


def test_behavior_cells_are_fixed_and_alpha_helix_moment_is_deterministic() -> None:
    policy = BehaviorSpacePolicy()
    behavior = BehaviorVector(
        charge_density=0.2,
        hydrophobicity=0.45,
        hydrophobic_moment=0.3,
        length=20,
    )

    assert behavior_cell_id(behavior, policy) == "q5-h3-m3-l2"
    assert alpha_helix_hydrophobic_moment("KKKAAAGGGSSS") == (
        alpha_helix_hydrophobic_moment("KKKAAAGGGSSS")
    )
    assert policy.total_cell_count == 2160
