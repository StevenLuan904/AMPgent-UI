from __future__ import annotations

from pathlib import Path

import yaml

from pepagent.provenance.hashing import sha256_json
from pepagent.v32_evidence_closure import load_submitted_manifest

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_multiobjective_evidence_closure_v32.yaml"


def test_closure_contract_is_append_only_and_initially_unsubmitted() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert payload["execution_authorized"] is False
    assert payload["scientific_contract"]["append_only_grandchild_run"] is True
    assert payload["scientific_contract"]["no_v32_parent_backwrite"] is True
    assert payload["scientific_contract"]["no_acceptance_child_backwrite"] is True
    assert payload["formal_closure_run"]["submitted"] is False


def test_submitted_manifest_is_exactly_recoverable_from_frozen_git_object() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    source = payload["submitted_manifest"]
    manifest = load_submitted_manifest(source["git_commit"], source["repository_path"])
    assert sha256_json(manifest) == source["canonical_json_sha256"]
    assert manifest["execution_status"] == "implementation_complete"
    assert manifest["formal_run"]["submitted"] is False
