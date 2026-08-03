from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
from pathlib import Path

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
SPECIES = "Escherichia coli"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_sha256(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(
            f"{label} SHA256 mismatch: expected {expected}, observed {observed}"
        )


def read_candidates(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    candidate_ids = [row["candidate_id"] for row in rows]
    if not rows or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("input must contain unique candidate_id rows")
    return rows


def validate_sequence(sequence: str) -> str | None:
    if not sequence or any(residue not in AMINO_ACIDS for residue in sequence):
        return "non-standard or empty peptide sequence"
    if not 5 <= len(sequence) <= 50:
        return "outside released LLAMP 5-50 residue domain"
    return None


def predict(
    sequences: list[str],
    *,
    upstream_repository: Path,
    base_model: Path,
    checkpoint: Path,
    genome_features: Path,
    batch_size: int,
    device_name: str,
) -> list[tuple[float, float]]:
    import torch
    from transformers import EsmTokenizer

    sys.path.insert(0, str(upstream_repository.resolve()))
    from utils.model import LLAMP

    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
    device = torch.device(device_name)
    model = LLAMP(
        hidden_feat=256,
        pooling="CLS",
        pretrained_model=str(base_model.resolve()),
    )
    state = torch.load(checkpoint, map_location="cpu")
    load_result = model.load_state_dict(state, strict=False)
    missing = list(load_result.missing_keys)
    unexpected = set(load_result.unexpected_keys)
    if missing or unexpected - {"bert.embeddings.position_ids"}:
        raise RuntimeError(
            f"checkpoint mismatch: missing={missing!r} unexpected={sorted(unexpected)!r}"
        )
    model.to(device)
    model.eval()

    tokenizer = EsmTokenizer.from_pretrained(str(base_model.resolve()))
    feature_by_species = torch.load(genome_features, map_location="cpu")
    if SPECIES not in feature_by_species:
        raise ValueError(f"{SPECIES} is absent from the locked genome features")
    species_feature = torch.as_tensor(
        feature_by_species[SPECIES][0], dtype=torch.float32
    )

    predictions: list[tuple[float, float]] = []
    with torch.no_grad():
        for offset in range(0, len(sequences), batch_size):
            batch = sequences[offset : offset + batch_size]
            encoded = tokenizer.batch_encode_plus(
                batch,
                add_special_tokens=True,
                padding=True,
                return_tensors="pt",
            )
            genome_batch = species_feature.unsqueeze(0).repeat(len(batch), 1)
            output = model(
                encoded["input_ids"].to(device),
                encoded["attention_mask"].to(device),
                genome_batch.to(device),
            ).reshape(-1)
            for value in output.detach().cpu().numpy().tolist():
                log10_mic = float(value)
                mic = 10.0**log10_mic
                if not math.isfinite(log10_mic) or not math.isfinite(mic) or mic <= 0:
                    raise ValueError("LLAMP returned a non-finite MIC prediction")
                predictions.append((log10_mic, mic))
    if len(predictions) != len(sequences):
        raise RuntimeError("LLAMP prediction count does not match input count")
    return predictions


def evaluate(
    input_path: Path,
    output_path: Path,
    raw_output_dir: Path,
    upstream_repository: Path,
    base_model: Path,
    checkpoint: Path,
    genome_features: Path,
    model_sha256: str,
    config_sha256: str,
    checkpoint_sha256: str,
    genome_features_sha256: str,
    batch_size: int,
    device: str,
) -> None:
    require_sha256(base_model / "pytorch_model.bin", model_sha256, "base model")
    require_sha256(base_model / "config.json", config_sha256, "base model config")
    require_sha256(checkpoint, checkpoint_sha256, "LLAMP checkpoint")
    require_sha256(genome_features, genome_features_sha256, "genome features")
    if not (upstream_repository / "utils" / "model.py").is_file():
        raise FileNotFoundError("locked upstream LLAMP implementation is missing")

    candidates = read_candidates(input_path)
    eligible: list[tuple[str, str]] = []
    errors: dict[str, str] = {}
    for row in candidates:
        candidate_id = row["candidate_id"]
        sequence = row["sequence"].strip().upper()
        error = validate_sequence(sequence)
        if error:
            errors[candidate_id] = error
        else:
            eligible.append((candidate_id, sequence))

    predicted: dict[str, tuple[float, float]] = {}
    if eligible:
        values = predict(
            [sequence for _, sequence in eligible],
            upstream_repository=upstream_repository,
            base_model=base_model,
            checkpoint=checkpoint,
            genome_features=genome_features,
            batch_size=batch_size,
            device_name=device,
        )
        predicted = {
            candidate_id: values[index]
            for index, (candidate_id, _) in enumerate(eligible)
        }

    fields = [
        "candidate_id",
        "sequence",
        "status",
        "llamp_log10_mic_um",
        "llamp_predicted_mic_um",
        "species",
        "error",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in candidates:
            candidate_id = row["candidate_id"]
            sequence = row["sequence"].strip().upper()
            if candidate_id in predicted:
                log10_mic, mic = predicted[candidate_id]
                writer.writerow(
                    {
                        "candidate_id": candidate_id,
                        "sequence": sequence,
                        "status": "success",
                        "llamp_log10_mic_um": log10_mic,
                        "llamp_predicted_mic_um": mic,
                        "species": SPECIES,
                        "error": "",
                    }
                )
            else:
                writer.writerow(
                    {
                        "candidate_id": candidate_id,
                        "sequence": sequence,
                        "status": "out_of_domain",
                        "species": SPECIES,
                        "error": errors[candidate_id],
                    }
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output-dir", type=Path, required=True)
    parser.add_argument("--upstream-repository", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--genome-features", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--genome-features-sha256", required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    evaluate(
        args.input,
        args.output,
        args.raw_output_dir,
        args.upstream_repository,
        args.base_model,
        args.checkpoint,
        args.genome_features,
        args.model_sha256,
        args.config_sha256,
        args.checkpoint_sha256,
        args.genome_features_sha256,
        args.batch_size,
        args.device,
    )


if __name__ == "__main__":
    main()
