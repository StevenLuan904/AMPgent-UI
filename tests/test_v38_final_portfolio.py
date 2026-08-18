from uuid import UUID

import pytest

from pepagent.v38_final_portfolio import (
    StructureScoreEvidence,
    build_v38_final_portfolio,
)

IDS = (UUID(int=1), UUID(int=2))


def _evidence() -> tuple[StructureScoreEvidence, ...]:
    rows = []
    scores = {
        IDS[0]: {"gyrA": (-10.0, -5.0), "pbp2a": (-7.0, -2.0)},
        IDS[1]: {"gyrA": (-8.0, -7.0), "pbp2a": (-9.0, -4.0)},
    }
    for candidate_id, targets in scores.items():
        for target, (native, wrong) in targets.items():
            for lane, score in (("native", native), ("wrong_pocket", wrong)):
                for seed in (11, 12):
                    for ordinal in (0, 1):
                        rows.append(
                            StructureScoreEvidence(
                                candidate_id=candidate_id,
                                target_key=target,
                                control_lane=lane,
                                boltz_seed=seed,
                                decoy_ordinal=ordinal,
                                total_score=score,
                            )
                        )
    return tuple(rows)


def test_final_portfolio_keeps_three_unweighted_views() -> None:
    result = build_v38_final_portfolio(
        sequence_pareto_fronts={IDS[0]: 1, IDS[1]: 2},
        evidence=_evidence(),
        target_keys=("gyrA", "pbp2a"),
        expected_seeds=(11, 12),
        decoys_per_seed=2,
    )

    assert result.target_agnostic_front_one_candidate_ids == (IDS[0],)
    assert result.per_target_front_one_candidate_ids["gyrA"] == (IDS[0],)
    assert result.per_target_front_one_candidate_ids["pbp2a"] == (IDS[1],)
    assert result.cross_target_front_one_candidate_ids == IDS
    assert result.weighted_total_used is False
    assert result.structure_used_as_hard_safety_gate is False


def test_final_portfolio_rejects_incomplete_decoy_evidence() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        build_v38_final_portfolio(
            sequence_pareto_fronts={IDS[0]: 1, IDS[1]: 2},
            evidence=_evidence()[:-1],
            target_keys=("gyrA", "pbp2a"),
            expected_seeds=(11, 12),
            decoys_per_seed=2,
        )


def test_final_portfolio_rejects_duplicate_evidence_identity() -> None:
    evidence = _evidence()
    with pytest.raises(ValueError, match="duplicate"):
        build_v38_final_portfolio(
            sequence_pareto_fronts={IDS[0]: 1, IDS[1]: 2},
            evidence=(*evidence, evidence[0]),
            target_keys=("gyrA", "pbp2a"),
            expected_seeds=(11, 12),
            decoys_per_seed=2,
        )
