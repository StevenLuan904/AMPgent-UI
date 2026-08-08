from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(
    request: dict[str, object],
    model_path: Path,
    decomposer_path: Path,
    model_archive: Path,
) -> dict[str, object]:
    from amp.inference.inference import HydrAMPGenerator

    seed = int(request["seed"])
    raw_budget = int(request["raw_proposal_budget"])
    if raw_budget < 1:
        raise ValueError("raw_proposal_budget must be positive")
    generator = HydrAMPGenerator(
        model_path=str(model_path),
        decomposer_path=str(decomposer_path),
        softmax=False,
    )
    sequences = generator.unconstrained_generation(
        mode="amp",
        n_target=raw_budget,
        seed=seed,
        filter_out=False,
        properties=False,
        n_attempts=1,
    )
    if len(sequences) != raw_budget:
        raise ValueError(
            f"HydrAMP returned {len(sequences)} rows for raw budget {raw_budget}"
        )
    return {
        "generator_id": "hydramp",
        "seed": seed,
        "raw_proposal_budget": raw_budget,
        "records": [
            {"raw_rank": raw_rank, "sequence": str(sequence)}
            for raw_rank, sequence in enumerate(sequences, start=1)
        ],
        "weights": [
            {
                "path": model_archive.name,
                "size_bytes": model_archive.stat().st_size,
                "sha256": _sha256(model_archive),
            }
        ],
        "adapter_version": "hydramp-generator-v1-raw-unfiltered-nattempts1",
        "internal_score_filtering_enabled": False,
        "generation_condition": "amp",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--decomposer-path", type=Path, required=True)
    parser.add_argument("--model-archive", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = generate(
        request,
        args.model_path,
        args.decomposer_path,
        args.model_archive,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"generator_id": "hydramp", "records": len(result["records"])}))


if __name__ == "__main__":
    main()
