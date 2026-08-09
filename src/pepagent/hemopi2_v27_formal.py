from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pepagent.hemopi2_v27_formal_worker import INPUT_ROW_COUNT, OUTPUT_COLUMNS
from pepagent.hemopi2_v27_worker import REQUIRED_ENVIRONMENT


def validate_formal_output(payload: bytes) -> None:
    with io.StringIO(payload.decode("utf-8"), newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS:
            raise ValueError("formal v27 output column contract mismatch")
        rows = list(reader)
    if len(rows) != INPUT_ROW_COUNT:
        raise ValueError("formal v27 output row count mismatch")
    candidate_ids: list[str] = []
    sequence_hashes: list[str] = []
    for index, row in enumerate(rows):
        if hashlib.sha256(row["sequence"].encode()).hexdigest() != row["sequence_sha256"]:
            raise ValueError(f"formal v27 output row {index} sequence SHA mismatch")
        score = float(row["hemopi2_classification_score"])
        label = int(row["hemopi2_classification_label"])
        hc50 = float(row["hemopi2_hc50_um"])
        if not all(math.isfinite(value) for value in (score, hc50)):
            raise ValueError(f"formal v27 output row {index} is non-finite")
        if label not in {0, 1} or not 0.0 <= score <= 1.0 or hc50 < 0.0:
            raise ValueError(f"formal v27 output row {index} is outside its domain")
        if row["hemopi2_hc50_um"] != f"{hc50:.3f}":
            raise ValueError(f"formal v27 output row {index} violates HC50 precision")
        candidate_ids.append(row["candidate_id"])
        sequence_hashes.append(row["sequence_sha256"])
    if len(set(candidate_ids)) != INPUT_ROW_COUNT:
        raise ValueError("formal v27 output candidate IDs are not unique")
    if len(set(sequence_hashes)) != INPUT_ROW_COUNT:
        raise ValueError("formal v27 output sequences are not unique")


def _atomic_write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = root / "config/benchmarks/amp_designer_safety_validation_v27.yaml"
    marker = "\nexecution_status: formal_authorized\n"
    if marker not in f"\n{manifest.read_text(encoding='utf-8')}":
        raise RuntimeError("v27 formal run is not authorized by the current status")

    audit_root = root / "var/generator-iteration/v27-hemopi2-formal"
    attempt_marker = audit_root / "formal-attempt-started.json"
    _atomic_write_new(
        attempt_marker,
        (
            json.dumps(
                {
                    "attempt_count": 1,
                    "started_at": datetime.now(UTC).isoformat(),
                    "status": "started",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode(),
    )

    runtime = root / "var/validator-runtimes/hemopi2-v26-py311/Scripts/python.exe"
    worker = root / "src/pepagent/hemopi2_v27_formal_worker.py"
    environment = os.environ.copy()
    environment.update(REQUIRED_ENVIRONMENT)
    environment["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [str(runtime), str(worker)],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"v27 formal worker failed with exit code {result.returncode}: "
            f"{result.stderr.decode(errors='replace')}"
        )
    if result.stderr:
        raise RuntimeError("v27 formal worker emitted stderr")
    validate_formal_output(result.stdout)
    output_path = root / "reports/amp_designer_safety_validation_v27_20260809.csv"
    _atomic_write_new(output_path, result.stdout)
    output_sha = hashlib.sha256(result.stdout).hexdigest()
    sidecar = {
        "attempt_count": 1,
        "formal_input_sha256": (
            "fac36b6dbbf4c7525ab7982f054c3c3b02632e0760b938b137d719f1a22a7b12"
        ),
        "formal_output_path": output_path.relative_to(root).as_posix(),
        "formal_output_row_count": INPUT_ROW_COUNT,
        "formal_output_sha256": output_sha,
        "network_disabled": True,
        "status": "completed",
    }
    _atomic_write_new(
        audit_root / "formal-result-sidecar.json",
        (json.dumps(sidecar, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    print(f"formal_output_sha256={output_sha}")
    print(f"formal_output_rows={INPUT_ROW_COUNT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
