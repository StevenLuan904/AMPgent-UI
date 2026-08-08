from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


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
    rng = np.random.default_rng(seed)

    amps = pd.concat([dbaasp.load_data(), avpdb.load_data()], axis=0, join="inner")
    conditions = data_utils.make_condition_vectors(amps)
    sampled_indices = rng.integers(0, len(conditions), size=raw_budget)
    sampled_conditions = conditions[sampled_indices]
    latent = rng.normal(size=(raw_budget, 256))

    gan = tf.keras.models.load_model(model_dir)
    generated = gan.generator([latent, sampled_conditions]).numpy()
    sequences = data_utils.decode_sequences(generated, concatenate=False)
    if len(sequences) != raw_budget:
        raise ValueError(
            f"AMPGAN v2 returned {len(sequences)} rows for raw budget {raw_budget}"
        )
    return {
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
