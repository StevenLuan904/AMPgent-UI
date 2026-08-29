from __future__ import annotations

from pepagent.autoresearch_seed_selection import (
    select_instability_score_qualified_seed_rows,
)
from pepagent.provenance.hashing import sha256_text


def _row(
    sequence: str,
    *,
    family: str,
    support: int,
    pareto: int = 1,
    target: str = "pbp2a",
    ood: bool = False,
) -> dict[str, str]:
    return {
        "activity_model_support_count": str(support),
        "candidate_id": f"candidate-{sequence}",
        "display_eligible": "True",
        "family_key_80_80": family,
        "family_size_80_80_with_baseline": "1",
        "formal_metric_count": "12",
        "formal_metrics_complete": "True",
        "guruprasad_instability_index": "20.0",
        "guruprasad_instability_ood": str(ood),
        "macrel_hemolysis_label": "low",
        "macrel_hemolysis_probability": "0.1",
        "pareto_depth_within_expansion_target": str(pareto),
        "safety_labels_pass": "True",
        "sequence": sequence,
        "sequence_sha256": sha256_text(sequence),
        "target_key": target,
        "toxinpred3_hybrid_score": "0.0",
        "toxinpred3_label": "Non-Toxin",
        "valid_sequence": "True",
        "formal_twelve_marker": "preserved",
    }


def test_selection_exhausts_consensus_families_before_supplemental() -> None:
    rows = [
        _row("ACDEFGHIKLMNPQRSTVWY", family="f-low", support=1, pareto=1),
        _row("CDEFGHIKLMNPQRSTVWYA", family="f-two", support=2, pareto=4),
        _row("DEFGHIKLMNPQRSTVWYAC", family="f-three", support=3, pareto=8),
    ]

    result = select_instability_score_qualified_seed_rows(
        rows, target_key="PBP2A", count=2
    )

    assert {row["family_key_80_80"] for row in result.selected_rows} == {
        "f-two",
        "f-three",
    }
    assert result.consensus_family_count == 2
    assert result.supplemental_family_count == 0
    assert all(row["formal_twelve_marker"] == "preserved" for row in result.selected_rows)


def test_selection_is_family_unique_deterministic_and_uses_pareto_front() -> None:
    weaker = _row(
        "EFGHIKLMNPQRSTVWYACD", family="f-shared", support=2, pareto=3
    )
    better = _row(
        "FGHIKLMNPQRSTVWYACDE", family="f-shared", support=2, pareto=1
    )
    other = _row("GHIKLMNPQRSTVWYACDEF", family="f-other", support=1, pareto=1)

    forward = select_instability_score_qualified_seed_rows(
        [weaker, other, better], target_key="pbp2a", count=2
    )
    reverse = select_instability_score_qualified_seed_rows(
        [better, other, weaker], target_key="pbp2a", count=2
    )

    assert [row["sequence_sha256"] for row in forward.selected_rows] == [
        row["sequence_sha256"] for row in reverse.selected_rows
    ]
    assert better["sequence_sha256"] in {
        row["sequence_sha256"] for row in forward.selected_rows
    }
    assert weaker["sequence_sha256"] not in {
        row["sequence_sha256"] for row in forward.selected_rows
    }


def test_selection_excludes_structure_history_but_keeps_score_qualified_ood_rows() -> None:
    excluded = _row("HIKLMNPQRSTVWYACDEFG", family="f-excluded", support=3)
    short = _row("IKLMNPQRSTVWYACDEFG", family="f-short", support=3, ood=True)
    eligible = _row("KLMNPQRSTVWYACDEFGHI", family="f-eligible", support=1)

    result = select_instability_score_qualified_seed_rows(
        [excluded, short, eligible],
        target_key="pbp2a",
        count=1,
        excluded_sequence_sha256s=[excluded["sequence_sha256"]],
    )

    assert [row["family_key_80_80"] for row in result.selected_rows] == ["f-short"]
    assert result.excluded_structure_history_count == 1
    assert result.eligible_family_count == 2


def test_selection_fails_closed_when_unique_family_quota_is_unavailable() -> None:
    rows = [
        _row("LMNPQRSTVWYACDEFGHIK", family="f-only", support=2),
        _row("MNPQRSTVWYACDEFGHIKL", family="f-only", support=3),
    ]

    try:
        select_instability_score_qualified_seed_rows(
            rows, target_key="pbp2a", count=2
        )
    except ValueError as error:
        assert "only 1 eligible unique families" in str(error)
    else:
        raise AssertionError("family-short seed selection should fail closed")
