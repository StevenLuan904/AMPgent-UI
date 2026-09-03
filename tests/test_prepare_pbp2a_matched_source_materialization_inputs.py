from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


def _module():
    path = (
        Path(__file__).parents[1]
        / "analysis"
        / "prepare_pbp2a_matched_source_materialization_inputs.py"
    )
    spec = importlib.util.spec_from_file_location("matched_inputs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_adds_explicit_source_and_preserves_identity(tmp_path):
    scores = tmp_path / "scores.csv"
    challenger = tmp_path / "challenger.csv"
    fields = ["source_arm", "sequence_sha256", "sequence"]
    with scores.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {"source_arm": "PepGLAD", "sequence_sha256": "a" * 64, "sequence": "AAAA"},
                {"source_arm": "PepFlow", "sequence_sha256": "b" * 64, "sequence": "CCCC"},
            ]
        )
    with challenger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sequence_sha256"])
        writer.writeheader()
        writer.writerows([{"sequence_sha256": "a" * 64}, {"sequence_sha256": "b" * 64}])
    _module().run(scores, challenger, tmp_path / "out")
    with (tmp_path / "out" / "pepglad_candidate_scores.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["source"] == "PepGLAD"
    assert rows[0]["sequence_sha256"] == "a" * 64
