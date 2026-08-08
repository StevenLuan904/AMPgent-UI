from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ADAPTER_VERSION = "hydramp-raw-analogue-v24-no-internal-scoring"
CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


class _ForbiddenClassifier:
    def __init__(self, name: str):
        self.name = name

    def predict(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"HydrAMP internal {self.name} classifier call is forbidden")


def disable_internal_classifiers(generator: Any) -> None:
    generator._amp_classifier = _ForbiddenClassifier("AMP")
    generator._mic_classifier = _ForbiddenClassifier("MIC")


def derive_cell_seed(
    run_seed: int,
    parent_sequence_sha256: str,
    temperature: float,
) -> int:
    payload = (
        f"sha256-v1\n{run_seed}\n{parent_sequence_sha256}\n"
        f"{temperature:.17g}\n"
    ).encode("ascii")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value % (2**31 - 1)


def validate_request_payload(request: dict[str, object]) -> dict[str, object]:
    if request.get("benchmark_id") != "amp_generator_hydramp_analogue_v24":
        raise ValueError("unexpected benchmark_id")
    phase = request.get("phase")
    if phase not in {"development", "confirmation"}:
        raise ValueError("phase must be development or confirmation")
    if not isinstance(request.get("seed"), int):
        raise ValueError("seed must be an integer")
    temperatures = request.get("temperatures")
    if not isinstance(temperatures, list) or not temperatures:
        raise ValueError("temperatures must be a non-empty list")
    normalized_temperatures = [float(value) for value in temperatures]
    if any(not math.isfinite(value) or value <= 0 for value in normalized_temperatures):
        raise ValueError("temperatures must be finite and positive")
    if len(normalized_temperatures) != len(set(normalized_temperatures)):
        raise ValueError("temperatures must be unique")
    if phase == "confirmation" and len(normalized_temperatures) != 1:
        raise ValueError("confirmation request must contain one frozen temperature")
    raw_budget = request.get("raw_proposals_per_cell")
    if not isinstance(raw_budget, int) or isinstance(raw_budget, bool) or raw_budget < 1:
        raise ValueError("raw_proposals_per_cell must be a positive integer")
    if request.get("amp_condition") != 1 or request.get("mic_condition") != 1:
        raise ValueError("HydrAMP v24 condition bits must both equal one")
    if request.get("cell_seed_derivation") != "sha256-v1":
        raise ValueError("unexpected cell seed derivation")
    if request.get("internal_amp_classifier_calls_allowed") is not False:
        raise ValueError("internal AMP classifier calls must be forbidden")
    if request.get("internal_mic_classifier_calls_allowed") is not False:
        raise ValueError("internal MIC classifier calls must be forbidden")
    parents = request.get("parents")
    if not isinstance(parents, list) or not parents:
        raise ValueError("parents must be a non-empty list")
    parent_ids: list[str] = []
    parent_shas: list[str] = []
    for parent in parents:
        if not isinstance(parent, dict):
            raise ValueError("each parent must be an object")
        parent_id = parent.get("parent_id")
        sequence = parent.get("sequence")
        sequence_digest = parent.get("sequence_sha256")
        if not isinstance(parent_id, str) or not parent_id:
            raise ValueError("parent_id must be a non-empty string")
        if not isinstance(sequence, str) or sequence != sequence.strip().upper():
            raise ValueError("parent sequence must already be canonicalized")
        if not 10 <= len(sequence) <= 25:
            raise ValueError("parent sequence length is outside the v24 contract")
        if any(symbol not in CANONICAL_AMINO_ACIDS for symbol in sequence):
            raise ValueError("parent sequence contains a noncanonical amino acid")
        actual_digest = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
        if sequence_digest != actual_digest:
            raise ValueError("parent sequence SHA-256 mismatch")
        parent_ids.append(parent_id)
        parent_shas.append(actual_digest)
    if len(parent_ids) != len(set(parent_ids)):
        raise ValueError("parent IDs must be unique")
    if len(parent_shas) != len(set(parent_shas)):
        raise ValueError("parent sequence SHA-256 values must be unique")
    validated = dict(request)
    validated["temperatures"] = normalized_temperatures
    return validated


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(
    request_payload: dict[str, object],
    model_path: Path,
    decomposer_path: Path,
    model_archive: Path,
) -> dict[str, object]:
    import numpy as np
    from amp.data_utils import sequence as du_sequence
    from amp.inference.inference import HydrAMPGenerator
    from amp.utils.generate_peptides import translate_peptide
    from amp.utils.seed import set_seed

    request = validate_request_payload(request_payload)
    generator = HydrAMPGenerator(
        model_path=str(model_path),
        decomposer_path=str(decomposer_path),
        softmax=False,
    )
    disable_internal_classifiers(generator)

    cells: list[dict[str, object]] = []
    diagnostic_reconstructions: list[dict[str, object]] = []
    parents = request["parents"]
    temperatures = request["temperatures"]
    run_seed = request["seed"]
    raw_proposals_per_cell = request["raw_proposals_per_cell"]
    for parent in parents:
        padded = du_sequence.pad(du_sequence.to_one_hot([parent["sequence"]]))
        sigma = generator.get_sigma(padded)
        encoded_parent = generator._encoder.predict(padded, verbose=0, batch_size=1)

        reconstruction_seed = derive_cell_seed(
            run_seed,
            parent["sequence_sha256"],
            1.0,
        )
        set_seed(reconstruction_seed)
        reconstructed = generator._decoder.predict(
            np.hstack(
                [
                    encoded_parent,
                    np.ones((1, 1), dtype=float),
                    np.ones((1, 1), dtype=float),
                ]
            ),
            verbose=0,
            batch_size=1,
        )
        diagnostic_reconstructions.append(
            {
                "parent_id": parent["parent_id"],
                "parent_sequence_sha256": parent["sequence_sha256"],
                "cell_seed": reconstruction_seed,
                "sequence": translate_peptide(reconstructed.argmax(axis=2)[0]),
                "diagnostic_only": True,
            }
        )

        for temperature in temperatures:
            cell_seed = derive_cell_seed(
                run_seed,
                parent["sequence_sha256"],
                temperature,
            )
            set_seed(cell_seed)
            encoded = np.repeat(
                encoded_parent,
                raw_proposals_per_cell,
                axis=0,
            )
            sigma_repeated = np.repeat(
                sigma,
                raw_proposals_per_cell,
                axis=0,
            )
            noise = np.random.normal(
                loc=0,
                scale=temperature * sigma_repeated,
                size=encoded.shape,
            )
            conditioned = np.hstack(
                [
                    encoded + noise,
                    np.ones((raw_proposals_per_cell, 1), dtype=float),
                    np.ones((raw_proposals_per_cell, 1), dtype=float),
                ]
            )
            decoded = generator._decoder.predict(
                conditioned,
                verbose=0,
                batch_size=raw_proposals_per_cell,
            )
            sequences = [translate_peptide(row) for row in decoded.argmax(axis=2)]
            if len(sequences) != raw_proposals_per_cell:
                raise ValueError("HydrAMP decoder returned an unexpected raw row count")
            cells.append(
                {
                    "parent_id": parent["parent_id"],
                    "parent_sequence_sha256": parent["sequence_sha256"],
                    "temperature": temperature,
                    "run_seed": run_seed,
                    "cell_seed": cell_seed,
                    "raw_proposal_budget": raw_proposals_per_cell,
                    "records": [
                        {"raw_rank": rank, "sequence": sequence}
                        for rank, sequence in enumerate(sequences, start=1)
                    ],
                }
            )

    expected_raw_count = (
        len(parents)
        * len(temperatures)
        * raw_proposals_per_cell
    )
    actual_raw_count = sum(len(cell["records"]) for cell in cells)
    if actual_raw_count != expected_raw_count:
        raise ValueError("HydrAMP raw analogue output budget mismatch")
    return {
        "benchmark_id": request["benchmark_id"],
        "generator_id": "hydramp",
        "generation_mode": "parent_optimization",
        "phase": request["phase"],
        "seed": run_seed,
        "temperatures": temperatures,
        "raw_proposals_per_cell": raw_proposals_per_cell,
        "raw_count": actual_raw_count,
        "cells": cells,
        "diagnostic_reconstructions": diagnostic_reconstructions,
        "weights": [
            {
                "path": model_archive.name,
                "size_bytes": model_archive.stat().st_size,
                "sha256": _sha256(model_archive),
            }
        ],
        "adapter_version": ADAPTER_VERSION,
        "amp_condition": 1,
        "mic_condition": 1,
        "internal_amp_classifier_calls": 0,
        "internal_mic_classifier_calls": 0,
        "internal_score_filtering_enabled": False,
        "selection_performed": False,
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
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "generator_id": "hydramp",
                "phase": result["phase"],
                "raw_count": result["raw_count"],
            }
        )
    )


if __name__ == "__main__":
    main()
