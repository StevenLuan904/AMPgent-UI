from __future__ import annotations

import argparse
import csv
import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Any

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
SCRIPT_SHA256 = "68fdcf72745cd911d2d50624b619ccf322c08992c31d663c10539c1bd75ea0f6"
WEIGHT_SHA256 = {
    "AMPlify_balanced_model_weights_1.h5": (
        "f9a0bf942a3ea6b01579295c6e4b87125e0a05c5a373de0fb54c5c5eb8280cf2"
    ),
    "AMPlify_balanced_model_weights_2.h5": (
        "f3542737840254e4135ef238413c8dac67cc5ddde9782004d7ad42cac35cf0a5"
    ),
    "AMPlify_balanced_model_weights_3.h5": (
        "b6644c510b57d202fa08330af402e6d2c76735cf577a3d7832a3cb1175de5bd3"
    ),
    "AMPlify_balanced_model_weights_4.h5": (
        "1718d26c4ba00e93ba65329995bdb52f6f449d7e379ba891371436258f82d87a"
    ),
    "AMPlify_balanced_model_weights_5.h5": (
        "cbb8012d0a31c2076591b3e6e0f300e63bae0d485b63a46d4f9dcc84e7b36446"
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_runtime(script: Path, weights_directory: Path) -> None:
    if not script.is_file() or sha256_file(script) != SCRIPT_SHA256:
        raise ValueError("AMPlify entry script is missing or has the wrong SHA256")
    for filename, expected in WEIGHT_SHA256.items():
        weight_path = weights_directory / filename
        if not weight_path.is_file() or sha256_file(weight_path) != expected:
            raise ValueError(f"AMPlify weight is missing or has the wrong SHA256: {filename}")


def validate_sequence(sequence: str) -> str | None:
    if not sequence or any(residue not in AMINO_ACIDS for residue in sequence):
        return "non-standard or empty peptide sequence"
    if not 2 <= len(sequence) <= 200:
        return "outside released AMPlify 2-200 residue domain"
    return None


def parse_tsv(path: Path, expected: dict[str, str]) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        required = {
            "Sequence_ID",
            "Sequence",
            "Probability_score",
            "Prediction",
            *(f"Sub_model_{index}_probability_score" for index in range(1, 6)),
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("AMPlify output columns are incomplete")
        for row in reader:
            candidate_id = row["Sequence_ID"]
            if candidate_id not in expected or candidate_id in parsed:
                raise ValueError(f"unexpected or duplicate AMPlify ID: {candidate_id}")
            sequence = row["Sequence"].strip().upper().rstrip("*")
            if sequence != expected[candidate_id]:
                raise ValueError(f"AMPlify sequence mismatch for {candidate_id}")
            probability = float(row["Probability_score"])
            submodels = [
                float(row[f"Sub_model_{index}_probability_score"])
                for index in range(1, 6)
            ]
            if not 0.0 <= probability <= 1.0 or any(
                not 0.0 <= value <= 1.0 for value in submodels
            ):
                raise ValueError(f"AMPlify probability outside [0,1] for {candidate_id}")
            label = row["Prediction"].strip()
            expected_label = "AMP" if probability > 0.5 else "non-AMP"
            if label != expected_label:
                raise ValueError(f"AMPlify label/threshold mismatch for {candidate_id}")
            parsed[candidate_id] = {
                "candidate_id": candidate_id,
                "sequence": sequence,
                "status": "success",
                "amplify_probability": probability,
                "amplify_label": label,
                "amplify_log_scaled_score": row.get("AMPlify_log_scaled_score", ""),
                **{
                    f"amplify_submodel_{index}_probability": submodels[index - 1]
                    for index in range(1, 6)
                },
                "error": "",
            }
    if parsed.keys() != expected.keys():
        raise ValueError("AMPlify output omitted one or more candidate IDs")
    return parsed


def evaluate(
    input_path: Path,
    output_path: Path,
    raw_output_dir: Path,
    amplify_python: Path,
    amplify_script: Path,
    weights_directory: Path,
) -> None:
    verify_runtime(amplify_script, weights_directory)
    with input_path.open(encoding="utf-8-sig", newline="") as stream:
        candidates = list(csv.DictReader(stream))
    candidate_ids = [row["candidate_id"] for row in candidates]
    if not candidates or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("input must contain unique candidate_id rows")

    eligible: dict[str, str] = {}
    errors: dict[str, str] = {}
    for row in candidates:
        candidate_id = row["candidate_id"]
        sequence = row["sequence"].strip().upper()
        error = validate_sequence(sequence)
        if error:
            errors[candidate_id] = error
        else:
            eligible[candidate_id] = sequence

    parsed: dict[str, dict[str, Any]] = {}
    if eligible:
        run_dir = (raw_output_dir / f"amplify-{uuid.uuid4().hex}").resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
        fasta_path = run_dir / "candidates.fasta"
        with fasta_path.open("w", encoding="utf-8", newline="\n") as stream:
            for candidate_id, sequence in eligible.items():
                stream.write(f">{candidate_id}\n{sequence}\n")
        completed = subprocess.run(
            [
                str(amplify_python),
                str(amplify_script),
                "--model",
                "balanced",
                "--seqs",
                str(fasta_path),
                "--out_dir",
                str(run_dir),
                "--out_format",
                "tsv",
                "--sub_model",
                "on",
                "--attention",
                "off",
            ],
            cwd=amplify_script.parent,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        (run_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (run_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(
                f"AMPlify exited with code {completed.returncode}: "
                f"{completed.stderr[-2000:]}"
            )
        outputs = list(run_dir.glob("AMPlify_balanced_results_*.tsv"))
        if len(outputs) != 1:
            raise RuntimeError(f"expected one AMPlify TSV output, found {len(outputs)}")
        parsed = parse_tsv(outputs[0], eligible)

    fields = [
        "candidate_id",
        "sequence",
        "status",
        "amplify_probability",
        "amplify_label",
        "amplify_log_scaled_score",
        *(f"amplify_submodel_{index}_probability" for index in range(1, 6)),
        "error",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in candidates:
            candidate_id = row["candidate_id"]
            if candidate_id in parsed:
                writer.writerow(parsed[candidate_id])
            else:
                writer.writerow(
                    {
                        "candidate_id": candidate_id,
                        "sequence": row["sequence"].strip().upper(),
                        "status": "out_of_domain",
                        "error": errors[candidate_id],
                    }
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output-dir", type=Path, required=True)
    parser.add_argument("--amplify-python", type=Path, required=True)
    parser.add_argument("--amplify-script", type=Path, required=True)
    parser.add_argument("--weights-directory", type=Path, required=True)
    args = parser.parse_args()
    evaluate(
        args.input,
        args.output,
        args.raw_output_dir,
        args.amplify_python,
        args.amplify_script,
        args.weights_directory,
    )


if __name__ == "__main__":
    main()
