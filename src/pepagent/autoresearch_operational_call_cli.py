from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.session import SessionFactory


async def _persist(record: OperationalCallRecord) -> dict[str, str]:
    async with SessionFactory() as session, session.begin():
        run, call = await persist_operational_call(session, record)
    return {
        "status": call.status,
        "target_key": record.target_key,
        "operation_key": record.operation_key,
        "operational_run_id": str(run.id),
        "tool_call_id": str(call.id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Persist one operational AMPgent invocation in PostgreSQL"
    )
    parser.add_argument(
        "--record",
        required=True,
        help="Path to one JSON record, or '-' to read the record from stdin",
    )
    args = parser.parse_args()
    record_text = (
        sys.stdin.read()
        if args.record == "-"
        else Path(args.record).resolve().read_text(encoding="utf-8-sig")
    )
    record = OperationalCallRecord.model_validate_json(
        record_text
    )
    result = asyncio.run(_persist(record))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
