from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from pepagent.db.models import Evaluation
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory

TOOL_NAME = "autoresearch-rosetta-receipt-ingest"
TOOL_VERSION = "2026.09.02-v2"
PRIMARY_METRIC = "rosetta_dg_separated_reu"
MINIMUM_METRIC = "rosetta_dg_minimum_reu"
TARGET_BY_ACCESSION = {
    "P0A9G6": "acea",
    "NP_416734.1": "gyra",
    "WP_308061015.1": "pbp2a",
    "NP_001020421.2": "vegfa",
    "NP_032032.1": "fgf2",
    "NP_001272991.1": "angpt1",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_receipt_payload(
    *, receipt_text: str, result_text: str, receipt_path: str
) -> dict[str, Any]:
    receipt_bytes = receipt_text.encode("utf-8")
    result_bytes = result_text.encode("utf-8")
    receipt = json.loads(receipt_text)
    if receipt.get("schema_version") != "ampgent.autoresearch-rosetta-candidate-completion.1":
        raise ValueError("not an AutoResearch candidate receipt")
    nstruct = int(receipt.get("nstruct", 0))
    if receipt.get("status") != "succeeded" or nstruct not in {20, 200}:
        raise ValueError("receipt is not a successful 20/200-decoy result")
    result_sha = sha256_bytes(result_bytes)
    if result_sha != receipt.get("result_sha256"):
        raise ValueError("result hash does not match receipt")
    result = json.loads(result_text)
    decoys = result.get("decoys")
    if not isinstance(decoys, list) or len(decoys) != nstruct:
        raise ValueError("result decoy count differs from receipt")
    top = sorted(decoys, key=lambda item: float(item["reweighted_sc"]))[:10]
    primary = statistics.median(float(item["dG_separated"]) for item in top)
    receipt_primary = float(receipt["primary_dG_separated_reu"])
    if not math.isfinite(primary) or not math.isclose(
        primary, receipt_primary, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("primary dG aggregation mismatch")
    minimum = min(float(item["dG_separated"]) for item in decoys)
    receipt_minimum = float(receipt["minimum_dG_separated_reu"])
    if not math.isfinite(minimum) or not math.isclose(
        minimum, receipt_minimum, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("minimum dG aggregation mismatch")
    return {
        "candidate_id": str(uuid.UUID(str(receipt["candidate_id"]))),
        "sequence_sha256": str(receipt["sequence_sha256"]).lower(),
        "target_key": str(receipt["target_key"]).casefold(),
        "primary": primary,
        "minimum": minimum,
        "nstruct": nstruct,
        "receipt_path": receipt_path,
        "receipt_sha256": sha256_bytes(receipt_bytes),
        "result_sha256": result_sha,
    }


def validate_receipt(path: Path) -> dict[str, Any]:
    result_path = path.parent / "results" / "rosetta_result.json"
    if not result_path.is_file():
        raise ValueError("result file is missing")
    return validate_receipt_payload(
        receipt_text=path.read_text(encoding="utf-8"),
        result_text=result_path.read_text(encoding="utf-8"),
        receipt_path=str(path.resolve()),
    )


def validate_bundle_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("schema_version") != "ampgent.rosetta-receipt-bundle-item.1":
        raise ValueError("not a Rosetta receipt bundle item")
    receipt_path = str(item.get("receipt_path", ""))
    if not receipt_path.startswith("/"):
        raise ValueError("bundle receipt_path must be an absolute remote path")
    return validate_receipt_payload(
        receipt_text=str(item["receipt_json"]),
        result_text=str(item["result_json"]),
        receipt_path=receipt_path,
    )


CANDIDATE_SQL = text(
    """
    SELECT c.id AS candidate_id, c.run_id, c.sequence_sha256, t.accession
    FROM candidates c
    JOIN experiment_runs r ON r.id=c.run_id
    JOIN targets t ON t.id=r.target_id
    WHERE c.id IN :candidate_ids
    """
).bindparams(bindparam("candidate_ids", expanding=True))

EXISTING_SQL = text(
    """
    SELECT e.candidate_id, e.metric_name, e.numeric_value
    FROM evaluations e
    WHERE e.candidate_id IN :candidate_ids
      AND e.metric_name IN :metric_names
      AND e.status='succeeded'
      AND e.numeric_value IS NOT NULL
    ORDER BY e.created_at DESC, e.id DESC
    """
).bindparams(
    bindparam("candidate_ids", expanding=True), bindparam("metric_names", expanding=True)
)


async def ingest(validated: list[dict[str, Any]], source_sha: str) -> dict[str, int]:
    if not validated:
        return {"validated": 0, "inserted_evaluations": 0, "already_present": 0}
    candidate_ids = [uuid.UUID(item["candidate_id"]) for item in validated]
    inserted = 0
    already_present = 0
    async with SessionFactory() as session:
        candidate_rows = (
            await session.execute(CANDIDATE_SQL, {"candidate_ids": candidate_ids})
        ).mappings().all()
        candidates = {str(row["candidate_id"]): dict(row) for row in candidate_rows}
        if len(candidates) != len(set(item["candidate_id"] for item in validated)):
            raise ValueError("one or more receipt candidate ids are absent from PostgreSQL")
        existing_rows = (
            await session.execute(
                EXISTING_SQL,
                {
                    "candidate_ids": candidate_ids,
                    "metric_names": [PRIMARY_METRIC, MINIMUM_METRIC],
                },
            )
        ).mappings().all()
        existing: dict[tuple[str, str], float] = {}
        for row in existing_rows:
            existing.setdefault(
                (str(row["candidate_id"]), str(row["metric_name"])),
                float(row["numeric_value"]),
            )

        pending_by_run: dict[str, list[tuple[dict[str, Any], list[tuple[str, float]]]]] = (
            defaultdict(list)
        )
        for item in validated:
            candidate = candidates[item["candidate_id"]]
            target = TARGET_BY_ACCESSION.get(str(candidate["accession"]))
            if (
                candidate["sequence_sha256"] != item["sequence_sha256"]
                or target != item["target_key"]
            ):
                raise ValueError(f"candidate identity mismatch: {item['candidate_id']}")
            missing: list[tuple[str, float]] = []
            for metric, value in (
                (PRIMARY_METRIC, item["primary"]),
                (MINIMUM_METRIC, item["minimum"]),
            ):
                prior = existing.get((item["candidate_id"], metric))
                if prior is None:
                    missing.append((metric, value))
                elif not math.isclose(prior, value, rel_tol=0.0, abs_tol=1e-9):
                    raise ValueError(
                        f"existing {metric} conflicts for candidate {item['candidate_id']}"
                    )
                else:
                    already_present += 1
            if missing:
                pending_by_run[str(candidate["run_id"])].append((item, missing))

        repository = ExperimentRepository(session)
        for run_id, rows in pending_by_run.items():
            for offset in range(0, len(rows), 512):
                batch = rows[offset : offset + 512]
                identities = [
                    {
                        "candidate_id": item["candidate_id"],
                        "receipt_sha256": item["receipt_sha256"],
                    }
                    for item, _missing in batch
                ]
                call = await repository.record_completed_tool_call(
                    uuid.UUID(run_id),
                    TOOL_NAME,
                    TOOL_VERSION,
                    source_sha,
                    {
                        "schema_version": "ampgent.rosetta-receipt-ingest.1",
                        "receipts": identities,
                    },
                    {
                        "nstruct": batch[0][0]["nstruct"],
                        "primary_aggregation": (
                            "median_dG_separated_of_top_10_reweighted_sc"
                        ),
                    },
                    {"validated_candidate_count": len(batch)},
                    model_uri="rosetta://InterfaceAnalyzer/ref2015",
                )
                for item, missing in batch:
                    raw = {
                        "schema_version": "ampgent.rosetta-receipt-ingest.1",
                        "receipt_path": item["receipt_path"],
                        "receipt_sha256": item["receipt_sha256"],
                        "result_sha256": item["result_sha256"],
                        "nstruct": item["nstruct"],
                        "primary_aggregation": (
                            "median_dG_separated_of_top_10_reweighted_sc"
                        ),
                        "pool_a_strict_dg_lt_minus_30": item["primary"] < -30.0,
                    }
                    for metric, value in missing:
                        session.add(
                            Evaluation(
                                candidate_id=uuid.UUID(item["candidate_id"]),
                                tool_call_id=call.id,
                                metric_name=metric,
                                numeric_value=value,
                                text_value=None,
                                unit="REU",
                                status="succeeded",
                                out_of_domain=False,
                                limitations_json=[
                                    "Rosetta energy units are not experimental kcal/mol"
                                ],
                                raw_json=raw,
                            )
                        )
                        inserted += 1
                await session.flush()
        await session.commit()
    return {
        "validated": len(validated),
        "inserted_evaluations": inserted,
        "already_present": already_present,
    }


async def scan_once(roots: list[Path], source_sha: str) -> dict[str, Any]:
    validated: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    seen: set[str] = set()
    marker_skipped = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("candidates/*/*/completion_receipt.json"):
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            marker = path.parent / "postgresql_ingest_receipt.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if (
                    payload.get("schema_version")
                    != "ampgent.autoresearch-rosetta-candidate-completion.1"
                    or payload.get("status") != "succeeded"
                ):
                    continue
                receipt_sha = sha256_file(path)
                if marker.exists():
                    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
                    if marker_payload.get("receipt_sha256") == receipt_sha:
                        marker_skipped += 1
                        continue
                validated.append(validate_receipt(path))
            except Exception as error:
                invalid.append({"path": resolved, "error": str(error)})
    result = await ingest(validated, source_sha)
    for item in validated:
        write_json(
            Path(item["receipt_path"]).parent / "postgresql_ingest_receipt.json",
            {
                "schema_version": "ampgent.rosetta-postgresql-ingest-receipt.1",
                "ingested_at": utc_now(),
                "candidate_id": item["candidate_id"],
                "receipt_sha256": item["receipt_sha256"],
                "metrics": [PRIMARY_METRIC, MINIMUM_METRIC],
            },
        )
    return {
        "schema_version": "ampgent.rosetta-receipt-ingester-state.1",
        "observed_at": utc_now(),
        "roots": [str(root) for root in roots],
        **result,
        "marker_skipped": marker_skipped,
        "invalid_success_receipt_count": len(invalid),
        "invalid_success_receipts": invalid[:100],
    }


async def ingest_bundle_jsonl(lines: list[str], source_sha: str) -> dict[str, Any]:
    validated: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = validate_bundle_item(json.loads(line))
            identity = item["receipt_sha256"]
            if identity not in seen:
                seen.add(identity)
                validated.append(item)
        except Exception as error:
            invalid.append({"line": str(line_number), "error": str(error)})
    result = await ingest(validated, source_sha)
    return {
        "schema_version": "ampgent.rosetta-receipt-ingester-state.1",
        "observed_at": utc_now(),
        "source": "compact_jsonl_stream",
        **result,
        "marker_skipped": 0,
        "invalid_success_receipt_count": len(invalid),
        "invalid_success_receipts": invalid[:100],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", type=Path)
    parser.add_argument("--bundle-jsonl-stdin", action="store_true")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--watch-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.bundle_jsonl_stdin and args.root:
        parser.error("--bundle-jsonl-stdin and --root are mutually exclusive")
    if not args.bundle_jsonl_stdin and not args.root:
        parser.error("one or more --root values are required")
    if args.bundle_jsonl_stdin and args.watch_seconds:
        parser.error("JSONL stdin mode is one-shot")
    source_sha = sha256_file(Path(__file__).resolve())
    if args.bundle_jsonl_stdin:
        state = asyncio.run(ingest_bundle_jsonl(sys.stdin.readlines(), source_sha))
        write_json(args.state, state)
        return
    while True:
        state = asyncio.run(scan_once(args.root or [], source_sha))
        write_json(args.state, state)
        if args.watch_seconds <= 0:
            break
        time.sleep(args.watch_seconds)


if __name__ == "__main__":
    main()
