from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from pepagent.ampgan_v2_condition_policy import (
    CONDITION_POLICY_VERSION,
    FROZEN_CONDITION_POOLS,
    FROZEN_CONDITION_SOURCE_FILES,
    FrozenSourceFileSpec,
    array_sha256,
    canonical_condition_row,
    compute_condition_pool,
    independent_rngs,
    validate_condition_policy_request,
    validate_condition_source_files,
    validate_full_condition_matrix,
)


def _row(
    row_id: str,
    *,
    groups: list[str],
    mic50: float,
    length: int,
) -> dict[str, object]:
    return {
        "id": row_id,
        "sequence": "ACDEFGHIKL",
        "target_groups": groups,
        "targets": ["Lipid Bilayer"],
        "mic50": mic50,
        "length": length,
    }


def test_condition_pool_filters_use_real_row_metadata_and_frozen_boundaries() -> None:
    records = [
        _row("low", groups=["Gram-"], mic50=17.9868929, length=10),
        _row("mid", groups=["Gram+"], mic50=57.5996376, length=25),
        _row(
            "mammal",
            groups=["Gram-", "Mammalian Cell"],
            mic50=1.0,
            length=20,
        ),
        _row("long", groups=["Gram-"], mic50=1.0, length=26),
    ]

    low, _ = compute_condition_pool(records, "bacterial_nomammal_short_low_mic")
    mid, _ = compute_condition_pool(records, "bacterial_nomammal_short_mid_mic")

    assert [json.loads(item.canonical_row)["id"] for item in low] == ["low"]
    assert [json.loads(item.canonical_row)["id"] for item in mid] == ["mid"]


def test_pool_digest_is_canonical_and_independent_of_input_order() -> None:
    records = [
        _row("a", groups=["Gram-", "Gram+"], mic50=2.0, length=12),
        _row("b", groups=["Gram+"], mic50=20.0, length=16),
    ]
    _, first = compute_condition_pool(records, "uniform_all")
    _, second = compute_condition_pool(list(reversed(records)), "uniform_all")
    assert first == second
    canonical = canonical_condition_row(records[0])
    assert len(hashlib.sha256(canonical.encode("utf-8")).hexdigest()) == 64


def test_request_must_repeat_exact_frozen_pool_contract() -> None:
    spec = FROZEN_CONDITION_POOLS["uniform_short"]
    request = {
        "condition_policy_version": CONDITION_POLICY_VERSION,
        "condition_policy": spec.pool_id,
        "condition_pool_expected_count": spec.expected_count,
        "condition_pool_expected_sha256": spec.expected_sha256,
    }
    assert validate_condition_policy_request(request) == spec
    request["condition_pool_expected_count"] = spec.expected_count + 1
    with pytest.raises(ValueError, match="count"):
        validate_condition_policy_request(request)


def test_v24_rng_streams_are_independent_and_latent_stream_is_arm_invariant() -> None:
    condition_rng_a, latent_rng_a = independent_rngs(20260821)
    condition_rng_b, latent_rng_b = independent_rngs(20260821)
    conditions = condition_rng_a.integers(0, 100, size=8)
    latent = latent_rng_a.normal(size=(8, 4))
    assert not np.array_equal(conditions, latent[:, 0].astype(int))
    assert np.array_equal(latent, latent_rng_b.normal(size=(8, 4)))
    assert np.array_equal(conditions, condition_rng_b.integers(0, 100, size=8))


def test_array_provenance_hash_includes_shape() -> None:
    assert array_sha256(np.asarray([[1.0, 2.0]])) != array_sha256(
        np.asarray([1.0, 2.0])
    )


def test_condition_source_file_contract_fails_closed_on_tamper(tmp_path: Path) -> None:
    source_dir = tmp_path / "ampgan"
    source_dir.mkdir()
    source_file = tmp_path / "data.csv"
    source_file.write_bytes(b"frozen")
    spec = FrozenSourceFileSpec(
        relative_path="../data.csv",
        expected_size_bytes=6,
        expected_sha256=hashlib.sha256(b"frozen").hexdigest(),
    )
    validate_condition_source_files(source_dir, specs=(spec,))
    source_file.write_bytes(b"tamper")
    with pytest.raises(ValueError, match="source contract mismatch"):
        validate_condition_source_files(source_dir, specs=(spec,))


def test_full_condition_matrix_fails_closed_on_shape_or_encoding() -> None:
    valid = np.zeros((1, 64), dtype=float)
    valid[0, 0] = 1
    valid[0, 11] = 1
    valid[0, 22] = 1
    valid[0, 32:42] = 1
    validate_full_condition_matrix(valid, expected_rows=1)
    with pytest.raises(ValueError, match="shape"):
        validate_full_condition_matrix(valid[:, :-1], expected_rows=1)
    tampered = valid.copy()
    tampered[0, 5] = 0.5
    with pytest.raises(ValueError, match="binary"):
        validate_full_condition_matrix(tampered, expected_rows=1)


def test_v24_manifest_matches_runtime_frozen_pool_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load(
        (root / "config/benchmarks/ampgan_v2_condition_policy_v24.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["condition_policy_version"] == CONDITION_POLICY_VERSION
    assert manifest["full_condition_matrix_built_before_pool_indexing"] is True
    assert manifest["internal_score_filtering_enabled"] is False
    manifest_pools = {
        item["pool_id"]: (item["expected_count"], item["expected_sha256"])
        for item in manifest["condition_pools"]
    }
    runtime_pools = {
        pool_id: (spec.expected_count, spec.expected_sha256)
        for pool_id, spec in FROZEN_CONDITION_POOLS.items()
    }
    assert manifest_pools == runtime_pools
    manifest_sources = {
        "../" + item["path"]: (item["size_bytes"], item["sha256"])
        for item in manifest["condition_source_inputs"]
    }
    runtime_sources = {
        item.relative_path: (item.expected_size_bytes, item.expected_sha256)
        for item in FROZEN_CONDITION_SOURCE_FILES
    }
    assert manifest_sources == runtime_sources
    assert manifest["scientific_contract"][
        "no_generator_internal_amp_or_mic_score_filtering"
    ]
