"""Materialize PostgreSQL ingester identity receipts into compact MD evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

RELEASE_TO_DIR = {
    "openmm_ff14sb_tip3p_1ns-npt_50ns-nvt_interface-pbc_v2": "interface",
    "ambertools26_mmgbsa_igb5_sparse_v1": "mmgbsa",
}


def _uuid(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"invalid {field}") from exc


def _path_identity(entry: dict[str, Any]) -> tuple[str, str]:
    identities: set[tuple[str, str]] = set()
    files = entry.get("files", [])
    file_entries = files.values() if isinstance(files, dict) else files
    for file_entry in file_entries:
        if not isinstance(file_entry, dict):
            raise ValueError("file identity entry is not an object")
        uri = str(file_entry.get("uri", ""))
        parts = [part for part in uri.replace("\\", "/").split("/") if part]
        try:
            index = parts.index("results")
            target, candidate = parts[index + 1 : index + 3]
        except (ValueError, IndexError) as exc:
            raise ValueError("file identity cannot be located under results") from exc
        _uuid(candidate, "candidate_id in file uri")
        identities.add((target.casefold(), candidate))
    if len(identities) != 1:
        raise ValueError("file identity drift or missing files")
    return next(iter(identities))


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def materialize(state: dict[str, Any], compact_root: Path, source: str) -> dict[str, Any]:
    seen: set[tuple[str, str, str, str]] = set()
    written = 0
    existing = 0
    entries = state.get("ingested", [])
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("ingested entry is not an object")
        run_id = _uuid(entry.get("subject_run_id"), "subject_run_id")
        candidate_id = _uuid(entry.get("candidate_id"), "candidate_id")
        tool_call_id = _uuid(entry.get("tool_call_id"), "tool_call_id")
        release = str(entry.get("model_release_key", ""))
        directory = RELEASE_TO_DIR.get(release)
        if directory is None:
            raise ValueError("unknown model_release_key")
        key = (run_id, candidate_id, release, tool_call_id)
        if key in seen:
            raise ValueError("duplicate ingested identity")
        seen.add(key)
        target, path_candidate = _path_identity(entry)
        if path_candidate != candidate_id:
            raise ValueError("candidate identity drift")
        destination = (
            compact_root / target / candidate_id / "analysis" / directory
            / "postgresql_ingest_receipt.json"
        )
        launch_path = destination.parents[2] / "launch_receipt.json"
        if not launch_path.is_file():
            raise ValueError("candidate launch receipt missing")
        with launch_path.open(encoding="utf-8") as handle:
            launch = json.load(handle)
        launch_run = launch.get("subject_run_id") or launch.get("run_id")
        if (
            _uuid(launch.get("candidate_id"), "launch candidate_id") != candidate_id
            or (launch_run is not None and _uuid(launch_run, "launch run_id") != run_id)
            or str(launch.get("target_key", "")).casefold() != target
        ):
            raise ValueError("launch identity drift")
        count = int(entry.get("inserted_evaluation_count", 0))
        already_complete = bool(entry.get("already_complete"))
        if (count <= 0 and not already_complete) or not str(entry.get("ingested_at", "")):
            raise ValueError("invalid ingester receipt fields")
        if already_complete and count <= 0:
            if not destination.is_file():
                raise ValueError("already-complete receipt is missing locally")
            with destination.open(encoding="utf-8") as handle:
                current = json.load(handle)
            if (
                _uuid(current.get("candidate_id"), "receipt candidate_id") != candidate_id
                or _uuid(current.get("subject_run_id"), "receipt subject_run_id") != run_id
                or _uuid(current.get("tool_call_id"), "receipt tool_call_id") != tool_call_id
                or current.get("model_release_key") != release
            ):
                raise ValueError("receipt identity drift")
            existing += 1
            continue
        payload = {
            "candidate_id": candidate_id,
            "subject_run_id": run_id,
            "model_release_key": release,
            "tool_call_id": tool_call_id,
            "inserted_evaluation_count": count,
            "ingested_at": str(entry["ingested_at"]),
            "source": source,
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            with destination.open(encoding="utf-8") as handle:
                current = json.load(handle)
            if current != payload:
                raise ValueError("receipt payload drift")
            existing += 1
        else:
            _atomic_write(destination, payload)
            written += 1
    return {
        "schema_version": "ampgent.pool-a-md-local-receipt-materialization.1",
        "candidate_entry_count": len(entries),
        "written_receipt_count": written,
        "existing_idempotent_receipt_count": existing,
        "duplicate_or_drift_rejected": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-root", type=Path, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    state = json.load(__import__("sys").stdin)
    print(json.dumps(materialize(state, args.compact_root, args.source), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
