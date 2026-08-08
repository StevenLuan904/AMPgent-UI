from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from pepagent.generator_benchmark import (
    CANONICAL_AMINO_ACIDS,
    GeneratorChallengerManifest,
)
from pepagent.generator_challenger_freeze_cli import freeze_raw_cohorts

CONFIG_PATH = (
    Path(__file__).parents[1]
    / "config"
    / "benchmarks"
    / "amp_designer_de_novo_v25.yaml"
)


def _sequence(index: int) -> str:
    alphabet = CANONICAL_AMINO_ACIDS
    return "".join(
        alphabet[(index // (len(alphabet) ** power)) % len(alphabet)]
        for power in range(10)
    )


def _write_raw_files(
    tmp_path: Path,
    *,
    internal_regressors_loaded: bool = False,
) -> GeneratorChallengerManifest:
    manifest = GeneratorChallengerManifest.model_validate(
        yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    )
    artifacts = [
        {
            "path": item.path,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        for item in manifest.generator.weights
    ]
    artifacts.append({"path": "voc/vocab.txt", "size_bytes": 70, "sha256": "a" * 64})
    sampling = {
        "top_k": 10,
        "top_p": 1.0,
        "temperature": None,
        "decode_steps": 34,
        "learned_prompt_tokens": 10,
        "batch_size": 100,
        "batches": 10,
    }
    for seed in manifest.seeds:
        raw = {
            "generator_id": "amp_designer",
            "seed": seed,
            "raw_proposal_budget": 1000,
            "records": [
                {"raw_rank": index + 1, "sequence": _sequence(index + seed)}
                for index in range(1000)
            ],
            "artifacts": artifacts,
            "sampling": sampling,
            "adapter_version": manifest.generator.adapter_version,
            "internal_score_filtering_enabled": False,
            "internal_regressors_loaded": internal_regressors_loaded,
        }
        (tmp_path / f"amp_designer_{seed}.json").write_text(
            json.dumps(raw),
            encoding="utf-8",
        )
    return manifest


def test_freeze_challenger_requires_all_raw_before_metrics(tmp_path: Path) -> None:
    manifest = _write_raw_files(tmp_path)
    result = freeze_raw_cohorts(manifest, tmp_path)
    freeze = result["freeze_manifest"]
    assert freeze["raw_records"] == 3000
    assert freeze["selected_occurrences"] == 300
    assert freeze["raw_outputs_frozen_before_metrics"] is True
    assert freeze["metrics_started"] is False


def test_freeze_challenger_rejects_internal_regressor_flag(tmp_path: Path) -> None:
    manifest = _write_raw_files(tmp_path, internal_regressors_loaded=True)
    with pytest.raises(ValueError, match="internal regressor flag"):
        freeze_raw_cohorts(manifest, tmp_path)
