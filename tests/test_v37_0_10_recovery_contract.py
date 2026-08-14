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
V37_0_9 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_9.yaml"
V37_0_10 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_10.yaml"
STRUCTURE_0_9 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_9.yaml"
)
STRUCTURE_0_10 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_10.yaml"
)

IMPLEMENTATION_REVISION = "447cb3928c8763681b3043cbc27a1dc83d56828e"
WORKER_SOURCE_REVISION = "6c458612e09d57af5d3bf60ea6454dcb8d49d6a0"


def _yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v37_0_10_is_new_identity_with_lifecycle_sequence_recovery() -> None:
    manifest = load_v37_preregistration(V37_0_10)

    assert manifest.version == "v37.0.10-lifecycle-sequence-concurrency-recovery"
    assert manifest.formal_run.submitted is False
    assert manifest.formal_run.run_id is None
    assert manifest.formal_run.workflow_id is None
    assert manifest.formal_run.implementation_revision == IMPLEMENTATION_REVISION
    assert (
        manifest.execution["worker_source_revision"]
        == WORKER_SOURCE_REVISION
    )
    assert validate_v37_experiment_spec(manifest, V37_0_10) == {
        "experiment_spec_path": (
            "../experiments/acea_v37_rapid_champion_structure_v37_0_10.yaml"
        ),
        "experiment_spec_sha256": sha256_file(STRUCTURE_0_10),
        "target_spec_sha256": (
            "7371dbf6a70821bd136a97a4a7e3ce0637f6d935a015dee3c218a7d82874a26d"
        ),
        "boltz_seeds": [20270380, 20270381, 20270382],
        "rosetta_decoys_per_pose": 16,
    }


def test_v37_0_10_preserves_every_v37_0_9_scientific_field() -> None:
    old = deepcopy(_yaml(V37_0_9))
    new = deepcopy(_yaml(V37_0_10))

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

    old_spec = _yaml(STRUCTURE_0_9)
    new_spec = _yaml(STRUCTURE_0_10)
    old_spec.pop("version")
    new_spec.pop("version")
    assert new_spec == old_spec


def test_v37_0_9_frozen_files_remain_at_recorded_hashes() -> None:
    assert sha256_file(V37_0_9) == (
        "750d63ec69977aa24f417e7d3827e69eb86df67c5a6cd444c851e41e31af66f4"
    )
    assert sha256_file(STRUCTURE_0_9) == (
        "330418085b45d0863e3937439e1a11b8a877b7dc71207a9c99b531471d17b045"
    )
