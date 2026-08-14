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
V37_0_8 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_8.yaml"
V37_0_9 = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37_0_9.yaml"
STRUCTURE_0_8 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_8.yaml"
)
STRUCTURE_0_9 = (
    ROOT / "config/experiments/acea_v37_rapid_champion_structure_v37_0_9.yaml"
)

IMPLEMENTATION_REVISION = "36b40b7329813f6ebc6ea1963a51555052f0a139"
WORKER_SOURCE_REVISION = "365f2460c08636b6ca596dd6ed481996b27fa04b"


def _yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v37_0_9_is_new_identity_with_temporal_metric_reference_recovery() -> None:
    manifest = load_v37_preregistration(V37_0_9)

    assert manifest.version == "v37.0.9-temporal-metric-result-reference-recovery"
    assert manifest.formal_run.submitted is False
    assert manifest.formal_run.run_id is None
    assert manifest.formal_run.workflow_id is None
    assert manifest.formal_run.implementation_revision == IMPLEMENTATION_REVISION
    assert (
        manifest.execution["worker_source_revision"]
        == WORKER_SOURCE_REVISION
    )
    assert validate_v37_experiment_spec(manifest, V37_0_9) == {
        "experiment_spec_path": (
            "../experiments/acea_v37_rapid_champion_structure_v37_0_9.yaml"
        ),
        "experiment_spec_sha256": sha256_file(STRUCTURE_0_9),
        "target_spec_sha256": (
            "7371dbf6a70821bd136a97a4a7e3ce0637f6d935a015dee3c218a7d82874a26d"
        ),
        "boltz_seeds": [20270380, 20270381, 20270382],
        "rosetta_decoys_per_pose": 16,
    }


def test_v37_0_9_preserves_every_v37_0_8_scientific_field() -> None:
    old = deepcopy(_yaml(V37_0_8))
    new = deepcopy(_yaml(V37_0_9))

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

    old_spec = _yaml(STRUCTURE_0_8)
    new_spec = _yaml(STRUCTURE_0_9)
    old_spec.pop("version")
    new_spec.pop("version")
    assert new_spec == old_spec


def test_v37_0_8_frozen_files_remain_at_recorded_hashes() -> None:
    assert sha256_file(V37_0_8) == (
        "9f939d0b06151390fc7257cbd27eb1fcd4629ac44a57e5bdb8b49593cbc3c7f3"
    )
    assert sha256_file(STRUCTURE_0_8) == (
        "f29a854ad35be090a2b866d33b6a7abeeea09cd275b7917564fff10818ee116b"
    )
