from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pepagent.generator_iteration import (
    HydrAMPParentOptimizationManifest,
    HydrAMPRawAnalogueRequest,
    validate_raw_analogue_request_against_manifest,
)
from pepagent.model_workers.hydramp_raw_analogue_cli import (
    derive_cell_seed,
    disable_internal_classifiers,
    validate_request_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    REPO_ROOT / "config" / "benchmarks" / "amp_generator_hydramp_analogue_v24.yaml"
)


def _manifest_payload() -> dict[str, object]:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_v24_manifest_is_frozen_and_budgeted() -> None:
    manifest = HydrAMPParentOptimizationManifest.model_validate(_manifest_payload())

    assert manifest.development_raw_budget == 4608
    assert manifest.confirmation_raw_budget == 768
    assert len(manifest.parents) == 12
    assert manifest.development_temperatures == [0.5, 2.0, 5.0]
    assert manifest.generator.internal_amp_classifier_calls_allowed is False
    assert manifest.generator.internal_mic_classifier_calls_allowed is False


def test_v24_manifest_rejects_parent_sha_mismatch() -> None:
    payload = _manifest_payload()
    payload["parents"][0]["sequence_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="sequence SHA-256 mismatch"):
        HydrAMPParentOptimizationManifest.model_validate(payload)


def test_v24_manifest_rejects_pepmlm_as_essential() -> None:
    payload = _manifest_payload()
    payload["essential_metrics"].append("target_specific_delta_nll")

    with pytest.raises(ValidationError, match="cannot be essential"):
        HydrAMPParentOptimizationManifest.model_validate(payload)


def test_v24_manifest_rejects_seen_confirmation_seed() -> None:
    payload = _manifest_payload()
    payload["confirmation_seed"] = payload["development_seeds"][0]

    with pytest.raises(ValidationError, match="held out"):
        HydrAMPParentOptimizationManifest.model_validate(payload)


def test_confirmation_request_requires_one_frozen_temperature() -> None:
    payload = _manifest_payload()
    request = {
        "benchmark_id": payload["benchmark_id"],
        "phase": "confirmation",
        "seed": payload["confirmation_seed"],
        "temperatures": [0.5, 2.0],
        "raw_proposals_per_cell": payload[
            "raw_proposals_per_parent_temperature_seed"
        ],
        "parents": payload["parents"],
        "amp_condition": 1,
        "mic_condition": 1,
        "cell_seed_derivation": "sha256-v1",
        "internal_amp_classifier_calls_allowed": False,
        "internal_mic_classifier_calls_allowed": False,
    }

    with pytest.raises(ValidationError, match="one frozen temperature"):
        HydrAMPRawAnalogueRequest.model_validate(request)
    with pytest.raises(ValueError, match="one frozen temperature"):
        validate_request_payload(request)


def test_cell_seed_is_stable_and_cell_specific() -> None:
    payload = _manifest_payload()
    parent_sha = payload["parents"][0]["sequence_sha256"]

    first = derive_cell_seed(20260821, parent_sha, 0.5)
    assert first == derive_cell_seed(20260821, parent_sha, 0.5)
    assert first != derive_cell_seed(20260821, parent_sha, 2.0)
    assert first != derive_cell_seed(20260822, parent_sha, 0.5)


def test_internal_classifier_sentinel_fails_closed() -> None:
    class FakeGenerator:
        _amp_classifier = object()
        _mic_classifier = object()

    generator = FakeGenerator()
    disable_internal_classifiers(generator)

    with pytest.raises(RuntimeError, match="internal AMP classifier"):
        generator._amp_classifier.predict([])
    with pytest.raises(RuntimeError, match="internal MIC classifier"):
        generator._mic_classifier.predict([])


def test_legacy_worker_revalidates_parent_sha_and_classifier_flags() -> None:
    payload = _manifest_payload()
    request = {
        "benchmark_id": payload["benchmark_id"],
        "phase": "development",
        "seed": payload["development_seeds"][0],
        "temperatures": payload["development_temperatures"],
        "raw_proposals_per_cell": payload[
            "raw_proposals_per_parent_temperature_seed"
        ],
        "parents": payload["parents"],
        "amp_condition": 1,
        "mic_condition": 1,
        "cell_seed_derivation": "sha256-v1",
        "internal_amp_classifier_calls_allowed": False,
        "internal_mic_classifier_calls_allowed": False,
    }
    assert validate_request_payload(request)["temperatures"] == [0.5, 2.0, 5.0]

    request["parents"][0]["sequence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sequence SHA-256 mismatch"):
        validate_request_payload(request)


def test_platform_rejects_request_budget_or_parent_order_drift() -> None:
    payload = _manifest_payload()
    manifest = HydrAMPParentOptimizationManifest.model_validate(payload)
    request = {
        "benchmark_id": payload["benchmark_id"],
        "phase": "development",
        "seed": payload["development_seeds"][0],
        "temperatures": payload["development_temperatures"],
        "raw_proposals_per_cell": payload[
            "raw_proposals_per_parent_temperature_seed"
        ],
        "parents": payload["parents"],
        "amp_condition": 1,
        "mic_condition": 1,
        "cell_seed_derivation": "sha256-v1",
        "internal_amp_classifier_calls_allowed": False,
        "internal_mic_classifier_calls_allowed": False,
    }
    validated = validate_raw_analogue_request_against_manifest(manifest, request)
    assert validated.seed == 20260821

    drifted_budget = deepcopy(request)
    drifted_budget["raw_proposals_per_cell"] = 65
    with pytest.raises(ValueError, match="raw budget"):
        validate_raw_analogue_request_against_manifest(manifest, drifted_budget)

    reordered = deepcopy(request)
    reordered["parents"] = list(reversed(reordered["parents"]))
    with pytest.raises(ValueError, match="frozen order"):
        validate_raw_analogue_request_against_manifest(manifest, reordered)


def test_v24_manifest_rejects_duplicate_parent_graph() -> None:
    payload = _manifest_payload()
    duplicate = deepcopy(payload["parents"][0])
    duplicate["parent_id"] = payload["parents"][1]["parent_id"]
    payload["parents"][0] = duplicate

    with pytest.raises(ValidationError, match="parent_id values must be unique"):
        HydrAMPParentOptimizationManifest.model_validate(payload)
