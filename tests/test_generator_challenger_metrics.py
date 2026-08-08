from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from pepagent.generator_benchmark import GeneratorChallengerManifest
from pepagent.generator_challenger_metrics_cli import _validate_frozen_inputs

CONFIG_PATH = (
    Path(__file__).parents[1]
    / "config"
    / "benchmarks"
    / "amp_designer_de_novo_v25.yaml"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[GeneratorChallengerManifest, Path, Path]:
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["seeds"] = [1, 2, 3]
    payload["raw_proposal_budget_per_seed"] = 100
    payload["selected_valid_unique_per_seed"] = 10
    payload["sampling"]["batch_size"] = 10
    payload["sampling"]["batches_per_seed"] = 10
    payload["smoke_validation"]["raw_records_per_repetition"] = 100
    payload["execution_status"] = "ready"
    payload.pop("completion")
    manifest = GeneratorChallengerManifest.model_validate(payload)
    selected_path = tmp_path / "selected.csv"
    with selected_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["candidate_id", "seed", "sequence"])
        writer.writeheader()
        for seed in manifest.seeds:
            for rank in range(10):
                writer.writerow(
                    {
                        "candidate_id": f"amp_designer-{seed}-{rank:03d}",
                        "seed": seed,
                        "sequence": "ACDEFGHIKL",
                    }
                )
    freeze_path = tmp_path / "freeze.json"
    freeze_path.write_text(
        json.dumps(
            {
                "benchmark_id": manifest.benchmark_id,
                "metrics_started": False,
                "selected_candidates": {
                    "size_bytes": selected_path.stat().st_size,
                    "sha256": _sha256(selected_path),
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest, freeze_path, selected_path


def test_metrics_accept_only_hash_bound_frozen_candidates(tmp_path: Path) -> None:
    manifest, freeze_path, selected_path = _fixture(tmp_path)
    _, rows = _validate_frozen_inputs(manifest, freeze_path, selected_path)
    assert len(rows) == 30


def test_metrics_reject_selected_candidate_drift(tmp_path: Path) -> None:
    manifest, freeze_path, selected_path = _fixture(tmp_path)
    selected_path.write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="drifted after freeze"):
        _validate_frozen_inputs(manifest, freeze_path, selected_path)
