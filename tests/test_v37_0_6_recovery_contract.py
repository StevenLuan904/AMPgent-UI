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
V37_0_5 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_5.yaml"
V37_0_6 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_6.yaml"
STRUCTURE_0_5 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_5.yaml"
)
STRUCTURE_0_6 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_6.yaml"
)


def _yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v37_0_6_is_new_identity_with_metric_projection_fix() -> None:
    manifest = load_v37_preregistration(V37_0_6)

    assert manifest.version == "v37.0.6-metric-observation-projection-recovery"
    assert manifest.formal_run.submitted is False
    assert manifest.formal_run.run_id is None
    assert manifest.formal_run.workflow_id is None
    assert manifest.formal_run.implementation_revision == (
        "e5e9d50bcd1d5b63b02cca1d80beca0478ce9376"
    )
    assert validate_v37_experiment_spec(manifest, V37_0_6) == {
        "experiment_spec_path": (
            "../experiments/acea_v37_rapid_champion_structure_v37_0_6.yaml"
        ),
        "experiment_spec_sha256": sha256_file(STRUCTURE_0_6),
        "target_spec_sha256": (
            "7371dbf6a70821bd136a97a4a7e3ce0637f6d935a015dee3c218a7d82874a26d"
        ),
        "boltz_seeds": [20270380, 20270381, 20270382],
        "rosetta_decoys_per_pose": 16,
    }


def test_v37_0_6_preserves_every_v37_0_5_scientific_field() -> None:
    old = deepcopy(_yaml(V37_0_5))
    new = deepcopy(_yaml(V37_0_6))

    old.pop("version")
    new.pop("version")
    old_structure = old["stage_2_structure_confirmation"]
    new_structure = new["stage_2_structure_confirmation"]
    for key in ("experiment_spec_path", "experiment_spec_sha256"):
        old_structure.pop(key)
        new_structure.pop(key)
    old["formal_run"].pop("implementation_revision")
    new["formal_run"].pop("implementation_revision")
    assert new == old

    old_spec = _yaml(STRUCTURE_0_5)
    new_spec = _yaml(STRUCTURE_0_6)
    old_spec.pop("version")
    new_spec.pop("version")
    assert new_spec == old_spec


def test_v37_0_5_frozen_files_remain_at_recorded_hashes() -> None:
    assert sha256_file(V37_0_5) == (
        "39d750ca9f1fe3de45aae4cd763845569b1f2501a4b40c8ffdc4f246758a6854"
    )
    assert sha256_file(STRUCTURE_0_5) == (
        "42757a1ba22d4e8c8c5e73246bc19814e814baf4b3f59b239a0197f31be80017"
    )
