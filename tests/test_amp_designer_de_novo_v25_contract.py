from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pepagent.generator_benchmark import GeneratorChallengerManifest

CONFIG_PATH = (
    Path(__file__).parents[1]
    / "config"
    / "benchmarks"
    / "amp_designer_de_novo_v25.yaml"
)
MODEL_MANIFEST_PATH = (
    Path(__file__).parents[1]
    / "config"
    / "models"
    / "manifests"
    / "amp_designer_zenodo_15051980.json"
)
ENVIRONMENT_PATH = (
    Path(__file__).parents[1]
    / "config"
    / "environments"
    / "amp_designer_v25_environment.json"
)


def _payload() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_v25_challenger_is_completed_and_append_only() -> None:
    manifest = GeneratorChallengerManifest.model_validate(_payload())
    assert manifest.execution_status == "completed"
    assert manifest.reference_results_immutable is True
    assert manifest.generator.internal_score_filtering_enabled is False
    assert manifest.sampling.temperature is None
    assert manifest.sampling.top_k == 10
    assert manifest.sampling.top_p == 1.0
    assert manifest.sampling.decode_steps == 34
    assert manifest.sampling.batch_size == 100
    assert manifest.sampling.batches_per_seed == 10
    assert manifest.smoke_validation is not None
    assert manifest.smoke_validation.outputs_identical is True
    assert manifest.completion is not None
    assert manifest.completion.candidate_count == 300
    assert manifest.completion.promoted_to_followup_validation is True


def test_v25_declared_weights_exactly_match_allowlist_and_exclude_regressors() -> None:
    manifest = GeneratorChallengerManifest.model_validate(_payload())
    assert {item.path for item in manifest.generator.weights} == {
        "config.json",
        "pytorch_model.bin",
    }
    assert all("regress" not in path.lower() for path in manifest.weight_download_allowlist)


def test_v25_rejects_internal_regressor_allowlisting() -> None:
    payload = deepcopy(_payload())
    payload["weight_download_allowlist"].append("Ecoli_regress.pth")
    with pytest.raises(ValueError, match="allowlist and denylist must be disjoint"):
        GeneratorChallengerManifest.model_validate(payload)


def test_v25_rejects_undeclared_internal_scoring_artifact() -> None:
    payload = deepcopy(_payload())
    scoring_path = "novel_mic_predictor.pth"
    payload["weight_download_allowlist"].append(scoring_path)
    payload["generator"]["weights"].append(
        {
            "path": scoring_path,
            "size_bytes": 1,
            "upstream_digest": "md5:00000000000000000000000000000000",
        }
    )
    with pytest.raises(ValueError, match="internal scoring artifacts"):
        GeneratorChallengerManifest.model_validate(payload)


def test_v25_rejects_batch_partition_drift() -> None:
    payload = deepcopy(_payload())
    payload["sampling"]["batches_per_seed"] = 9
    with pytest.raises(ValueError, match="batch partition"):
        GeneratorChallengerManifest.model_validate(payload)


def test_v25_ready_status_requires_downloaded_sha256() -> None:
    payload = deepcopy(_payload())
    payload["generator"]["weights"][0].pop("sha256")
    with pytest.raises(ValueError, match="local SHA-256"):
        GeneratorChallengerManifest.model_validate(payload)


def test_v25_keeps_pepmlm_diagnostic_only() -> None:
    payload = deepcopy(_payload())
    payload["metrics"][-1]["role"] = "profile"
    with pytest.raises(ValueError, match="diagnostic-only"):
        GeneratorChallengerManifest.model_validate(payload)


def test_v25_model_manifest_allowlist_matches_benchmark_and_is_ready() -> None:
    payload = _payload()
    model_manifest = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
    weights = model_manifest["weights_record"]
    assert [item["path"] for item in weights["allowlist"]] == payload[
        "weight_download_allowlist"
    ]
    assert [item["path"] for item in weights["denylist"]] == payload[
        "weight_download_denylist"
    ]
    assert all(item["local_sha256"] is not None for item in weights["allowlist"])
    assert model_manifest["execution_gate"]["status"] == "ready"


def test_v25_environment_is_installed_and_cpu_frozen() -> None:
    environment = json.loads(ENVIRONMENT_PATH.read_text(encoding="utf-8"))
    assert environment["status"] == "installed_verified"
    assert environment["device"]["type"] == "cpu"
    assert environment["required_versions"] == {
        "torch": "1.13.1+cpu",
        "transformers": "4.44.0",
        "tokenizers": "0.19.1",
        "numpy": "1.23.5",
    }
    assert environment["determinism_contract"]["fixed_batch_size"] == 100
