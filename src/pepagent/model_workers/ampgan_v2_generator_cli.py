from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pepagent.ampgan_v2_condition_policy import (
    CONDITION_POLICY_VERSION,
    FROZEN_CONDITION_POOLS,
    array_sha256,
    build_frozen_condition_pools,
    independent_rngs,
    validate_condition_policy_request,
    validate_condition_source_files,
    validate_full_condition_matrix,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _weight_manifest(model_dir: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(model_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(item for item in model_dir.rglob("*") if item.is_file())
    ]


def generate(
    request: dict[str, object], source_dir: Path, model_dir: Path
) -> dict[str, object]:
    source_dir = source_dir.resolve()
    model_dir = model_dir.resolve()
    sys.path.insert(0, str(source_dir))
    os.chdir(source_dir)

    condition_policy = request.get("condition_policy")
    condition_pool_spec = None
    if condition_policy is not None:
        condition_pool_spec = validate_condition_policy_request(request)
        validate_condition_source_files(source_dir)

    import avpdb
    import data_utils
    import dbaasp
    import model  # noqa: F401 - registers the SavedModel custom class
    import numpy as np
    import pandas as pd
    import tensorflow as tf

    seed = int(request["seed"])
    raw_budget = int(request["raw_proposal_budget"])
    if raw_budget < 1:
        raise ValueError("raw_proposal_budget must be positive")
    tf.random.set_seed(seed)

    amps = pd.concat([dbaasp.load_data(), avpdb.load_data()], axis=0, join="inner")
    conditions = data_utils.make_condition_vectors(amps)

    condition_provenance: list[dict[str, object]] | None = None
    if condition_policy is None:
        # Preserve the v23 stream and output contract exactly.
        rng = np.random.default_rng(seed)
        sampled_indices = rng.integers(0, len(conditions), size=raw_budget)
        sampled_conditions = conditions[sampled_indices]
        latent = rng.normal(size=(raw_budget, 256))
    else:
        if condition_pool_spec is None:
            raise ValueError("v24 condition pool spec was not validated")
        validate_full_condition_matrix(conditions, expected_rows=len(amps))
        source_records = [
            {
                "id": row["id"],
                "sequence": row["sequence"],
                "target_groups": row["target_groups"],
                "targets": row["targets"],
                "mic50": row["mic50"],
                "length": row["length"],
            }
            for _, row in amps.reset_index(drop=True).iterrows()
        ]
        # This occurs only after the complete 6558-row condition matrix above is built.
        pools = build_frozen_condition_pools(source_records)
        pool = pools[condition_pool_spec.pool_id]
        condition_rng, latent_rng = independent_rngs(seed)
        sampled_pool_offsets = condition_rng.integers(0, len(pool), size=raw_budget)
        sampled_entries = [pool[int(offset)] for offset in sampled_pool_offsets]
        sampled_indices = np.asarray(
            [entry.position for entry in sampled_entries], dtype=np.int64
        )
        sampled_conditions = conditions[sampled_indices]
        latent = latent_rng.normal(size=(raw_budget, 256))
        condition_provenance = []
        for entry, condition, latent_row in zip(  # noqa: B905 - Python 3.8 runtime
            sampled_entries, sampled_conditions, latent
        ):
            source = source_records[entry.position]
            condition_provenance.append(
                {
                    "condition_source_position": entry.position,
                    "condition_source_row_sha256": entry.row_sha256,
                    "condition_vector_sha256": array_sha256(condition),
                    "latent_sha256": array_sha256(latent_row),
                    "condition_target_groups": sorted(
                        str(item) for item in source["target_groups"]
                    ),
                    "condition_targets": sorted(
                        str(item) for item in source["targets"]
                    ),
                    "condition_mic50": float(source["mic50"]),
                    "condition_length": int(source["length"]),
                }
            )

    gan = tf.keras.models.load_model(model_dir)
    generated = gan.generator([latent, sampled_conditions]).numpy()
    sequences = data_utils.decode_sequences(generated, concatenate=False)
    if len(sequences) != raw_budget:
        raise ValueError(
            f"AMPGAN v2 returned {len(sequences)} rows for raw budget {raw_budget}"
        )
    result = {
        "generator_id": "ampgan_v2",
        "seed": seed,
        "raw_proposal_budget": raw_budget,
        "records": [
            {"raw_rank": raw_rank, "sequence": str(sequence)}
            for raw_rank, sequence in enumerate(sequences, start=1)
        ],
        "weights": _weight_manifest(model_dir),
        "adapter_version": "ampgan-v2-generator-v1-positive-conditions-unfiltered",
        "internal_score_filtering_enabled": False,
        "condition_sampling": "uniform_with_replacement_from_released_positive_amp_rows",
    }
    if condition_provenance is not None:
        result["records"] = [
            {**record, **provenance}
            for record, provenance in zip(  # noqa: B905 - Python 3.8 runtime
                result["records"], condition_provenance
            )
        ]
        result.update(
            {
                "adapter_version": "ampgan-v2-generator-v24-condition-policy-v1",
                "condition_policy_version": CONDITION_POLICY_VERSION,
                "condition_policy": condition_policy,
                "condition_pool_count": FROZEN_CONDITION_POOLS[
                    str(condition_policy)
                ].expected_count,
                "condition_pool_sha256": FROZEN_CONDITION_POOLS[
                    str(condition_policy)
                ].expected_sha256,
                "condition_sampling": (
                    "uniform_with_replacement_from_frozen_real_training_row_pool"
                ),
                "rng_contract": (
                    "numpy_seedsequence_spawn_condition_then_latent; "
                    "latent_stream_common_across_policy_arms"
                ),
                "full_condition_matrix_built_before_pool_indexing": True,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    request_path = args.request.resolve()
    output_path = args.output.resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = generate(request, args.source_dir, args.model_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"generator_id": "ampgan_v2", "records": len(result["records"])}))


if __name__ == "__main__":
    main()
