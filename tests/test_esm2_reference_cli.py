from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.model_workers.esm2_reference_cli import _model_file_manifest, _require_hash


def test_require_hash_fails_closed_on_model_drift(tmp_path: Path) -> None:
    path = tmp_path / "model.safetensors"
    path.write_bytes(b"model")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _require_hash(path, "0" * 64, label="model")


def test_model_file_manifest_is_sorted_and_content_addressed(tmp_path: Path) -> None:
    (tmp_path / "z.json").write_text("z", encoding="utf-8")
    (tmp_path / "a.json").write_text("a", encoding="utf-8")
    manifest = _model_file_manifest(tmp_path)
    assert [item["name"] for item in manifest] == ["a.json", "z.json"]
    assert all(len(str(item["sha256"])) == 64 for item in manifest)


def test_pinned_reference_embedding_witness_is_reproduced_and_candidate_independent() -> None:
    root = Path(__file__).parents[1]
    witness = json.loads(
        (root / "config/enterprise/esm2_reference_embedding_manifest_v39.json").read_text(
            encoding="utf-8"
        )
    )
    assert witness["status"] == "reference_embedding_frozen_reproduced_calibration_pending"
    assert witness["reference"] == {
        "fasta_sha256": "d1004b1398df723b2e4a044aaab13b6d9628d7fec23042e2cccd88f8534d6787",
        "sequence_count": 17131,
        "candidate_data_used": False,
    }
    assert witness["embedding"]["shape"] == [17131, 320]
    assert witness["embedding"]["npy_sha256"] == (
        "0c9fb13c1654f3ea394aa6b0b1b50602e95e61e6c9d70dcfb6a63910bf8c9f7f"
    )
    assert witness["reproducibility"] == {
        "independent_execution_count": 2,
        "embedding_sha256_match": True,
        "rows_sha256_match": True,
        "runtime_manifest_sha256_match": True,
    }
