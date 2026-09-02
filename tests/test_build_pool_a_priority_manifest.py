import csv
from pathlib import Path

from analysis.build_pool_a_priority_manifest import select


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _row(family: str, sequence: str, minimum: float) -> dict[str, str]:
    return {
        "branch_key": "gyra",
        "candidate_id": f"candidate-{sequence}",
        "sequence": sequence,
        "sequence_sha256": f"sha-{sequence}",
        "family_key_80_80": family,
        "formal_12_complete": "true",
        "formal_metric_count": "12",
        "display_eligible": "true",
        "excellent_sequence_stage_calibrated": "true",
        "activity_model_support_count_calibrated": "3",
        "guruprasad_instability_index": "50",
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
        "historical_exact_replay": "false",
        "challenger_conflict_status": "cross_model_disagreement_retained",
        "calibrated_hemolysis_probability": "0.2",
        "amp_read_log10_mic_um__parent_benefit_percentile": str(minimum),
        "llamp_log10_mic_um__parent_benefit_percentile": str(minimum + 0.1),
        "macrel_amp_probability__parent_benefit_percentile": str(minimum + 0.2),
    }


def test_select_keeps_best_candidate_per_family_and_retains_conflict(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    _write(
        source,
        [
            _row("family-a", "AAA", 0.6),
            _row("family-a", "BBB", 0.8),
            _row("family-b", "CCC", 0.7),
        ],
    )

    rows, summary = select([("gyra", "run-1", source)], 50)

    assert [row["sequence"] for row in rows] == ["BBB", "CCC"]
    assert rows[0]["challenger_conflict_status"] == (
        "cross_model_disagreement_retained"
    )
    assert summary["gyra"]["strict_pre_rosetta_family_count"] == 2


def test_select_combines_generations_without_cross_run_identity_merge(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write(first, [_row("family-a", "AAA", 0.6)])
    _write(second, [_row("family-b", "BBB", 0.8)])

    rows, summary = select(
        [("gyra", "run-1", first), ("gyra", "run-2", second)], 50
    )

    assert {(row["sequence"], row["subject_run_id"]) for row in rows} == {
        ("AAA", "run-1"),
        ("BBB", "run-2"),
    }
    assert summary["gyra"]["strict_pre_rosetta_family_count"] == 2
