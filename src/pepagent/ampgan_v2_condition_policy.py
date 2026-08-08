from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

CONDITION_POLICY_VERSION = "ampgan-v2-v24-condition-policy-v1"
LOW_MIC_MAX = 17.9868929
MID_MIC_MAX = 57.5996376


@dataclass(frozen=True)
class FrozenConditionPoolSpec:
    pool_id: str
    expected_count: int
    expected_sha256: str


@dataclass(frozen=True)
class ConditionPoolEntry:
    position: int
    row_sha256: str
    canonical_row: str


@dataclass(frozen=True)
class FrozenSourceFileSpec:
    relative_path: str
    expected_size_bytes: int
    expected_sha256: str


FROZEN_CONDITION_POOLS = {
    item.pool_id: item
    for item in (
        FrozenConditionPoolSpec(
            pool_id="uniform_all",
            expected_count=6558,
            expected_sha256=(
                "55eafc0460677d45fc0402f8f169fb6ae7f8ecb6a6a48c2a146c1d611505cd25"
            ),
        ),
        FrozenConditionPoolSpec(
            pool_id="uniform_short",
            expected_count=4988,
            expected_sha256=(
                "34ac812d0ebe8acce69962b6d232c7a02d3a4d7e08ab127e2ce09ff162eb3ce9"
            ),
        ),
        FrozenConditionPoolSpec(
            pool_id="bacterial_nomammal_short",
            expected_count=1644,
            expected_sha256=(
                "c60d2f7c4cfc069355813f656789731b2d5aac78958aa4ef23746e9c6f811553"
            ),
        ),
        FrozenConditionPoolSpec(
            pool_id="bacterial_nomammal_short_low_mic",
            expected_count=426,
            expected_sha256=(
                "35c2f1be87978d0d68b717f615a941c00b3e09fcb8e8645426f9a3c1de835b93"
            ),
        ),
        FrozenConditionPoolSpec(
            pool_id="bacterial_nomammal_short_mid_mic",
            expected_count=455,
            expected_sha256=(
                "04188f2beefa755e45657023ab92af21e1a60052f6cc12ff7fdabd1ffcd8d5ba"
            ),
        ),
    )
}

FROZEN_CONDITION_SOURCE_FILES = (
    FrozenSourceFileSpec(
        relative_path="../data/dbaasp/clean.csv",
        expected_size_bytes=728902,
        expected_sha256=(
            "71b41e999dc257fb3c2b3ceaa8a744d56f3e3a10b29259c386b865e1a76ef250"
        ),
    ),
    FrozenSourceFileSpec(
        relative_path="../data/avpdb/AVPdb_data.tsv",
        expected_size_bytes=731264,
        expected_sha256=(
            "af33d4f78a973cc7780ff7f429931333adaa6c3817d21aea29e53fc32d908f5c"
        ),
    ),
    FrozenSourceFileSpec(
        relative_path="../data/avpdb/targets_mapping.json",
        expected_size_bytes=1423,
        expected_sha256=(
            "2746f3a2cbf9c10963d21e06767475f61e982c2e34785c06a4ac67140bb2c57c"
        ),
    ),
)


def canonical_condition_row(record: Mapping[str, Any]) -> str:
    """Serialize only the frozen AMPGAN condition-source fields."""
    required = {"id", "sequence", "target_groups", "targets", "mic50", "length"}
    missing = sorted(required.difference(record))
    if missing:
        raise ValueError(f"condition row is missing fields: {', '.join(missing)}")
    groups = record["target_groups"]
    targets = record["targets"]
    if isinstance(groups, str) or not isinstance(groups, Iterable):
        raise ValueError("target_groups must be a non-string iterable")
    if isinstance(targets, str) or not isinstance(targets, Iterable):
        raise ValueError("targets must be a non-string iterable")
    mic50 = float(record["mic50"])
    length = int(record["length"])
    if mic50 < 0:
        raise ValueError("mic50 must be non-negative")
    if length < 1:
        raise ValueError("length must be positive")
    payload = {
        "id": str(record["id"]),
        "length": length,
        "mic50": format(mic50, ".17g"),
        "sequence": str(record["sequence"]),
        "target_groups": sorted(str(item) for item in groups),
        "targets": sorted(str(item) for item in targets),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _in_policy(record: Mapping[str, Any], pool_id: str) -> bool:
    length = int(record["length"])
    mic50 = float(record["mic50"])
    groups = {str(item) for item in record["target_groups"]}
    short = 10 <= length <= 25
    bacterial_nomammal = bool(groups.intersection({"Gram+", "Gram-"})) and not groups.intersection(
        {"Mammalian Cell", "Cancer"}
    )
    if pool_id == "uniform_all":
        return True
    if pool_id == "uniform_short":
        return short
    if pool_id == "bacterial_nomammal_short":
        return bacterial_nomammal and short
    if pool_id == "bacterial_nomammal_short_low_mic":
        return bacterial_nomammal and short and mic50 <= LOW_MIC_MAX
    if pool_id == "bacterial_nomammal_short_mid_mic":
        return bacterial_nomammal and short and LOW_MIC_MAX < mic50 <= MID_MIC_MAX
    raise ValueError(f"unknown AMPGAN condition pool: {pool_id}")


def compute_condition_pool(
    records: list[Mapping[str, Any]], pool_id: str
) -> tuple[list[ConditionPoolEntry], str]:
    if pool_id not in FROZEN_CONDITION_POOLS:
        raise ValueError(f"unknown AMPGAN condition pool: {pool_id}")
    entries = []
    for position, record in enumerate(records):
        canonical = canonical_condition_row(record)
        if _in_policy(record, pool_id):
            entries.append(
                ConditionPoolEntry(
                    position=position,
                    row_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    canonical_row=canonical,
                )
            )
    entries.sort(key=lambda item: (item.row_sha256, item.position))
    canonical_rows = sorted(item.canonical_row for item in entries)
    payload = "\n".join(canonical_rows) + "\n"
    return entries, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_frozen_condition_pools(
    records: list[Mapping[str, Any]],
) -> dict[str, list[ConditionPoolEntry]]:
    """Build and verify every arm so input drift stops all v24 policies."""
    pools = {}
    errors = []
    for pool_id, spec in FROZEN_CONDITION_POOLS.items():
        entries, digest = compute_condition_pool(records, pool_id)
        if len(entries) != spec.expected_count:
            errors.append(
                f"{pool_id} count {len(entries)} != frozen {spec.expected_count}"
            )
        if digest != spec.expected_sha256:
            errors.append(f"{pool_id} sha256 {digest} != frozen {spec.expected_sha256}")
        pools[pool_id] = entries
    if errors:
        raise ValueError("AMPGAN condition pool contract mismatch: " + "; ".join(errors))
    return pools


def validate_condition_policy_request(request: Mapping[str, Any]) -> FrozenConditionPoolSpec:
    if request.get("condition_policy_version") != CONDITION_POLICY_VERSION:
        raise ValueError(
            "condition_policy_version must equal " + CONDITION_POLICY_VERSION
        )
    pool_id = request.get("condition_policy")
    if not isinstance(pool_id, str) or pool_id not in FROZEN_CONDITION_POOLS:
        raise ValueError(f"unknown AMPGAN condition pool: {pool_id}")
    spec = FROZEN_CONDITION_POOLS[pool_id]
    if request.get("condition_pool_expected_count") != spec.expected_count:
        raise ValueError("request condition pool count does not match frozen contract")
    if request.get("condition_pool_expected_sha256") != spec.expected_sha256:
        raise ValueError("request condition pool SHA-256 does not match frozen contract")
    return spec


def validate_condition_source_files(source_dir, specs=None) -> None:
    source_specs = FROZEN_CONDITION_SOURCE_FILES if specs is None else specs
    errors = []
    for spec in source_specs:
        path = (source_dir / spec.relative_path).resolve()
        if not path.is_file():
            errors.append(f"missing {spec.relative_path}")
            continue
        size = path.stat().st_size
        if size != spec.expected_size_bytes:
            errors.append(
                f"{spec.relative_path} size {size} != frozen {spec.expected_size_bytes}"
            )
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != spec.expected_sha256:
            errors.append(
                f"{spec.relative_path} sha256 {digest} != frozen {spec.expected_sha256}"
            )
    if errors:
        raise ValueError("AMPGAN condition source contract mismatch: " + "; ".join(errors))


def validate_full_condition_matrix(matrix: Any, *, expected_rows: int) -> None:
    import numpy as np

    values = np.asarray(matrix)
    if values.shape != (expected_rows, 64):
        raise ValueError(
            f"AMPGAN full condition matrix shape {values.shape} != {(expected_rows, 64)}"
        )
    if not np.isfinite(values).all():
        raise ValueError("AMPGAN full condition matrix contains non-finite values")
    if not np.logical_or(values == 0, values == 1).all():
        raise ValueError("AMPGAN full condition matrix must be binary")
    if not (values[:, :11].sum(axis=1) >= 1).all():
        raise ValueError("AMPGAN condition rows need at least one target group")
    if not (values[:, 11:22].sum(axis=1) >= 1).all():
        raise ValueError("AMPGAN condition rows need at least one target")
    if not (values[:, 22:32].sum(axis=1) == 1).all():
        raise ValueError("AMPGAN condition rows need exactly one MIC bin")
    length_masks = values[:, 32:64]
    expected_masks = np.arange(32)[None, :] < length_masks.sum(axis=1)[:, None]
    if not np.array_equal(length_masks, expected_masks):
        raise ValueError("AMPGAN length condition must be a contiguous prefix mask")


def independent_rngs(seed: int):
    """Return independent condition and latent RNG streams for v24 only."""
    import numpy as np

    condition_seed, latent_seed = np.random.SeedSequence(seed).spawn(2)
    return np.random.default_rng(condition_seed), np.random.default_rng(latent_seed)


def array_sha256(array: Any, *, dtype: str = "<f8") -> str:
    import numpy as np

    canonical = np.ascontiguousarray(array, dtype=np.dtype(dtype))
    shape = json.dumps(list(canonical.shape), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(shape + b"\0" + canonical.tobytes(order="C")).hexdigest()
