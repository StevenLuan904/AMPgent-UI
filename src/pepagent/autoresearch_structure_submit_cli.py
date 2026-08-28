from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pepagent.autoresearch_structure_submit import (
    count_structure_lifecycle_events,
    execute_structure_formal_plan,
    load_structure_formal_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or explicitly submit the six-target wetlab-gold structure cohort"
        )
    )
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--pocket-catalog", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="reserve the six PostgreSQL runs and submit the Temporal workflows",
    )
    parser.add_argument(
        "--reserve-only",
        action="store_true",
        help="with --execute, persist the six runs/candidates without Temporal submission",
    )
    parser.add_argument(
        "--readback",
        action="store_true",
        help="read back PostgreSQL evidence counts after execution",
    )
    return parser


async def _main(args: argparse.Namespace) -> dict[str, object]:
    plan = load_structure_formal_plan(
        cohort_path=args.cohort,
        target_manifest_path=args.target_manifest,
        pocket_catalog_path=args.pocket_catalog,
    )
    if not args.execute:
        if args.reserve_only or args.readback:
            raise ValueError("--reserve-only/--readback require --execute")
        return {"status": "validated", "inert": True, "executed": False, **plan.summary()}
    result = await execute_structure_formal_plan(plan, reserve_only=args.reserve_only)
    if args.readback:
        result["postgresql_readback"] = await count_structure_lifecycle_events(plan)
    return result


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(_main(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
