from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from pepagent.autoresearch_instability_restoration import (
    build_restoration_manifest,
    canonical_manifest_bytes,
    persist_restoration,
    validate_restoration_manifest,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes


def _parse_cutoff(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("snapshot cutoff must include a timezone")
    return parsed.astimezone(UTC)


async def _database_now() -> datetime:
    async with SessionFactory() as session:
        value = await session.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime):
        raise RuntimeError("PostgreSQL did not return a snapshot timestamp")
    return value


async def _dry_run(
    output: Path,
    cutoff: datetime | None,
    *,
    incremental: bool,
    prior_manifest_sha256: str | None,
    prior_snapshot_cutoff: datetime | None,
) -> dict[str, object]:
    if await asyncio.to_thread(output.exists):
        raise FileExistsError("dry-run manifest output is append-only")
    snapshot_cutoff = cutoff or await _database_now()
    async with SessionFactory() as session:
        manifest = await build_restoration_manifest(
            session,
            snapshot_cutoff=snapshot_cutoff,
            incremental=incremental,
            prior_manifest_sha256=prior_manifest_sha256,
            prior_snapshot_cutoff=prior_snapshot_cutoff,
        )
    payload = canonical_manifest_bytes(manifest)
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_bytes, payload)
    resolved_output = await asyncio.to_thread(output.resolve)
    return {
        "status": "inert",
        "executed": False,
        "manifest_path": str(resolved_output),
        "manifest_sha256": sha256_bytes(payload),
        "manifest_size_bytes": len(payload),
        "snapshot_cutoff": manifest["snapshot_cutoff"],
        "summary": manifest["summary"],
        "target_summary": manifest["target_summary"],
    }


async def _execute(path: Path) -> dict[str, object]:
    resolved_path = await asyncio.to_thread(path.resolve, strict=True)
    payload = await asyncio.to_thread(resolved_path.read_bytes)
    manifest = json.loads(payload)
    validate_restoration_manifest(manifest)
    if canonical_manifest_bytes(manifest) != payload:
        raise ValueError("manifest is not in canonical frozen form")
    cutoff = _parse_cutoff(str(manifest["snapshot_cutoff"]))
    scope = manifest.get("restoration_scope", {"mode": "full_snapshot"})
    incremental = scope.get("mode") == "incremental_unrestored_only"
    prior_cutoff = (
        _parse_cutoff(str(scope["prior_snapshot_cutoff"]))
        if incremental
        else None
    )
    async with SessionFactory() as session:
        replay = await build_restoration_manifest(
            session,
            snapshot_cutoff=cutoff,
            incremental=incremental,
            prior_manifest_sha256=(
                str(scope["prior_manifest_sha256"]) if incremental else None
            ),
            prior_snapshot_cutoff=prior_cutoff,
        )
    replay_payload = canonical_manifest_bytes(replay)
    if replay_payload != payload:
        raise ValueError("PostgreSQL cutoff replay differs from the inert manifest")
    result = await persist_restoration(
        manifest=manifest,
        manifest_bytes=payload,
        session_factory=SessionFactory,
    )
    return {**result, "executed": True}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore old Guruprasad-OOD-only exclusions append-only in PostgreSQL"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run-output", type=Path)
    group.add_argument("--execute-manifest", type=Path)
    parser.add_argument("--snapshot-cutoff", type=_parse_cutoff)
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="include only candidates without a stable restoration witness at the cutoff",
    )
    parser.add_argument("--prior-manifest-sha256")
    parser.add_argument("--prior-snapshot-cutoff", type=_parse_cutoff)
    args = parser.parse_args()
    if args.execute_manifest and any(
        (
            args.snapshot_cutoff,
            args.incremental,
            args.prior_manifest_sha256,
            args.prior_snapshot_cutoff,
        )
    ):
        parser.error("snapshot/incremental options are only valid for an inert dry-run")
    if args.incremental and not (
        args.prior_manifest_sha256 and args.prior_snapshot_cutoff
    ):
        parser.error(
            "--incremental requires --prior-manifest-sha256 and "
            "--prior-snapshot-cutoff"
        )
    if not args.incremental and (
        args.prior_manifest_sha256 or args.prior_snapshot_cutoff
    ):
        parser.error("prior manifest options require --incremental")
    result = asyncio.run(
        _dry_run(
            args.dry_run_output,
            args.snapshot_cutoff,
            incremental=args.incremental,
            prior_manifest_sha256=args.prior_manifest_sha256,
            prior_snapshot_cutoff=args.prior_snapshot_cutoff,
        )
        if args.dry_run_output
        else _execute(args.execute_manifest)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
