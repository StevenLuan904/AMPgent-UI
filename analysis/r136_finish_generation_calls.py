"""Persist the real terminal claims for the already-registered r136 calls."""

# This lifecycle adapter keeps audited SQL and receipt fields verbatim.
# ruff: noqa: E501, I001, ASYNC240

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

import asyncpg
from pepagent.settings import get_settings

RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"
CAMPAIGN = "acea-vegfa-dual-autoresearch-20260923-v1"
CALLS = {
    "acea": "ca3d75f9-5f6e-5261-8439-236c0927599c",
    "vegfa": "7e9e8249-4109-51f9-9359-2d6d59242e50",
}


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def obj(value):
    return value if isinstance(value, dict) else json.loads(value or "{}")


async def main(receipt_path: Path, output_path: Path) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    claims = {item["target"]: item for item in receipt["claims"]}
    outputs = {item["target"]: item for item in receipt["outputs"]}
    if set(claims) != set(CALLS) or set(outputs) != set(CALLS):
        raise ValueError("r136 receipt must contain exactly acea and vegfa claims/outputs")
    for target, claim in claims.items():
        if claim.get("proposal_round") != 136 or claim.get("status") != "completed" or claim.get("exit_code") != 0:
            raise ValueError(f"r136 claim is not terminal exit0: {target}")

    dsn = get_settings().database_url.replace(":55432/", ":55433/").replace("postgresql+asyncpg://", "postgresql://", 1)
    result = {"run_id": RUN, "receipt": str(receipt_path), "targets": {}}
    async with asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=15, command_timeout=45) as pool:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("set local lock_timeout='2s'")
                await conn.execute("set local statement_timeout='45s'")
                await conn.execute("select pg_advisory_xact_lock(hashtextextended($1,0))", f"{CAMPAIGN}:r136:generation-lifecycle")
                run = await conn.fetchrow("select id::text,spec_json from experiment_runs where id=$1::uuid", RUN)
                if not run:
                    raise RuntimeError("scientific run missing")
                for target, call_id in CALLS.items():
                    claim, generated = claims[target], outputs[target]
                    row = await conn.fetchrow("select id::text,run_id::text,tool_name,status,input_json,parameters_json from tool_calls where id=$1::uuid for update", call_id)
                    if not row or row["run_id"] != RUN or row["tool_name"] != "pepmlm.generate":
                        raise RuntimeError(f"r136 call identity mismatch: {target}")
                    inp = obj(row["input_json"])
                    if inp.get("proposal_round") != 136 or inp.get("target_key") != target or inp.get("request_sha256") != claim["request_sha256"]:
                        raise RuntimeError(f"r136 input/claim mismatch: {target}")
                    actual = {
                        "schema_version": "r136.generation.lifecycle.v1", "target": target,
                        "proposal_round": 136, "pid": claim["pid"], "exit_code": claim["exit_code"],
                        "started_at": claim["started_at"], "completed_at": claim["completed_at"],
                        "request_sha256": claim["request_sha256"], "output_path": generated["path"],
                        "output_sha256": generated["sha256"], "output_bytes": generated["bytes"],
                        "claim_source": str(receipt_path), "model_invocation_started": True,
                        "terminal_status": "completed",
                    }
                    if row["status"] == "running":
                        params = obj(row["parameters_json"])
                        params["actual_execution"] = actual
                        await conn.execute("update tool_calls set status='completed',started_at=$2::timestamptz,finished_at=$3::timestamptz,output_sha256=$4,error_json=null,parameters_json=$5::jsonb where id=$1::uuid and run_id=$6::uuid and status='running'", call_id, dt(claim["started_at"]), dt(claim["completed_at"]), generated["sha256"], json.dumps(params, sort_keys=True), RUN)
                    after = await conn.fetchrow("select id::text,status,started_at,finished_at,output_sha256,input_json from tool_calls where id=$1::uuid and run_id=$2::uuid", call_id, RUN)
                    if not after or after["status"] != "completed" or after["output_sha256"] != generated["sha256"]:
                        raise RuntimeError(f"r136 lifecycle readback mismatch: {target}")
                    result["targets"][target] = {"id": after["id"], "status": after["status"], "started_at": str(after["started_at"]), "finished_at": str(after["finished_at"]), "output_sha256": after["output_sha256"]}
                counts = await conn.fetchrow("select count(*) filter (where run_id=$1::uuid) as toolcalls,count(*) filter (where run_id=$1::uuid and tool_name='pepmlm.generate' and status='completed') as completed_generations,count(*) filter (where run_id=$1::uuid and status='running') as running from tool_calls", RUN)
                result["counts"] = dict(counts)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.receipt, args.output))
