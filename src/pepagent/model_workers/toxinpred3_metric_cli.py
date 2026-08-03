from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import joblib
import numpy as np

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


def load_literal_motifs(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8-sig")
    if "Motifs:" not in text:
        raise ValueError(f"motif file has no Motifs section: {path}")
    motifs: list[str] = []
    for line in text.split("Motifs:", 1)[1].splitlines():
        tokens = line.strip().split()
        if not tokens:
            continue
        if any(token not in AMINO_ACIDS for token in tokens):
            raise ValueError(f"unsupported non-literal motif: {line!r}")
        motifs.append("".join(tokens))
    if not motifs:
        raise ValueError(f"motif file contains no motifs: {path}")
    return tuple(motifs)


def aac_dpc_features(sequence: str) -> list[float]:
    length = len(sequence)
    if length < 2:
        raise ValueError("AAC+DPC requires at least two residues")
    aac = [sequence.count(amino_acid) / length * 100.0 for amino_acid in AMINO_ACIDS]
    denominator = length - 1
    dpc: list[float] = []
    for first in AMINO_ACIDS:
        for second in AMINO_ACIDS:
            pair = first + second
            count = sum(sequence[index : index + 2] == pair for index in range(denominator))
            dpc.append(count / denominator * 100.0)
    return aac + dpc


def _rounded(value: float) -> float:
    return round(float(value) + 0.0, 3)


def evaluate(
    input_path: Path,
    output_path: Path,
    model_path: Path,
    positive_motif_path: Path,
    negative_motif_path: Path,
    threshold: float,
) -> None:
    with input_path.open(encoding="utf-8-sig", newline="") as stream:
        candidates = list(csv.DictReader(stream))
    candidate_ids = [row["candidate_id"] for row in candidates]
    if not candidates or len(set(candidate_ids)) != len(candidates):
        raise ValueError("input must contain unique candidate_id rows")

    normalized: list[tuple[str, str]] = []
    out_of_domain: dict[str, str] = {}
    for row in candidates:
        candidate_id = row["candidate_id"]
        sequence = row["sequence"].strip().upper()
        if not sequence or any(residue not in AMINO_ACIDS for residue in sequence):
            out_of_domain[candidate_id] = "non-standard or empty peptide sequence"
        elif not 4 <= len(sequence) <= 35:
            out_of_domain[candidate_id] = "outside released ToxinPred3 4-35 residue domain"
        else:
            normalized.append((candidate_id, sequence))

    predicted: dict[str, dict[str, Any]] = {}
    if normalized:
        matrix = np.asarray([aac_dpc_features(sequence) for _, sequence in normalized], dtype=float)
        classifier = joblib.load(model_path)
        probabilities = classifier.predict_proba(matrix)[:, -1]
        positive_motifs = load_literal_motifs(positive_motif_path)
        negative_motifs = load_literal_motifs(negative_motif_path)
        for (candidate_id, sequence), probability in zip(
            normalized, probabilities, strict=True
        ):
            ml_score = float(probability)
            positive_match = any(motif in sequence for motif in positive_motifs)
            negative_match = any(motif in sequence for motif in negative_motifs)
            hybrid_unclipped = _rounded(
                ml_score + (0.5 if positive_match else 0.0) + (-0.5 if negative_match else 0.0)
            )
            hybrid_score = min(1.0, max(0.0, hybrid_unclipped))
            predicted[candidate_id] = {
                "candidate_id": candidate_id,
                "sequence": sequence,
                "status": "success",
                "toxinpred3_ml_score": _rounded(ml_score),
                "toxinpred3_hybrid_score": _rounded(hybrid_score),
                "toxinpred3_label": ("Toxin" if hybrid_unclipped > threshold else "Non-Toxin"),
                "toxinpred3_positive_motif_match": str(positive_match).lower(),
                "toxinpred3_negative_motif_match": str(negative_match).lower(),
                "error": "",
            }

    fields = [
        "candidate_id",
        "sequence",
        "status",
        "toxinpred3_ml_score",
        "toxinpred3_hybrid_score",
        "toxinpred3_label",
        "toxinpred3_positive_motif_match",
        "toxinpred3_negative_motif_match",
        "error",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in candidates:
            candidate_id = row["candidate_id"]
            if candidate_id in predicted:
                writer.writerow(predicted[candidate_id])
            else:
                writer.writerow(
                    {
                        "candidate_id": candidate_id,
                        "sequence": row["sequence"].strip().upper(),
                        "status": "out_of_domain",
                        "error": out_of_domain[candidate_id],
                    }
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--positive-motifs", type=Path, required=True)
    parser.add_argument("--negative-motifs", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.38)
    args = parser.parse_args()
    args.raw_output_dir.mkdir(parents=True, exist_ok=True)
    evaluate(
        args.input,
        args.output,
        args.model,
        args.positive_motifs,
        args.negative_motifs,
        args.threshold,
    )


if __name__ == "__main__":
    main()
