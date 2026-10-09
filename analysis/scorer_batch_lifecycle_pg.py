"""Parameterized PG lifecycle adapter for one formal12 and one AMPlify batch.

Default mode is read-only validation.  This module never starts a scorer and
does not infer timestamps; callers must supply actual terminal receipts.
"""

# The SQL is deliberately kept close to the existing production contract.
# Long statements are easier to audit verbatim than to hide behind builders.
# ruff: noqa: E501, I001, ASYNC240

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from pepagent.settings import get_settings

from scorer_tool_call_lifecycle_helper import (
    build_batch_registration,
    validate_start_receipt,
    validate_terminal_receipt,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def db_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dsn():
    return get_settings().database_url.replace(":55432/", ":55433/").replace("postgresql+asyncpg://", "postgresql://", 1)


def registrations(spec):
    return [
        build_batch_registration(
            root_id=spec["root_id"], run_id=spec["run_id"], campaign_id=spec["campaign_id"],
            round_name=spec["round"], batch_kind=item["batch_kind"], input_path=item["input_path"],
            source_path=item["source_path"], candidate_rows=item["candidate_rows"],
            model_release_key=item["model_release_key"], source_artifact_sha256=item["source_artifact_sha256"],
            attempt=item.get("attempt", 1),
            model_uri=item.get("model_uri"),
            weights_sha256=item.get("weights_sha256"),
            environment_sha256=item.get("environment_sha256"),
        )
        for item in spec["batches"]
    ]


async def main(spec_path: Path, receipt: Path | None, output: Path, execute: bool):
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    regs = registrations(spec)
    for reg in regs:
        inp = reg["input_json"]
        for field in ("input_path", "source_path"):
            if not os.path.isfile(inp[field]):
                raise FileNotFoundError(f"registered scorer {field} does not exist: {inp[field]}")
    if {x["input_json"]["batch_kind"] for x in regs} != {"formal12", "amplify"}:
        raise ValueError("spec must contain exactly formal12 and amplify batches")
    if len(regs) != 2:
        raise ValueError("exactly two batch ToolCalls are allowed")
    if execute and os.environ.get(f"{spec['round'].upper()}_SCORER_APPROVED") != "1":
        raise RuntimeError("--execute requires explicit round scorer approval environment variable")
    result = {"executed": execute, "run_id": spec["run_id"], "round": spec["round"], "registrations": [
        {"id": x["id"], "batch_kind": x["input_json"]["batch_kind"], "input_sha256": x["input_sha256"], "candidate_count": x["candidate_count"]} for x in regs
    ]}
    if receipt:
        terminal = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(terminal, list):
            terminal = [terminal]
        by_id = {x["id"]: x for x in regs}
        result["lifecycle_updates"] = []
        for item in terminal:
            registration = by_id[item["tool_call_id"]]
            update = validate_start_receipt(registration, item) if item.get("status") == "running" else validate_terminal_receipt(registration, item)
            result["lifecycle_updates"].append({"tool_call_id": item["tool_call_id"], **update})
    async with asyncpg.create_pool(dsn(), min_size=1, max_size=1, timeout=10, command_timeout=45) as pool:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("set local lock_timeout='2s'")
                await conn.execute("set local statement_timeout='45s'")
                await conn.execute("select pg_advisory_xact_lock(hashtextextended($1,0))", f"{spec['campaign_id']}:{spec['round']}:scorer-lifecycle")
                run = await conn.fetchrow("select id::text,spec_json from experiment_runs where id=$1::uuid", spec["run_id"])
                if not run:
                    raise RuntimeError("scientific run not found")
                run_spec = run["spec_json"] if isinstance(run["spec_json"], dict) else json.loads(run["spec_json"])
                if run_spec.get("root_campaign_id") != spec["campaign_id"] or run_spec.get("scientific_root_id") != spec["root_id"]:
                    raise RuntimeError("run/root identity mismatch")
                nullability = {
                    row["column_name"]: row["is_nullable"] == "YES"
                    for row in await conn.fetch(
                        "select column_name,is_nullable from information_schema.columns where table_name='tool_calls' and column_name = any($1::text[])",
                        ["model_uri", "weights_sha256", "environment_sha256"],
                    )
                }
                rows = []
                for reg in regs:
                    inp = reg["input_json"]
                    for field in ("model_uri", "weights_sha256", "environment_sha256"):
                        if inp.get(field) is None and nullability.get(field) is False:
                            raise RuntimeError(f"tool_calls.{field} is NOT NULL but the runtime manifest supplied no value")
                    old = await conn.fetchrow("select id::text,run_id::text,tool_name,status,idempotency_key,input_sha256 from tool_calls where id=$1::uuid", reg["id"])
                    if old:
                        if old["run_id"] != spec["run_id"] or old["idempotency_key"] != reg["idempotency_key"] or old["input_sha256"] != reg["input_sha256"]:
                            raise RuntimeError(f"scorer registration identity conflict: {reg['id']}")
                        rows.append({"id": old["id"], "status": old["status"], "result": "replay"})
                    elif execute:
                        now = datetime.now(UTC)
                        inp = reg["input_json"]
                        params = reg["parameters_json"]
                        await conn.execute("insert into tool_calls (id,run_id,tool_name,tool_version,model_uri,weights_sha256,environment_sha256,idempotency_key,input_sha256,input_json,parameters_json,status,attempt,queued_at,started_at) values ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,'queued',$12,$13,null)", reg["id"], spec["run_id"], reg["tool_name"], reg["tool_version"], inp["model_uri"], inp["weights_sha256"], inp["environment_sha256"], reg["idempotency_key"], reg["input_sha256"], json.dumps(inp), json.dumps(params), inp["attempt"], now)
                        rows.append({"id": reg["id"], "status": "queued", "result": "inserted"})
                    else:
                        rows.append({"id": reg["id"], "status": "absent", "result": "validated_not_executed"})
                if execute and receipt:
                    for update in result.get("lifecycle_updates", []):
                        if update["status"] == "running":
                            command = await conn.execute("update tool_calls set status='running',started_at=$2::timestamptz,parameters_json=coalesce(parameters_json,'{}'::jsonb)||$3::jsonb where id=$1::uuid and run_id=$4::uuid and status in ('queued','running')", update["tool_call_id"], db_time(update["started_at"]), json.dumps(update["parameters_json_patch"]), spec["run_id"])
                            if command != "UPDATE 1":
                                old = await conn.fetchrow("select status,started_at::text from tool_calls where id=$1::uuid and run_id=$2::uuid", update["tool_call_id"], spec["run_id"])
                                old_started = datetime.fromisoformat(old["started_at"].replace("Z", "+00:00")) if old and old["started_at"] else None
                                new_started = datetime.fromisoformat(update["started_at"].replace("Z", "+00:00"))
                                if not old or old["status"] != "running" or old_started != new_started:
                                    raise RuntimeError(f"start lifecycle conflict for {update['tool_call_id']}")
                        else:
                            command = await conn.execute("update tool_calls set status=$2,started_at=$3::timestamptz,finished_at=$4::timestamptz,output_sha256=$5,error_json=$6::jsonb,parameters_json=coalesce(parameters_json,'{}'::jsonb)||$7::jsonb where id=$1::uuid and run_id=$8::uuid and status in ('queued','running')", update["tool_call_id"], update["status"], db_time(update["started_at"]), db_time(update["finished_at"]), update["output_sha256"], json.dumps(update["error_json"]) if update["error_json"] else None, json.dumps(update["parameters_json_patch"]), spec["run_id"])
                            if command != "UPDATE 1":
                                old = await conn.fetchrow("select status,started_at::text,finished_at::text,output_sha256,error_json::text from tool_calls where id=$1::uuid and run_id=$2::uuid", update["tool_call_id"], spec["run_id"])
                                expected_error = json.dumps(update["error_json"], sort_keys=True) if update["error_json"] else None
                                old_started = datetime.fromisoformat(old["started_at"].replace("Z", "+00:00")) if old and old["started_at"] else None
                                old_finished = datetime.fromisoformat(old["finished_at"].replace("Z", "+00:00")) if old and old["finished_at"] else None
                                new_started = datetime.fromisoformat(update["started_at"].replace("Z", "+00:00"))
                                new_finished = datetime.fromisoformat(update["finished_at"].replace("Z", "+00:00"))
                                old_error = json.dumps(json.loads(old["error_json"]), sort_keys=True) if old and old["error_json"] else None
                                if not old or old["status"] != update["status"] or old_started != new_started or old_finished != new_finished or old["output_sha256"] != update["output_sha256"] or old_error != expected_error:
                                    raise RuntimeError(f"terminal lifecycle conflict for {update['tool_call_id']}")
                result["readback"] = [dict(x) for x in await conn.fetch("select id::text,run_id::text,tool_name,status,queued_at,started_at,finished_at,input_sha256,output_sha256 from tool_calls where id=any($1::uuid[]) order by id", [x["id"] for x in regs])]
                result["counts"] = dict(await conn.fetchrow("select count(*) toolcalls,count(*) filter(where status='running') running from tool_calls where run_id=$1::uuid", spec["run_id"]))
                result["rows"] = rows
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.spec, args.receipt, args.output, args.execute))
