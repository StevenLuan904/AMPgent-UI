import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))

from write_unavailable_challenger_receipt import write


def test_unavailable_challenger_never_claims_no_conflict(tmp_path: Path) -> None:
    source = tmp_path / "scores.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["candidate_id", "sequence", "sequence_sha256", "branch_key", "target_key"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "proposal-a",
                "sequence": "AAAA",
                "sequence_sha256": "a" * 64,
                "branch_key": "gyra",
                "target_key": "GyrA",
            }
        )
    receipt = write(source, tmp_path / "challenger")
    assert receipt["challenger_status"] == "runtime_unavailable"
    assert receipt["challenger_no_conflict_count"] == 0
    rows = list(csv.DictReader((tmp_path / "challenger" / "challenger_review.csv").open()))
    assert rows[0]["challenger_conflict_status"] == "not_assessed"
