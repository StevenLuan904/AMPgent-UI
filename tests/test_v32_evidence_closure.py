from __future__ import annotations

from pathlib import Path

import yaml

from pepagent.provenance.hashing import sha256_json
from pepagent.v32_evidence_closure import load_submitted_manifest

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_multiobjective_evidence_closure_v32.yaml"


def test_closure_contract_is_append_only_and_frozen_for_one_run() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert payload["execution_authorized"] is False
    assert payload["execution_status"] == "completed"
    assert payload["implementation"]["revision"] == (
        "6074fa4585f74c4b5d61685928d667c4167f92bc"
    )
    assert payload["scientific_contract"]["append_only_grandchild_run"] is True
    assert payload["scientific_contract"]["no_v32_parent_backwrite"] is True
    assert payload["scientific_contract"]["no_acceptance_child_backwrite"] is True
    assert payload["formal_closure_run"]["submitted"] is True
    assert payload["formal_closure_run"]["run_id"] == (
        "de9f72ae-e490-408d-9432-c71a75a3d499"
    )
    assert payload["completion"]["database_object_store_only_replay"] is True
    assert payload["completion"]["v32_parent_backwrite"] is False
    assert payload["completion"]["acceptance_child_backwrite"] is False
    assert payload["completion"]["verdict"] == "ready_for_v33_preregistration"


def test_submitted_manifest_is_exactly_recoverable_from_frozen_git_object() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    source = payload["submitted_manifest"]
    manifest = load_submitted_manifest(source["git_commit"], source["repository_path"])
    assert sha256_json(manifest) == source["canonical_json_sha256"]
    assert manifest["execution_status"] == "implementation_complete"
    assert manifest["formal_run"]["submitted"] is False
