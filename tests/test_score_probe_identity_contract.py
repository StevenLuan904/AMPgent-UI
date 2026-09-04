from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "analysis" / "autoresearch_proposal_score_probe.py"
SPEC = importlib.util.spec_from_file_location("score_probe_identity", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, sequences: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sequence", "sequence_sha256"])
        writer.writeheader()
        for sequence in sequences:
            writer.writerow(
                {"sequence": sequence, "sequence_sha256": MODULE.sha256_text(sequence)}
            )


def test_score_all_identity_contract_rejects_zero_overlap(tmp_path: Path) -> None:
    expected = tmp_path / "proposals.csv"
    actual = tmp_path / "score_input.csv"
    _write(expected, ["AAAA", "CCCC"])
    _write(actual, ["GGGG", "TTTT", "NNNN"])

    with pytest.raises(ValueError, match="overlap=0"):
        with actual.open(encoding="utf-8", newline="") as stream:
            MODULE._assert_input_identity(
                list(csv.DictReader(stream)), expected_path=expected, source=actual
            )


def test_score_all_identity_contract_accepts_exact_order_and_hashes(tmp_path: Path) -> None:
    expected = tmp_path / "proposals.csv"
    _write(expected, ["AAAA", "CCCC"])

    with expected.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    MODULE._assert_input_identity(rows, expected_path=expected, source=expected)
