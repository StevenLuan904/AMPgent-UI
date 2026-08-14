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
V37_0_4 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37.yaml"
V37_0_5 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_5.yaml"
STRUCTURE_0_4 = ROOT / "config/experiments/acea_v37_rapid_champion_structure.yaml"
STRUCTURE_0_5 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_5.yaml"
)


def _yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v37_0_5_is_new_identity_with_the_projection_fix() -> None:
    manifest = load_v37_preregistration(V37_0_5)

    assert manifest.version == "v37.0.5-attempt-receipt-projection-recovery"
    assert manifest.formal_run.submitted is False
    assert manifest.formal_run.run_id is None
    assert manifest.formal_run.workflow_id is None
    assert manifest.formal_run.implementation_revision == (
        "e5d0171e203b0816aff6f7a9c717324244bb5b45"
    )
    assert validate_v37_experiment_spec(manifest, V37_0_5) == {
        "experiment_spec_path": (
            "../experiments/acea_v37_rapid_champion_structure_v37_0_5.yaml"
        ),
        "experiment_spec_sha256": sha256_file(STRUCTURE_0_5),
        "target_spec_sha256": (
            "7371dbf6a70821bd136a97a4a7e3ce0637f6d935a015dee3c218a7d82874a26d"
        ),
        "boltz_seeds": [20270380, 20270381, 20270382],
        "rosetta_decoys_per_pose": 16,
    }


def test_v37_0_5_changes_only_versioned_recovery_identity() -> None:
    old = deepcopy(_yaml(V37_0_4))
    new = deepcopy(_yaml(V37_0_5))

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

    old_spec = _yaml(STRUCTURE_0_4)
    new_spec = _yaml(STRUCTURE_0_5)
    old_spec.pop("version")
    new_spec.pop("version")
    assert new_spec == old_spec


def test_v37_0_4_frozen_files_remain_at_their_recorded_hashes() -> None:
    assert sha256_file(V37_0_4) == (
        "91c85a044b42a2bbc5de1e165305d9ba5fad9c53cbc8fd835cc2cf5c0767f15a"
    )
    assert sha256_file(STRUCTURE_0_4) == (
        "b8c89fd5d4f255e985fc61f706b1e6ca5c0b5cf1ecc16c123180fa16df63c149"
    )
