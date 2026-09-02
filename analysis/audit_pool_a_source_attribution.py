"""Audit explicit generator provenance for an exact Pool-A snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from pepagent.db.models import Candidate, ToolCall
from pepagent.db.session import SessionFactory

PROVIDERS = ("pepglad", "pepflow", "pepmlm")
EXPLICIT_KEYS = {
    "source_provider",
    "generator_id",
    "generator_name",
    "generator",
    "model_name",
    "model_uri",
    "operator_id",
    "action_type",
}


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def explicit_evidence(value: Any, prefix: str = "") -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).casefold() in EXPLICIT_KEYS and isinstance(item, str):
                folded = item.casefold()
                for provider in PROVIDERS:
                    if provider in folded:
                        evidence.append({"path": path, "provider": provider, "value": item})
            evidence.extend(explicit_evidence(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            evidence.extend(explicit_evidence(item, f"{prefix}[{index}]"))
    return evidence


def classify_provider(*sources: Any) -> tuple[str | None, list[dict[str, str]]]:
    evidence: list[dict[str, str]] = []
    for index, source in enumerate(sources):
        evidence.extend(explicit_evidence(source, f"source[{index}]"))
    providers = sorted({item["provider"] for item in evidence})
    return (providers[0] if len(providers) == 1 else None), evidence


async def audit(snapshot_path: Path) -> dict[str, Any]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    expected = snapshot["pool_a_all"]
    expected_by_id = {UUID(item["candidate_id"]): item for item in expected}
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(
                    Candidate.id,
                    Candidate.run_id,
                    Candidate.metadata_json,
                    Candidate.generator_call_id,
                    ToolCall.tool_name,
                    ToolCall.tool_version,
                    ToolCall.model_uri,
                    ToolCall.input_json,
                    ToolCall.parameters_json,
                )
                .outerjoin(ToolCall, ToolCall.id == Candidate.generator_call_id)
                .where(Candidate.id.in_(expected_by_id))
            )
        ).all()
    output_rows = []
    for row in rows:
        item = expected_by_id[row.id]
        if str(row.run_id) != str(item["run_id"]):
            raise ValueError(f"run identity drift for {row.id}")
        provider, evidence = classify_provider(
            row.metadata_json,
            {
                "generator_id": row.tool_name,
                "generator_name": row.tool_version,
                "model_uri": row.model_uri,
                "input": row.input_json,
                "parameters": row.parameters_json,
            },
        )
        output_rows.append(
            {
                "candidate_id": str(row.id),
                "run_id": str(row.run_id),
                "target_key": item["target_key"],
                "source_provider": provider,
                "attribution_status": "explicit" if provider else "unattributed_or_ambiguous",
                "generator_call_id": str(row.generator_call_id) if row.generator_call_id else None,
                "evidence": evidence,
            }
        )
    missing = sorted(str(value) for value in set(expected_by_id) - {row.id for row in rows})
    counts = Counter(row["source_provider"] or "unattributed_or_ambiguous" for row in output_rows)
    return {
        "schema_version": "ampgent.pool-a-source-attribution-audit.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "binding": "postgresql_candidate_id+run_id",
        "sequence_inference_used": False,
        "expected_candidate_count": len(expected),
        "database_candidate_count": len(rows),
        "missing_candidate_ids": missing,
        "source_counts": dict(sorted(counts.items())),
        "candidates": sorted(output_rows, key=lambda item: (item["target_key"], item["candidate_id"])),
    }


async def main() -> None:
    args = cli()
    payload = await audit(args.snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    asyncio.run(main())
