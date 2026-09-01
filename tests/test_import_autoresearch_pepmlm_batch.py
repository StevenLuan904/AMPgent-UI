from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_file


def _module():
    path = Path(__file__).resolve().parents[1] / "analysis" / "import_autoresearch_pepmlm_batch.py"
    spec = importlib.util.spec_from_file_location("_import_autoresearch_pepmlm_batch", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    output = tmp_path / "fgf2.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": "pepmlm.target-conditional-generation.1",
                "generated_count": 1,
                "model": "PepMLM-650M",
                "revision": "frozen-revision",
                "candidates": [
                    {
                        "action_id": "fgf2-denovo-0000",
                        "action_kind": "de_novo",
                        "action_seed": 10,
                        "action_sha256": "a" * 64,
                        "conditional_nll": 1.25,
                        "conditional_ppl": 3.49,
                        "expected_improvement_axes": ["sequence_family_novelty"],
                        "protected_axes": ["non_toxin"],
                        "sequence": "KLLKLLKLLK",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "completion.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "ampgent.autoresearch.pepmlm-gap-completion.v1",
                "workload_sha256": "b" * 64,
                "branch_outputs": {
                    "fgf2": {
                        "candidate_count": 1,
                        "unique_sequence_count": 1,
                        "output_sha256": sha256_file(output),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return output, receipt


def test_import_branch_preserves_action_and_target_score(tmp_path: Path) -> None:
    module = _module()
    output, receipt = _fixture(tmp_path)
    rows, evidence = module.import_branch(
        output_json=output,
        completion_receipt=receipt,
        branch_key="fgf2",
        generation=99,
    )
    assert len(rows) == 1
    assert rows[0]["action_id"] == "fgf2-denovo-0000"
    assert rows[0]["pepmlm_conditional_nll"] == "1.25"
    assert rows[0]["historical_exact_replay"] == "unchecked"
    assert evidence["candidate_count"] == 1
    assert evidence["history_check_status"] == "deferred_to_postgresql_materialization_gate"


def test_import_branch_rejects_output_hash_drift(tmp_path: Path) -> None:
    module = _module()
    output, receipt = _fixture(tmp_path)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["candidates"][0]["sequence"] = "RRRRRRRRRR"
    output.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        module.import_branch(
            output_json=output,
            completion_receipt=receipt,
            branch_key="fgf2",
            generation=99,
        )
