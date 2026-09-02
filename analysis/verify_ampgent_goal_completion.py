from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analysis.report_pool_a_live import TARGETS
from analysis.verify_pool_a_completion import verify as verify_pool_a

ACTION_TYPES = ("masked_substitution", "controlled_crossover", "de_novo")
CLOSE_FILES = (
    "archive_updates.json",
    "parent_child_delta_receipts.json",
    "replay_bundle.json",
)
CONSUMER_THREAD_ID = "01a01cf7-c832-7930-b1a4-b39edcf1dca4"


def _contains(path: Path, needle: bytes) -> bool:
    overlap = max(0, len(needle) - 1)
    carry = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value = carry + block
            if needle in value:
                return True
            carry = value[-overlap:] if overlap else b""
    return False


def _closed_loop_evidence(reports_root: Path, target: str) -> dict[str, Any]:
    pattern = re.compile(rf"^autoresearch_lineage_round(\d+)_{target}.*_close")
    bundles: list[tuple[int, Path]] = []
    for path in reports_root.iterdir():
        match = pattern.match(path.name)
        if path.is_dir() and match and all((path / name).is_file() for name in CLOSE_FILES):
            bundles.append((int(match.group(1)), path))
    bundles.sort(key=lambda item: (item[0], item[1].name))
    archive_front_count = 0
    latest = bundles[-1][1] if bundles else None
    if latest is not None:
        archive = json.loads((latest / "archive_updates.json").read_text(encoding="utf-8"))
        current = ((archive.get("branches") or {}).get(target) or {}).get("current") or {}
        archive_front_count = len(current)
    action_counts = {}
    plans = list(reports_root.glob(f"autoresearch_lineage_round*_{target}*/plans.json"))
    for action in ACTION_TYPES:
        needle = f'"action_type": "{action}"'.encode()
        action_counts[action] = sum(_contains(path, needle) for path in plans)
    return {
        "close_bundle_count": len(bundles),
        "latest_close_bundle": str(latest) if latest else None,
        "latest_archive_front_count": archive_front_count,
        "action_plan_file_counts": action_counts,
    }


def verify(
    pool_payload: dict[str, Any], reports_root: Path, access: dict[str, Any]
) -> dict[str, Any]:
    pool = verify_pool_a(pool_payload)
    errors = [f"pool_a:{error}" for error in pool["errors"]]
    branches = {}
    for target in TARGETS:
        evidence = _closed_loop_evidence(reports_root, target)
        branches[target] = evidence
        if evidence["close_bundle_count"] < 1:
            errors.append(f"{target}:close_bundle")
        if evidence["latest_archive_front_count"] < 2:
            errors.append(f"{target}:multi_front_archive")
        for action, count in evidence["action_plan_file_counts"].items():
            if count < 1:
                errors.append(f"{target}:action:{action}")

    authority = access.get("authoritative_database") or {}
    migration = access.get("migration_evidence") or {}
    contract = access.get("access_contract") or {}
    access_checks = {
        "remote_host": authority.get("host") == "192.168.99.19",
        "database": authority.get("database") == "pepagent",
        "not_in_recovery": authority.get("in_recovery") is False,
        "cutover_rows_match": migration.get("cutover_user_table_differences") == 0,
        "old_primary_fenced": migration.get("old_primary_fenced") is True,
        "workstation_endpoint": contract.get("workstation_endpoint") == "127.0.0.1:55432",
        "consumer_thread": contract.get("consumer_thread_id") == CONSUMER_THREAD_ID,
        "local_query_passed": contract.get("local_database_query_status") == "passed",
    }
    errors.extend(f"access:{key}" for key, passed in access_checks.items() if not passed)
    return {
        "schema_version": "ampgent.goal-completion-verification.1",
        "verified_at": datetime.now(UTC).isoformat(),
        "verified": not errors,
        "pool_a": pool,
        "branches": branches,
        "remote_postgresql_access_checks": access_checks,
        "ui_message_suppressed_by_latest_user_instruction": (
            access.get("proactive_thread_message_sent") is False
        ),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool-report", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--access-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(
        json.loads(args.pool_report.read_text(encoding="utf-8")),
        args.reports_root,
        json.loads(args.access_contract.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
