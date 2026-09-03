import asyncio
import json

import asyncpg


async def main():
    run_id = "70fe41b6-015b-5cc4-b38a-b63947bba655"
    c = await asyncpg.connect(
        "postgresql://pepagent:change-me@localhost:55432/pepagent",
        command_timeout=30,
    )
    run = await c.fetchrow("select id::text, status from experiment_runs where id=$1::uuid", run_id)
    candidate = await c.fetchval("select count(*) from candidates where run_id=$1::uuid", run_id)
    evaluation = await c.fetchval(
        "select count(*) from evaluations where subject_run_id=$1::uuid", run_id
    )
    toolcall = await c.fetchval("select count(*) from tool_calls where run_id=$1::uuid", run_id)
    print(
        json.dumps(
            {
                "run": dict(run) if run else None,
                "candidate": candidate,
                "evaluation": evaluation,
                "toolcall": toolcall,
            },
            indent=2,
        )
    )
    await c.close()


asyncio.run(main())
