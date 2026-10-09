"""Pure helpers for one formal12 batch and one AMPlify batch ToolCall.

The module deliberately performs no database I/O.  It builds deterministic
registration payloads and validates the real terminal receipt that a caller
may later persist in the scientific run transaction.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

AA_ALPHABET = set("ACDEFGHIKLMNPQRSTVWY")
TERMINAL = {"completed", "failed"}
BATCHES = {
    "formal12": {
        "tool_name": "ampgent.score_all_r116_runner_local",
        "tool_version": "score-all-r116.batch.v1",
    },
    "amplify": {
        "tool_name": "ampgent.amplify",
        "tool_version": "amplify-2.0.1.batch.v1",
    },
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _timestamp(value: Any) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value:
        raise ValueError("terminal lifecycle timestamp must be a non-empty ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("terminal lifecycle timestamp must include timezone")
    return value, parsed


def _validate_candidates(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidate_rows:
        raise ValueError("scorer batch cannot be empty")
    ids = [row.get("candidate_id") for row in candidate_rows]
    sequences = [row.get("sequence") for row in candidate_rows]
    if any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique non-empty strings")
    if any(not isinstance(x, str) or not x or x != x.strip() or x != x.upper() for x in sequences):
        raise ValueError("sequences must be non-empty canonical uppercase strings")
    if any(not set(x) <= AA_ALPHABET for x in sequences):
        raise ValueError("sequence contains a non-canonical amino-acid symbol")
    if len(sequences) != len(set(sequences)):
        raise ValueError("scorer batch must contain unique sequences")
    for row in candidate_rows:
        if not isinstance(row.get("target"), str) or not row["target"]:
            raise ValueError("each scorer row requires target")
    return [dict(row) for row in candidate_rows]


def deterministic_batch_tool_call_id(
    root_id: str, run_id: str, batch_kind: str, round_name: str, attempt: int = 1
) -> str:
    """Return the stable ID for exactly one batch invocation of a kind."""
    if batch_kind not in BATCHES:
        raise ValueError(f"unsupported scorer batch kind: {batch_kind}")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return str(uuid.uuid5(uuid.UUID(root_id), f"{run_id}:{round_name}:scorer:{batch_kind}:attempt{attempt}.v1"))


def build_batch_registration(
    *,
    root_id: str,
    run_id: str,
    campaign_id: str,
    round_name: str,
    batch_kind: str,
    input_path: str,
    source_path: str,
    candidate_rows: list[dict[str, Any]],
    model_release_key: str,
    source_artifact_sha256: str,
    attempt: int = 1,
    model_uri: str | None = None,
    weights_sha256: str | None = None,
    environment_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a queued ToolCall-shaped payload; does not write it."""
    if batch_kind not in BATCHES:
        raise ValueError(f"unsupported scorer batch kind: {batch_kind}")
    if not round_name.startswith("r") or not round_name[1:].isdigit():
        raise ValueError("round_name must be like r136")
    if not input_path or not source_path or not model_release_key:
        raise ValueError("input/source paths and model release are required")
    if len(source_artifact_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_artifact_sha256.lower()):
        raise ValueError("source_artifact_sha256 must be a hexadecimal SHA-256")
    rows = _validate_candidates(candidate_rows)
    spec = BATCHES[batch_kind]
    call_id = deterministic_batch_tool_call_id(root_id, run_id, batch_kind, round_name, attempt)
    input_json = {
        "schema_version": "ampgent.scorer-batch-input.v1",
        "campaign_id": campaign_id,
        "run_id": run_id,
        "round": round_name,
        "batch_kind": batch_kind,
        "attempt": attempt,
        "input_path": input_path,
        "source_path": source_path,
        "source_artifact_sha256": source_artifact_sha256.lower(),
        "model_release_key": model_release_key,
        "model_uri": model_uri,
        "weights_sha256": weights_sha256,
        "environment_sha256": environment_sha256,
        "candidate_ids": [row["candidate_id"] for row in rows],
        "candidate_rows": rows,
        "candidate_count": len(rows),
    }
    parameters = {
        "batch_kind": batch_kind,
        "ordered_candidate_ids": input_json["candidate_ids"],
        "input_path": input_path,
        "source_path": source_path,
        "model_release_key": model_release_key,
        "model_uri": model_uri,
        "weights_sha256": weights_sha256,
        "environment_sha256": environment_sha256,
        "source_artifact_sha256": source_artifact_sha256.lower(),
        "counts_as_scorer_invocation": True,
    }
    return {
        "id": call_id,
        "run_id": run_id,
        "tool_name": spec["tool_name"],
        "tool_version": spec["tool_version"],
        "idempotency_key": _digest({"run_id": run_id, "round": round_name, "batch_kind": batch_kind, "input_sha256": _digest(input_json)}),
        "input_sha256": _digest(input_json),
        "input_json": input_json,
        "parameters_json": parameters,
        "status": "queued",
        "candidate_count": len(rows),
        "counts_as_scorer_invocation": True,
    }


def validate_terminal_receipt(registration: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate actual terminal evidence and return a DB-update-shaped payload."""
    if receipt.get("tool_call_id") != registration["id"]:
        raise ValueError("receipt ToolCall ID mismatch")
    if receipt.get("batch_kind") != registration["input_json"]["batch_kind"]:
        raise ValueError("receipt batch kind mismatch")
    if receipt.get("status") not in TERMINAL:
        raise ValueError("receipt status must be completed or failed")
    if receipt.get("exit_code") is None or isinstance(receipt["exit_code"], bool):
        raise ValueError("receipt requires integer exit_code")
    if not isinstance(receipt["exit_code"], int):
        raise ValueError("exit_code must be integer")
    if receipt["status"] == "completed" and receipt["exit_code"] != 0:
        raise ValueError("completed scorer receipt must have exit_code=0")
    if receipt["status"] == "failed" and receipt["exit_code"] == 0:
        raise ValueError("failed scorer receipt cannot have exit_code=0")
    started, started_dt = _timestamp(receipt.get("started_at"))
    finished, finished_dt = _timestamp(receipt.get("finished_at"))
    if finished_dt < started_dt:
        raise ValueError("finished_at must not precede started_at")
    if receipt.get("request_sha256") != registration["input_sha256"]:
        raise ValueError("receipt request_sha256 does not match registered input_sha256")
    output_sha = receipt.get("output_sha256")
    if receipt["status"] == "completed":
        if not isinstance(output_sha, str) or len(output_sha) != 64 or any(c not in "0123456789abcdef" for c in output_sha.lower()):
            raise ValueError("completed receipt requires output_sha256")
    elif output_sha is None and not receipt.get("terminal_artifact_path") and not receipt.get("stderr_path"):
        raise ValueError("failed receipt without output requires terminal artifact or stderr path")
    ids = receipt.get("candidate_ids")
    if ids != registration["input_json"]["candidate_ids"]:
        raise ValueError("receipt candidate IDs/order drifted")
    return {
        "status": receipt["status"],
        "started_at": started,
        "finished_at": finished,
        "output_sha256": output_sha.lower() if isinstance(output_sha, str) else None,
        "error_json": {"terminal_receipt": dict(receipt)} if receipt["status"] == "failed" else None,
        "parameters_json_patch": {"actual_execution": dict(receipt), "counts_as_scorer_invocation": True},
    }


def validate_start_receipt(registration: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate evidence that a registered batch was actually started.

    This is intentionally separate from terminal validation: a queued record is
    not evidence that a scorer process ran, and callers must provide the real
    process start time and input identity before transitioning it to running.
    """
    if receipt.get("tool_call_id") != registration["id"]:
        raise ValueError("start receipt ToolCall ID mismatch")
    if receipt.get("batch_kind") != registration["input_json"]["batch_kind"]:
        raise ValueError("start receipt batch kind mismatch")
    if receipt.get("status") != "running":
        raise ValueError("start receipt status must be running")
    started, _ = _timestamp(receipt.get("started_at"))
    if receipt.get("request_sha256") != registration["input_sha256"]:
        raise ValueError("start receipt request_sha256 does not match registered input_sha256")
    if receipt.get("candidate_ids") != registration["input_json"]["candidate_ids"]:
        raise ValueError("start receipt candidate IDs/order drifted")
    process_id = receipt.get("pid", receipt.get("process_id"))
    if process_id is not None and (isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0):
        raise ValueError("pid must be a positive integer when supplied")
    return {
        "status": "running",
        "started_at": started,
        "parameters_json_patch": {"actual_start": dict(receipt), "counts_as_scorer_invocation": True},
    }
