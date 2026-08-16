from __future__ import annotations

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.workers.activities import _bind_rosetta_decoy_hashes


def _result() -> dict:
    return {
        "prepacked_input_sha256": "a" * 64,
        "decoys": [
            {
                "index": 1,
                "seed": 20270464,
                "structure": "decoys/decoy_0001.pdb",
                "structure_sha256": "b" * 64,
                "dG_separated": -12.5,
                "interface_score": -8.0,
                "total_score": -101.0,
            }
        ],
    }


def test_bind_rosetta_decoy_hashes_records_exact_identities() -> None:
    result = _result()

    _bind_rosetta_decoy_hashes(result)

    decoy = result["decoys"][0]
    assert decoy["input_sha256"] == "a" * 64
    assert decoy["output_sha256"] == "b" * 64
    assert decoy["score_terms_sha256"] == sha256_json(
        {
            "dG_separated": -12.5,
            "interface_score": -8.0,
            "total_score": -101.0,
        }
    )


def test_bind_rosetta_decoy_hashes_is_idempotent_and_rejects_drift() -> None:
    result = _result()
    _bind_rosetta_decoy_hashes(result)
    _bind_rosetta_decoy_hashes(result)

    result["decoys"][0]["output_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="output_sha256 drifted"):
        _bind_rosetta_decoy_hashes(result)
