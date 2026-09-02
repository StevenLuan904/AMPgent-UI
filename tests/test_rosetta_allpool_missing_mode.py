from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path

import pytest

SOURCE_PATH = (
    Path(__file__).parents[1]
    / "deploy"
    / "remote"
    / "run_autoresearch_rosetta_batch.py"
)
SPEC = importlib.util.spec_from_file_location("run_autoresearch_rosetta_batch", SOURCE_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def _row(*, branch_key: str, sequence: str, rank: int) -> dict[str, str]:
    return {
        "branch_key": branch_key,
        "proposal_rank": str(rank),
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "candidate_id": f"candidate-{branch_key}-{rank}",
        "guruprasad_instability_index": "75.0",
        "display_eligible": "false",
        "formal_12_complete": "false",
        "structure_queue_selected": "true",
        "rosetta_dg_receipt_status": "missing",
        "challenger_conflict_status": "not_required_for_all_pool_scoring",
    }


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_allpool_mode_accepts_non_display_sequence_with_finite_score(tmp_path: Path) -> None:
    path = tmp_path / "queue.csv"
    _write(path, [_row(branch_key="acea", sequence="ACDEFGHIKL", rank=1)])

    rows = RUNNER.load_rows(path, allow_all_pool_missing=True)

    assert len(rows) == 1


def test_default_mode_retains_strict_admission_gate(tmp_path: Path) -> None:
    path = tmp_path / "queue.csv"
    _write(path, [_row(branch_key="acea", sequence="ACDEFGHIKL", rank=1)])

    with pytest.raises(ValueError, match="frozen hard gate"):
        RUNNER.load_rows(path)


def test_same_sequence_is_distinct_across_targets(tmp_path: Path) -> None:
    path = tmp_path / "queue.csv"
    sequence = "ACDEFGHIKL"
    _write(
        path,
        [
            _row(branch_key="acea", sequence=sequence, rank=1),
            _row(branch_key="gyra", sequence=sequence, rank=2),
        ],
    )

    rows = RUNNER.load_rows(path, allow_all_pool_missing=True)

    assert [(row["branch_key"], row["sequence"]) for row in rows] == [
        ("acea", sequence),
        ("gyra", sequence),
    ]
