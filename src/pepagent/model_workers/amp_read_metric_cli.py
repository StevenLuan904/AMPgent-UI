from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
from pathlib import Path

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
MODEL_WEIGHTS = {
    "cnn": 0.25000594,
    "transformer": 0.25000460,
    "attention": 0.25000825,
    "lstm": 0.24998219,
}


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
    if len(sequence) > 100:
        return "outside released AMP-READ maximum length of 100 residues"
    return None


def _install_attention_pickle_class() -> None:
    """Expose the upstream __main__.AttentionNetwork class required by its pickle."""
    import torch
    from torch import nn

    class PositionalEncoding(nn.Module):
        def __init__(self, length: int, d_model: int = 20, dropout: float = 0):
            super().__init__()
            self.dropout = nn.Dropout(p=dropout)
            pe = torch.zeros(length, d_model)
            position = torch.arange(0, length, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(
                torch.arange(0, d_model, 2).float()
                * (-math.log(10000.0) / d_model)
            )
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, values):
            return values + self.pe

    class AttentionNetwork(nn.Module):
        def __init__(
            self,
            batch_size: int = 128,
            embedding_size: int = 20,
            num_tokens: int = 100,
            num_classes: int = 1,
            num_heads: int = 4,
        ):
            super().__init__()
            self.pe = PositionalEncoding(num_tokens, embedding_size)
            self.batch_size = batch_size
            self.embedding_size = embedding_size
            self.num_tokens = num_tokens
            self.num_classes = num_classes
            self.hidden1 = 20
            self.hidden2 = 60
            self.hidden3 = 20
            self.dropout = 0.2
            self.relu = nn.ReLU()
            self.LN = nn.LayerNorm(normalized_shape=self.hidden1)
            self.fc1 = nn.Linear(self.embedding_size, self.hidden1)
            self.multihead_att = nn.MultiheadAttention(
                embed_dim=self.hidden1,
                num_heads=num_heads,
                batch_first=True,
                dropout=self.dropout,
            )
            self.flatten = nn.Flatten()
            self.fc2 = nn.Linear(self.hidden1 * self.num_tokens, self.hidden2)
            self.fc3 = nn.Linear(self.hidden2, self.hidden3)
            self.new_fc4 = nn.Linear(self.hidden3, self.num_classes)
            self.dropout = nn.Dropout(self.dropout)

        def forward(self, values, mask):
            values = self.fc1(self.pe(values))
            values, _ = self.multihead_att(
                values,
                values,
                values,
                key_padding_mask=mask.to(torch.bool),
            )
            values = self.dropout(self.fc2(self.flatten(values)))
            values = self.relu(values)
            values = self.relu(self.fc3(values))
            return self.new_fc4(self.dropout(values))

    # torch.load resolves the released Attention pickle against __main__.
    vars(sys.modules["__main__"])["PositionalEncoding"] = PositionalEncoding
    vars(sys.modules["__main__"])["AttentionNetwork"] = AttentionNetwork


def predict(
    sequences: list[str],
    *,
    upstream_repository: Path,
    model_paths: dict[str, Path],
    device_name: str,
) -> list[dict[str, float]]:
    import numpy as np
    import torch

    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
    device = torch.device(device_name)
    sys.path.insert(0, str(upstream_repository.resolve()))
    import otherModelsRegression  # noqa: F401

    _install_attention_pickle_class()
    models = {
        name: torch.load(path, map_location="cpu") for name, path in model_paths.items()
    }
    for model in models.values():
        model.to(device)
        for module in model.modules():
            if hasattr(module, "device"):
                module.device = device
        model.eval()

    alphabet = {residue: index for index, residue in enumerate("ACDEFGHIKLMNPQRSTVWY")}
    inputs = torch.zeros((len(sequences), 100, 20), dtype=torch.float32)
    masks = torch.ones((len(sequences), 100), dtype=torch.bool)
    for row_index, sequence in enumerate(sequences):
        for column_index, residue in enumerate(sequence):
            inputs[row_index, column_index, alphabet[residue]] = 1.0
        masks[row_index, : len(sequence)] = False
    inputs = inputs.to(device)
    masks = masks.to(device)

    outputs: dict[str, list[float]] = {}
    with torch.no_grad():
        for name, model in models.items():
            values = model(inputs, masks) if name == "attention" else model(inputs)
            outputs[name] = np.asarray(values.detach().cpu()).reshape(-1).tolist()

    predictions: list[dict[str, float]] = []
    for index in range(len(sequences)):
        row = {name: float(values[index]) for name, values in outputs.items()}
        row["ensemble"] = sum(MODEL_WEIGHTS[name] * row[name] for name in MODEL_WEIGHTS)
        if not all(math.isfinite(value) for value in row.values()):
            raise ValueError("AMP-READ returned a non-finite prediction")
        predictions.append(row)
    return predictions


def evaluate(
    input_path: Path,
    output_path: Path,
    raw_output_dir: Path,
    upstream_repository: Path,
    model_paths: dict[str, Path],
    expected_hashes: dict[str, str],
    source_sha256: str,
    device: str,
) -> None:
    require_sha256(
        upstream_repository / "otherModelsRegression.py",
        source_sha256,
        "AMP-READ model source",
    )
    for name, path in model_paths.items():
        require_sha256(path, expected_hashes[name], f"AMP-READ {name} checkpoint")

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

    predicted: dict[str, dict[str, float]] = {}
    if eligible:
        values = predict(
            [sequence for _, sequence in eligible],
            upstream_repository=upstream_repository,
            model_paths=model_paths,
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
        "amp_read_log10_mic_um",
        "amp_read_predicted_mic_um",
        "amp_read_cnn_log10_mic_um",
        "amp_read_transformer_log10_mic_um",
        "amp_read_attention_log10_mic_um",
        "amp_read_lstm_log10_mic_um",
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
            if candidate_id not in predicted:
                writer.writerow(
                    {
                        "candidate_id": candidate_id,
                        "sequence": sequence,
                        "status": "out_of_domain",
                        "error": errors[candidate_id],
                    }
                )
                continue
            values = predicted[candidate_id]
            writer.writerow(
                {
                    "candidate_id": candidate_id,
                    "sequence": sequence,
                    "status": "success",
                    "amp_read_log10_mic_um": values["ensemble"],
                    "amp_read_predicted_mic_um": 10.0 ** values["ensemble"],
                    "amp_read_cnn_log10_mic_um": values["cnn"],
                    "amp_read_transformer_log10_mic_um": values["transformer"],
                    "amp_read_attention_log10_mic_um": values["attention"],
                    "amp_read_lstm_log10_mic_um": values["lstm"],
                    "error": "",
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output-dir", type=Path, required=True)
    parser.add_argument("--upstream-repository", type=Path, required=True)
    for name in MODEL_WEIGHTS:
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    evaluate(
        args.input,
        args.output,
        args.raw_output_dir,
        args.upstream_repository,
        {name: getattr(args, name) for name in MODEL_WEIGHTS},
        {name: getattr(args, f"{name}_sha256") for name in MODEL_WEIGHTS},
        args.source_sha256,
        args.device,
    )


if __name__ == "__main__":
    main()
