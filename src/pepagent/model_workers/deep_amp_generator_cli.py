from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

AA_ALPHABET = "BCDSQKIPTFNGHLRWAVEYM-"


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


def generate(request: dict[str, object], model_dir: Path) -> dict[str, object]:
    import numpy as np
    import tensorflow as tf

    seed = int(request["seed"])
    raw_budget = int(request["raw_proposal_budget"])
    if raw_budget < 1:
        raise ValueError("raw_proposal_budget must be positive")
    tf.keras.utils.set_random_seed(seed)
    rng = np.random.default_rng(seed)
    decoder = tf.keras.models.load_model(model_dir)
    latent = rng.uniform(-5.0, 5.0, size=(raw_budget, 50))
    decoded = decoder.predict(latent, verbose=0)
    indices = np.argmax(decoded, axis=-1)
    records = []
    for raw_rank, row in enumerate(indices, start=1):
        sequence = "".join(AA_ALPHABET[int(index)] for index in row).strip("-")
        records.append({"raw_rank": raw_rank, "sequence": sequence})
    return {
        "generator_id": "deep_amp",
        "seed": seed,
        "raw_proposal_budget": raw_budget,
        "records": records,
        "weights": _weight_manifest(model_dir),
        "adapter_version": "deep-amp-generator-v1-raw-unfiltered",
        "internal_score_filtering_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = generate(request, args.model_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"generator_id": "deep_amp", "records": len(result["records"])}))


if __name__ == "__main__":
    main()
