from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from pepagent.provenance.hashing import sha256_file
from pepagent.v37_preregistration import (
    load_v37_preregistration,
    validate_v37_experiment_spec,
)

ROOT = Path(__file__).resolve().parents[1]
V37_0_14 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_14.yaml"
V37_0_15 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_15.yaml"
STRUCTURE_0_14 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_14.yaml"
)
STRUCTURE_0_15 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_15.yaml"
)

IMPLEMENTATION_REVISION = "dfa634a207470c737694d743c45a045f48324367"
WORKER_SOURCE_REVISION = "dfa634a207470c737694d743c45a045f48324367"


def _yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v37_0_15_is_new_identity_with_rosetta_decoy_hash_binding_recovery() -> None:
    manifest = load_v37_preregistration(V37_0_15)

    assert manifest.version == "v37.0.15-rosetta-decoy-hash-binding-recovery"
    assert manifest.formal_run.submitted is False
    assert manifest.formal_run.run_id is None
    assert manifest.formal_run.workflow_id is None
    assert manifest.formal_run.implementation_revision == IMPLEMENTATION_REVISION
    assert manifest.execution["worker_source_revision"] == WORKER_SOURCE_REVISION
    assert validate_v37_experiment_spec(manifest, V37_0_15) == {
        "experiment_spec_path": (
            "../experiments/acea_v37_rapid_champion_structure_v37_0_15.yaml"
        ),
        "experiment_spec_sha256": sha256_file(STRUCTURE_0_15),
        "target_spec_sha256": (
            "7371dbf6a70821bd136a97a4a7e3ce0637f6d935a015dee3c218a7d82874a26d"
        ),
        "boltz_seeds": [20270380, 20270381, 20270382],
        "rosetta_decoys_per_pose": 16,
    }


def test_v37_0_15_preserves_every_v37_0_14_scientific_field() -> None:
    old = deepcopy(_yaml(V37_0_14))
    new = deepcopy(_yaml(V37_0_15))

    old.pop("version")
    new.pop("version")
    old_structure = old["stage_2_structure_confirmation"]
    new_structure = new["stage_2_structure_confirmation"]
    for key in ("experiment_spec_path", "experiment_spec_sha256"):
        old_structure.pop(key)
        new_structure.pop(key)
    old["formal_run"].pop("implementation_revision")
    new["formal_run"].pop("implementation_revision")
    old["execution"].pop("worker_source_revision")
    new["execution"].pop("worker_source_revision")
    assert new == old

    old_spec = _yaml(STRUCTURE_0_14)
    new_spec = _yaml(STRUCTURE_0_15)
    old_spec.pop("version")
    new_spec.pop("version")
    assert new_spec == old_spec


def test_v37_0_14_frozen_files_remain_at_recorded_hashes() -> None:
    assert sha256_file(V37_0_14) == (
        "57b330c341a1fb13f78c1c8a2e70333367d16a8c47baf6a68374492b86735de9"
    )
    assert sha256_file(STRUCTURE_0_14) == (
        "143c6ea1242f896db7299b387911fa99e86db4f444d6581c404ff68b7ed02054"
    )
