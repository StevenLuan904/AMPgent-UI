from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any

from pepagent.novelty_reference import parse_fasta_records
from pepagent.provenance.hashing import sha256_text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str, *, label: str) -> str:
    observed = _sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, observed {observed}")
    return observed


def _model_file_manifest(model_directory: Path) -> list[dict[str, int | str]]:
    files: list[dict[str, int | str]] = []
    for path in sorted(item for item in model_directory.iterdir() if item.is_file()):
        files.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return files


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze pooled ESM-2 reference embeddings")
    parser.add_argument("--reference-fasta", type=Path, required=True)
    parser.add_argument("--reference-sha256", required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--model-weights-sha256", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--output-npy", type=Path, required=True)
    parser.add_argument("--output-rows", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--device", default="cuda:0")
    return parser.parse_args()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    args = _parse_args()
    if args.batch_size < 1:
        raise ValueError("batch size must be positive")
    if args.output_npy.suffix != ".npy":
        raise ValueError("embedding output must use the .npy suffix")
    reference_sha256 = _require_hash(
        args.reference_fasta, args.reference_sha256, label="reference FASTA"
    )
    weights_sha256 = _require_hash(
        args.model_directory / "model.safetensors",
        args.model_weights_sha256,
        label="ESM-2 safetensors",
    )
    records = parse_fasta_records(args.reference_fasta.read_text(encoding="utf-8"))
    if len({identifier for identifier, _sequence in records}) != len(records):
        raise ValueError("reference FASTA identifiers must be unique")

    import numpy as np
    import torch
    import torch.nn.functional as functional
    import transformers
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_directory, local_files_only=True, trust_remote_code=False
    )
    model = AutoModel.from_pretrained(
        args.model_directory,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        add_pooling_layer=False,
    )
    device = torch.device(args.device)
    model.to(device)
    model.eval()
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = False

    batches: list[np.ndarray] = []
    with torch.inference_mode():
        for start in range(0, len(records), args.batch_size):
            batch = records[start : start + args.batch_size]
            encoded = tokenizer(
                [sequence for _identifier, sequence in batch],
                return_tensors="pt",
                padding=True,
                return_special_tokens_mask=True,
            )
            special_tokens_mask = encoded.pop("special_tokens_mask").to(device)
            encoded = {key: value.to(device) for key, value in encoded.items()}
            hidden = model(**encoded).last_hidden_state
            valid = encoded["attention_mask"].bool() & ~special_tokens_mask.bool()
            pooled = (hidden * valid.unsqueeze(-1)).sum(dim=1) / valid.sum(
                dim=1, keepdim=True
            )
            normalized = functional.normalize(pooled.float(), p=2, dim=1)
            batches.append(normalized.cpu().numpy())

    embeddings = np.concatenate(batches, axis=0).astype(np.float32, copy=False)
    args.output_npy.parent.mkdir(parents=True, exist_ok=True)
    temporary_npy = args.output_npy.with_suffix(".tmp.npy")
    with temporary_npy.open("wb") as handle:
        np.save(handle, embeddings, allow_pickle=False)
    os.replace(temporary_npy, args.output_npy)

    temporary_rows = args.output_rows.with_suffix(args.output_rows.suffix + ".tmp")
    with temporary_rows.open("w", encoding="utf-8", newline="\n") as handle:
        for row_index, (identifier, sequence) in enumerate(records):
            handle.write(
                json.dumps(
                    {
                        "row_index": row_index,
                        "reference_id": identifier,
                        "sequence_sha256": sha256_text(sequence),
                        "sequence_length": len(sequence),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    os.replace(temporary_rows, args.output_rows)

    manifest = {
        "schema_version": "ampgent.esm2-reference-embedding-manifest.1",
        "model": {
            "identity": "facebook/esm2_t6_8M_UR50D",
            "revision": args.model_revision,
            "weights_sha256": weights_sha256,
            "files": _model_file_manifest(args.model_directory),
        },
        "reference": {
            "fasta_sha256": reference_sha256,
            "sequence_count": len(records),
            "candidate_data_used": False,
        },
        "embedding": {
            "pooling": "mean_last_hidden_state_excluding_special_and_padding_tokens",
            "l2_normalized": True,
            "dtype": "float32",
            "shape": list(embeddings.shape),
            "npy_sha256": _sha256_file(args.output_npy),
            "rows_sha256": _sha256_file(args.output_rows),
        },
        "runtime": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "device": str(device),
            "device_name": torch.cuda.get_device_name(device) if device.type == "cuda" else "cpu",
            "batch_size": args.batch_size,
            "tf32_allowed": False,
        },
        "limitations": [
            (
                "The frozen embeddings establish a reference representation, not a calibrated "
                "OOD threshold."
            ),
            "ESM-2 was trained on UniRef50 and training-set overlap remains to be audited.",
            "Embedding novelty cannot rescue an inactive or unsafe candidate.",
        ],
    }
    _write_json_atomic(args.output_manifest, manifest)
    print(json.dumps(manifest, separators=(",", ":")))


if __name__ == "__main__":
    main()
