from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from pepagent.hemopi2_v27_worker import (
    network_disabled,
    require_preimport_environment,
)


def _load_input(path: Path, expected_sha256: str) -> list[dict[str, str]]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("AutoResearch challenger input SHA-256 drifted")
    raw = json.loads(payload.decode("utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != (
        "ampgent.autoresearch-challenger-worker-input.1"
    ):
        raise ValueError("AutoResearch challenger input schema drifted")
    rows = raw.get("candidates")
    if not isinstance(rows, list) or not rows:
        raise ValueError("AutoResearch challenger input candidates are empty")
    candidate_ids: set[str] = set()
    sequence_sha256s: set[str] = set()
    normalized: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {
            "candidate_id",
            "sequence",
            "sequence_sha256",
            "target_key",
        }:
            raise ValueError(f"AutoResearch challenger input row {index} schema drifted")
        candidate_id = str(row["candidate_id"])
        sequence = str(row["sequence"])
        sequence_sha256 = str(row["sequence_sha256"])
        target_key = str(row["target_key"])
        if hashlib.sha256(sequence.encode("utf-8")).hexdigest() != sequence_sha256:
            raise ValueError(f"AutoResearch challenger input row {index} sequence drifted")
        if candidate_id in candidate_ids or sequence_sha256 in sequence_sha256s:
            raise ValueError("AutoResearch challenger input identity is not unique")
        candidate_ids.add(candidate_id)
        sequence_sha256s.add(sequence_sha256)
        normalized.append(
            {
                "candidate_id": candidate_id,
                "sequence": sequence,
                "sequence_sha256": sequence_sha256,
                "target_key": target_key,
            }
        )
    return normalized


def _load_inference_runner() -> Callable[..., list[dict[str, object]]]:
    """Load offline numeric dependencies before replacing the socket class.

    The deterministic environment check must happen first.  Scikit-learn lazily
    imports ``ssl`` through joblib; importing it after ``network_disabled`` has
    replaced ``socket.socket`` prevents ``ssl.SSLSocket`` from being defined.
    Preloading these local-only modules does not open a network connection, and
    the actual model inference remains inside the network-disabled context.
    """

    importlib.import_module("ssl")
    importlib.import_module("sklearn.ensemble._forest")
    module = importlib.import_module("pepagent.hemopi2_v27_inference")
    return cast(
        Callable[..., list[dict[str, object]]],
        module.run_v27_predictions,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated HemoPI2 v27 challenger worker")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    args = parser.parse_args()
    require_preimport_environment()
    run_v27_predictions = _load_inference_runner()
    rows = _load_input(args.input.resolve(), args.input_sha256)
    model_root = args.model_root.resolve()
    with network_disabled():
        predictions = run_v27_predictions(
            [row["sequence"] for row in rows],
            model_root,
            model_root / "Data",
            evidence_scope="autoresearch_structure_cohort_challenger_shadow",
        )
    if len(predictions) != len(rows):
        raise ValueError("HemoPI2 challenger output count drifted")
    records = []
    for index, (row, prediction) in enumerate(zip(rows, predictions, strict=True)):
        if (
            prediction["sequence"] != row["sequence"]
            or prediction["sequence_sha256"] != row["sequence_sha256"]
        ):
            raise ValueError(f"HemoPI2 challenger output row {index} drifted")
        records.append({**row, **prediction})
    output = {
        "schema_version": "ampgent.autoresearch-hemopi2-challenger-output.1",
        "records": records,
    }
    sys.stdout.buffer.write(
        (
            json.dumps(
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
