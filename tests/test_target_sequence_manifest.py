from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.target_sequence_manifest import (
    load_target_sequence_manifest,
    sequence_sha256,
)

MANIFEST = (
    Path(__file__).parents[1]
    / "config"
    / "targets"
    / "ampgent_six_target_sequence_manifest_20260824.json"
)


def test_six_target_manifest_is_complete_and_content_addressed() -> None:
    targets = load_target_sequence_manifest(MANIFEST)
    assert [target.target_key for target in targets] == [
        "acea",
        "gyra",
        "pbp2a",
        "vegfa",
        "fgf2",
        "angpt1",
    ]
    assert sum(target.requested_candidate_count for target in targets) == 900
    assert {target.target_key for target in targets if target.is_partial} == {"pbp2a"}
    assert all(sequence_sha256(target.sequence) == target.sequence_sha256 for target in targets)


def test_manifest_rejects_sequence_hash_mismatch(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["targets"][0]["sequence"] = "A" + payload["targets"][0]["sequence"][1:]
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="sequence_sha256"):
        load_target_sequence_manifest(altered)
