import asyncio
import asyncpg
import json


async def main():
    c = await asyncpg.connect(
        "postgresql://pepagent:change-me@localhost:55432/pepagent",
        command_timeout=30,
    )
    runs = await c.fetch(
        "select id::text, status, spec_json from experiment_runs "
        "where spec_json->>'branch_key'='target_agnostic_amp' "
        "or spec_json->>'target_key'='target_agnostic' order by id"
    )
    releases = await c.fetch(
        "select e.subject_run_id::text as run_id, e.evidence_role, "
        "e.evidence_family, e.model_release_key, e.metric_name, count(*) "
        "from evaluations e join experiment_runs r on r.id=e.subject_run_id "
        "where r.spec_json->>'branch_key'='target_agnostic_amp' "
        "or r.spec_json->>'target_key'='target_agnostic' "
        "group by 1,2,3,4,5 order by 1,4,5"
    )
    print(json.dumps({"runs": [dict(row) for row in runs], "releases": [dict(row) for row in releases]}, default=str, indent=2))
    await c.close()


asyncio.run(main())
