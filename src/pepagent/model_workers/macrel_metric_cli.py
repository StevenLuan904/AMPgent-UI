from __future__ import annotations

import argparse
import csv
import gzip
import subprocess
import uuid
from pathlib import Path
from typing import Any


def expected_macrel_sequence(sequence: str) -> tuple[str, str]:
    """Mirror Macrel 1.6.1's source-level N-terminal Met normalization."""
    if sequence.startswith("M"):
        return sequence[1:], "Macrel removed the N-terminal M"
    return sequence, ""


def parse_prediction_rows(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        data_lines = (line for line in stream if line.strip() and not line.startswith("#"))
        return list(csv.DictReader(data_lines, delimiter="\t"))


def _risk(label: str) -> str:
    normalized = label.strip().lower().replace("-", "").replace("_", "")
    if normalized in {"hemo", "hemolytic"}:
        return "high"
    if normalized in {"nonhemo", "nonhemolytic"}:
        return "low"
    return ""


def evaluate(
    input_path: Path,
    output_path: Path,
    raw_output_dir: Path,
    macrel_executable: Path,
) -> None:
    with input_path.open(encoding="utf-8-sig", newline="") as stream:
        candidates = list(csv.DictReader(stream))
    expected = {row["candidate_id"]: row["sequence"].strip().upper() for row in candidates}
    if not expected or len(expected) != len(candidates):
        raise ValueError("input must contain unique candidate_id rows")

    run_dir = raw_output_dir / f"macrel-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=False)
    fasta_path = run_dir / "candidates.fasta"
    with fasta_path.open("w", encoding="utf-8", newline="\n") as stream:
        for candidate_id, sequence in expected.items():
            stream.write(f">{candidate_id}\n{sequence}\n")

    tool_output = run_dir / "output"
    completed = subprocess.run(
        [
            str(macrel_executable),
            "peptides",
            "--fasta",
            str(fasta_path),
            "--output",
            str(tool_output),
            "--keep-negatives",
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Macrel exited with code {completed.returncode}: {completed.stderr[-2000:]}"
        )
    prediction_path = tool_output / "macrel.out.prediction.gz"
    if not prediction_path.is_file():
        raise FileNotFoundError("Macrel prediction archive is missing")

    parsed: dict[str, dict[str, Any]] = {}
    for row in parse_prediction_rows(prediction_path):
        candidate_id = (row.get("Access") or "").strip().split()[0]
        if candidate_id not in expected or candidate_id in parsed:
            raise ValueError(f"unexpected or duplicate Macrel candidate ID: {candidate_id!r}")
        evaluated_sequence, normalization_note = expected_macrel_sequence(expected[candidate_id])
        returned_sequence = (row.get("Sequence") or "").strip().upper()
        if returned_sequence != evaluated_sequence:
            raise ValueError(
                f"Macrel sequence mismatch for {candidate_id}: "
                f"{returned_sequence!r} != {evaluated_sequence!r}"
            )
        amp_probability = float(row["AMP_probability"])
        hemolysis_probability = (
            float(row["Hemolytic_probability"])
            if row.get("Hemolytic_probability")
            else ""
        )
        if not 0.0 <= amp_probability <= 1.0 or (
            hemolysis_probability != ""
            and not 0.0 <= float(hemolysis_probability) <= 1.0
        ):
            raise ValueError(f"Macrel probability outside [0,1] for {candidate_id}")
        parsed[candidate_id] = {
            "candidate_id": candidate_id,
            "sequence": expected[candidate_id],
            "status": "success",
            "macrel_amp_probability": amp_probability,
            "macrel_hemolysis_probability": hemolysis_probability,
            "macrel_risk": _risk(row.get("Hemolytic") or ""),
            "macrel_evaluated_sequence": returned_sequence,
            "macrel_normalization_note": normalization_note,
        }
    if parsed.keys() != expected.keys():
        missing = sorted(expected.keys() - parsed.keys())
        raise ValueError(f"Macrel omitted candidate IDs: {missing}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "sequence",
        "status",
        "macrel_amp_probability",
        "macrel_hemolysis_probability",
        "macrel_risk",
        "macrel_evaluated_sequence",
        "macrel_normalization_note",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(parsed[candidate_id] for candidate_id in expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output-dir", type=Path, required=True)
    parser.add_argument("--macrel-executable", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.input, args.output, args.raw_output_dir, args.macrel_executable)


if __name__ == "__main__":
    main()
