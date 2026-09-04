from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "analysis/run_pbp2a_pepmlm_qd_gap_selection.py"
SPEC = importlib.util.spec_from_file_location("pbp2a_pepmlm_qd_gap_selection", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
select = MODULE.select
SCORES = ROOT / (
    "reports/autoresearch_lineage_round104_pbp2a_pepmlm_denovo_fullscore_20260901/"
    "candidate_scores_calibrated.csv"
)
CHALLENGER = ROOT / (
    "reports/autoresearch_lineage_round104_pbp2a_pepmlm_denovo_challenger_20260901/"
    "challenger_review.csv"
)
ARCHIVE = ROOT / "reports/pbp2a_pepmlm_source_increment_20260904/qd_archive_pbp2a.json"


def test_selection_is_bounded_and_has_frozen_source_identity(tmp_path: Path) -> None:
    receipt = select(
        scores_path=SCORES,
        challenger_path=CHALLENGER,
        archive_path=ARCHIVE,
        output_dir=tmp_path / "selected",
        limit=12,
    )
    assert receipt["target_key"] == "pbp2a"
    assert receipt["source"] == "PepMLM"
    assert receipt["artifact_replay"] is True
    assert receipt["proposal_count"] == 12
    assert receipt["formal12_count"] == 12
    assert receipt["display_count"] == 12
    assert receipt["activity_support_ge2_count"] == 12
    assert receipt["challenger_reviewed_count"] == 12
    assert receipt["qd_quality_eligible_count"] == 12
    with (tmp_path / "selected" / "candidate_scores.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        score_rows = list(csv.DictReader(handle))
    with (tmp_path / "selected" / "challenger_review.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        challenger_rows = list(csv.DictReader(handle))
    assert len(score_rows) == len(challenger_rows) == 12
    assert {row["sequence_sha256"] for row in score_rows} == {
        row["sequence_sha256"] for row in challenger_rows
    }
    qd = json.loads((tmp_path / "selected" / "qd_receipt.json").read_text(encoding="utf-8"))
    assert qd["target_key"] == "pbp2a"
    assert qd["source_scope"] == "artifact_replay"
    assert qd["policy"]["policy_id"] == "ampgent-peptide-behavior-space-v1"
    assert qd["historical_runs_modified"] is False
    assert {row["score_all_status"] for row in score_rows} == {"complete"}
    assert {row["historical_exact_replay"] for row in score_rows} == {"false"}
