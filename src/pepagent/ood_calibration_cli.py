from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pepagent.novelty_reference import parse_fasta_records


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str, *, label: str) -> None:
    observed = _sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, observed {observed}")


def _nearest_cosine(
    query: Any, reference: Any, *, chunk_size: int, exclude_self: bool
) -> Any:
    import torch

    maxima = []
    for start in range(0, query.shape[0], chunk_size):
        stop = min(query.shape[0], start + chunk_size)
        similarities = query[start:stop] @ reference.T
        if exclude_self:
            row = torch.arange(stop - start, device=query.device)
            similarities[row, row + start] = -torch.inf
        maxima.append(similarities.max(dim=1).values.cpu())
    return torch.cat(maxima).numpy()


def _quantiles(values: Any) -> dict[str, float]:
    import numpy as np

    return {
        label: float(np.quantile(values, quantile))
        for label, quantile in (("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99))
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate ESM-2 novelty distance on a holdout")
    parser.add_argument("--reference-npy", type=Path, required=True)
    parser.add_argument("--reference-npy-sha256", required=True)
    parser.add_argument("--reference-fasta", type=Path, required=True)
    parser.add_argument("--holdout-npy", type=Path, required=True)
    parser.add_argument("--holdout-npy-sha256", required=True)
    parser.add_argument("--holdout-fasta", type=Path, required=True)
    parser.add_argument("--output-scores", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--chunk-size", type=int, default=1024)
    return parser.parse_args()


def main() -> None:
    import numpy as np
    import torch

    args = _parse_args()
    if args.chunk_size < 1:
        raise ValueError("chunk size must be positive")
    _require_hash(args.reference_npy, args.reference_npy_sha256, label="reference embeddings")
    _require_hash(args.holdout_npy, args.holdout_npy_sha256, label="holdout embeddings")
    reference_np = np.load(args.reference_npy, allow_pickle=False)
    holdout_np = np.load(args.holdout_npy, allow_pickle=False)
    if reference_np.ndim != 2 or holdout_np.ndim != 2:
        raise ValueError("embedding arrays must be matrices")
    if reference_np.shape[1] != holdout_np.shape[1]:
        raise ValueError("reference and holdout embedding dimensions differ")
    reference_records = parse_fasta_records(args.reference_fasta.read_text(encoding="utf-8"))
    holdout_records = parse_fasta_records(args.holdout_fasta.read_text(encoding="utf-8"))
    if (
        len(reference_records) != reference_np.shape[0]
        or len(holdout_records) != holdout_np.shape[0]
    ):
        raise ValueError("FASTA and embedding row counts differ")

    device = torch.device(args.device)
    reference = torch.from_numpy(reference_np).to(device)
    holdout = torch.from_numpy(holdout_np).to(device)
    reference_similarity = _nearest_cosine(
        reference, reference, chunk_size=args.chunk_size, exclude_self=True
    )
    holdout_similarity = _nearest_cosine(
        holdout, reference, chunk_size=args.chunk_size, exclude_self=False
    )
    reference_distance = 1.0 - reference_similarity
    holdout_distance = 1.0 - holdout_similarity
    reference_sequences = {sequence for _identifier, sequence in reference_records}
    overlap = np.asarray(
        [sequence in reference_sequences for _identifier, sequence in holdout_records],
        dtype=np.bool_,
    )
    clean_holdout_distance = holdout_distance[~overlap]
    threshold = float(np.quantile(reference_distance, 0.95))

    args.output_scores.parent.mkdir(parents=True, exist_ok=True)
    temporary_scores = args.output_scores.with_suffix(".tmp.npz")
    np.savez_compressed(
        temporary_scores,
        reference_nearest_cosine=reference_similarity,
        holdout_nearest_cosine=holdout_similarity,
        holdout_exact_reference_overlap=overlap,
    )
    os.replace(temporary_scores, args.output_scores)
    manifest = {
        "schema_version": "ampgent.esm2-external-holdout-calibration.1",
        "status": "diagnostic_completed_training_overlap_pending",
        "candidate_data_used": False,
        "reference": {
            "count": int(reference_np.shape[0]),
            "embedding_sha256": args.reference_npy_sha256,
        },
        "holdout": {
            "count": int(holdout_np.shape[0]),
            "embedding_sha256": args.holdout_npy_sha256,
            "exact_reference_overlap_count": int(overlap.sum()),
            "clean_count": int((~overlap).sum()),
        },
        "distance": {
            "definition": "one_minus_maximum_cosine_similarity_to_frozen_dramp_reference",
            "reference_leave_one_out_quantiles": _quantiles(reference_distance),
            "clean_holdout_quantiles": _quantiles(clean_holdout_distance),
        },
        "diagnostic_operating_point": {
            "rule": "reference_leave_one_out_distance_p95",
            "distance_threshold": threshold,
            "expected_reference_false_ood_rate": float((reference_distance > threshold).mean()),
            "clean_holdout_flag_rate": float((clean_holdout_distance > threshold).mean()),
            "formal_ood_label_allowed": False,
        },
        "scores": {
            "path": str(args.output_scores),
            "sha256": _sha256_file(args.output_scores),
        },
        "runtime": {
            "device": str(device),
            "device_name": torch.cuda.get_device_name(device) if device.type == "cuda" else "cpu",
            "torch": torch.__version__,
            "chunk_size": args.chunk_size,
        },
        "limitations": [
            "The non-antimicrobial keyword exclusion is an imperfect negative label.",
            "ESM-2 UniRef50 training overlap remains unaudited.",
            "The operating point is diagnostic and must not hard-filter candidates yet.",
        ],
    }
    temporary_manifest = args.output_manifest.with_suffix(args.output_manifest.suffix + ".tmp")
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, args.output_manifest)
    print(json.dumps(manifest, separators=(",", ":")))


if __name__ == "__main__":
    main()
