"""Round-safe identity and source-root helpers for successor evidence plans."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import PurePosixPath
from typing import Any


def stable_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def derive_operation_identity(
    *, root_id: str, run_id: str, round_name: str, operation: str, version: str, input_identity: str
) -> tuple[str, str]:
    """Return deterministic UUID5 and idempotency key with round/input separation."""
    material = {
        "root_id": str(root_id),
        "run_id": str(run_id),
        "round": str(round_name),
        "operation": str(operation),
        "version": str(version),
        "input_identity": str(input_identity),
    }
    key = stable_sha(material)
    return str(uuid.uuid5(uuid.UUID(str(root_id)), json.dumps(material, sort_keys=True))), key


def source_root_from_freeze(freeze: dict[str, Any]) -> str:
    root = freeze.get("remote_source_root") or freeze.get("source_artifacts", {}).get("remote_root")
    if not isinstance(root, str) or not root.startswith("/data1/"):
        raise ValueError("freeze must provide an absolute remote source root")
    if "/runs/" not in root or "/postprocess_round" not in root:
        raise ValueError("remote source root must be the run-owned postprocess path")
    return root.rstrip("/")


def frozen_source_paths(
    freeze: dict[str, Any], *, enriched: str, formal: str, amplify: str,
    b_selection: str, b_manifest: str
) -> dict[str, str]:
    root = source_root_from_freeze(freeze)
    names = {
        "enriched": enriched, "formal": formal, "amplify": amplify,
        "B_selection": b_selection, "B_manifest": b_manifest,
    }
    if any("/" in name or "\\" in name or not name for name in names.values()):
        raise ValueError("source filenames must be leaf names; root comes from freeze")
    return {key: str(PurePosixPath(root) / name) for key, name in names.items()}
