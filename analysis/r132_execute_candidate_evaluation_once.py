"""r132 atomic score import writer. Default is read-only; --execute is gated."""

# ruff: noqa: E501

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from pepagent.settings import get_settings

RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"
ROOT = "f72805f4-7547-5017-a069-74042708d228"
CAMPAIGN = "acea-vegfa-dual-autoresearch-20260923-v1"


def digest(x):
    return hashlib.sha256(
        x
        if isinstance(x, (bytes, bytearray))
        else json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def norm(x):
    return x if isinstance(x, dict) else json.loads(x)


def validate_scorer_bindings(plan, run_id, candidate_ids):
    """Validate optional formal12/AMPlify ToolCall bindings in a plan.

    Legacy plans omit this field and retain their historical import behavior.
    New plans must bind every evaluation to one of two distinct batch calls;
    database status/run/input checks are performed again inside the transaction.
    """
    raw = plan.get("scorer_call_bindings")
    if raw is None:
        return {}
    if not isinstance(raw, list) or len(raw) != 2:
        raise RuntimeError("scorer bindings must contain exactly formal12 and amplify")
    bindings = {}
    kinds = set()
    expected = set(candidate_ids)
    for item in raw:
        call_id = item.get("tool_call_id")
        kind = item.get("batch_kind")
        ids = item.get("candidate_ids")
        if not isinstance(call_id, str) or not isinstance(kind, str) or kind not in {"formal12", "amplify"}:
            raise RuntimeError("invalid scorer binding identity")
        if call_id in bindings or kind in kinds or ids is None or set(ids) != expected:
            raise RuntimeError("scorer binding candidate/kind drift")
        bindings[call_id] = {"batch_kind": kind, "candidate_ids": list(ids)}
        kinds.add(kind)
    if kinds != {"formal12", "amplify"}:
        raise RuntimeError("scorer bindings must cover formal12 and amplify")
    for ev in plan.get("evaluations", []):
        scorer_id = ev.get("scorer_tool_call_id", ev.get("tool_call_id"))
        if scorer_id not in bindings:
            raise RuntimeError("evaluation scorer ToolCall binding missing or foreign")
    return bindings


def plan_context(p):
    """Return plan-derived identity and count settings; keep r132 defaults."""
    tc = p.get("score_import_tool_call", {})
    inp = tc.get("input_json", {})
    run = p.get("run_id")
    campaign = p.get("campaign_id")
    round_name = inp.get("round")
    root = p.get("scientific_root_id") or ROOT
    if not run or not campaign or not isinstance(round_name, str):
        raise RuntimeError("plan identity/status guard failed")
    if not re.fullmatch(r"r\d+", round_name):
        raise RuntimeError("plan round guard failed")
    if inp.get("run_id") != run or inp.get("campaign_id") != campaign:
        raise RuntimeError("plan input identity guard failed")
    if inp.get("source_artifacts") != p.get("source_artifacts"):
        raise RuntimeError("plan source-artifact identity guard failed")
    if f"import_{round_name}_" not in tc.get("tool_name", ""):
        raise RuntimeError("plan tool/round guard failed")
    expected_per_candidate = p.get("evaluations_per_candidate", 16)
    if not isinstance(expected_per_candidate, int) or expected_per_candidate != 16:
        raise RuntimeError("evaluation cardinality guard failed")
    candidates = p.get("candidates", [])
    evaluations = p.get("evaluations", [])
    occurrences = p.get("candidate_occurrences", [])
    if len(evaluations) != len(candidates) * expected_per_candidate:
        raise RuntimeError("plan count guard failed")
    if len(set(p["transaction_guards"]["generator_calls"])) != 2:
        raise RuntimeError("distinct generator guard failed")
    candidate_keys = {x["authoritative_candidate_id"] for x in candidates}
    if len(candidate_keys) != len(candidates) or any(
        not x.startswith(f"{round_name}-") for x in candidate_keys
    ):
        raise RuntimeError("candidate round/uniqueness guard failed")
    counts = {x: 0 for x in candidate_keys}
    for evaluation in evaluations:
        key = evaluation.get("authoritative_candidate_id")
        if key not in counts:
            raise RuntimeError("evaluation candidate guard failed")
        counts[key] += 1
    if any(count != expected_per_candidate for count in counts.values()):
        raise RuntimeError("evaluation per-candidate guard failed")
    candidate_ids = {x["id"] for x in candidates}
    scorer_bindings = validate_scorer_bindings(p, run, candidate_ids)
    generator_ids = set(p["transaction_guards"]["generator_calls"])
    candidate_by_id = {x["id"]: x for x in candidates}
    if any(x.get("generator_tool_call_id") not in generator_ids for x in candidates):
        raise RuntimeError("candidate generator-call guard failed")
    for occurrence in occurrences:
        if occurrence["candidate_id"] not in candidate_ids:
            raise RuntimeError("occurrence candidate guard failed")
        if occurrence["tool_call_id"] not in generator_ids:
            raise RuntimeError("occurrence generator-call guard failed")
        candidate = candidate_by_id[occurrence["candidate_id"]]
        if (
            occurrence["run_id"] != run
            or occurrence["parent_candidate_id"] != candidate["parent_id"]
            or occurrence["sequence"] != candidate["sequence"]
            or occurrence["sequence_sha256"] != candidate["sequence_sha256"]
        ):
            raise RuntimeError("occurrence target/candidate guard failed")
        action_id = occurrence.get("metadata_json", {}).get("action_id", "")
        if not action_id.startswith(f"{round_name}-"):
            raise RuntimeError("occurrence round guard failed")
    return p, {
        "run": run,
        "campaign": campaign,
        "root": root,
        "round": round_name,
        "evaluations_per_candidate": expected_per_candidate,
        "scorer_bindings": scorer_bindings,
    }


def validate_plan(p):
    """Validate and return the plan for the legacy r132 test/API."""
    validated, _ = plan_context(p)
    if validated.get("plan_status") != "complete_scores_pending_root_import":
        raise RuntimeError("plan identity/status guard failed")
    return validated


async def main(plan_path, receipt_path, execute=False):
    p = validate_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    _, ctx = plan_context(p)
    run_id = ctx["run"]
    campaign_id = ctx["campaign"]
    root_id = ctx["root"]
    round_name = ctx["round"]
    tc = p["score_import_tool_call"]
    imp = tc["id"]
    runtime = {
        "runtime_descriptor_kind": f"{round_name}_atomic_import_runtime",
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
        "operation": tc["tool_name"],
        "campaign_id": campaign_id,
        "run_id": run_id,
    }
    inp = dict(tc["input_json"])
    inp["runtime_descriptor"] = runtime
    inp["runtime_sha256"] = digest(runtime)
    ih = digest(inp)
    payload = {
        "candidates": p["candidates"],
        "evaluations": p["evaluations"],
        "candidate_occurrences": p["candidate_occurrences"],
        "source_artifacts": p["source_artifacts"],
    }
    oh = digest(payload)
    dsn = (
        get_settings()
        .database_url.replace(":55432/", ":55433/")
        .replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    approval_env = f"{round_name.upper()}_IMPORT_APPROVED"
    if execute and os.environ.get(approval_env) != "1":
        raise RuntimeError(f"--execute requires {approval_env}=1")
    async with asyncpg.create_pool(
        dsn, min_size=1, max_size=1, timeout=10, command_timeout=45
    ) as pool:
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute("set local lock_timeout='2s'")
                await c.execute("set local statement_timeout='45s'")
                if not execute:
                    await c.execute("set transaction read only")
                await c.execute(
                    "select pg_advisory_xact_lock(hashtextextended($1,0))",
                    f"{campaign_id}:{round_name}:score-import",
                )
                run = await c.fetchrow(
                    "select id::text,spec_json from experiment_runs where id=$1::uuid"
                    + (" for update" if execute else ""),
                    run_id,
                )
                spec = norm(run["spec_json"]) if run else {}
                if (
                    not run
                    or spec.get("root_campaign_id") != campaign_id
                    or spec.get("scientific_root_id") != root_id
                ):
                    raise RuntimeError("run/root mismatch")
                scorer_bindings = ctx["scorer_bindings"]
                for scorer_id, binding in scorer_bindings.items():
                    scorer = await c.fetchrow(
                        "select id::text,run_id::text,status,input_json from tool_calls where id=$1::uuid",
                        scorer_id,
                    )
                    if not scorer or scorer["run_id"] != run_id or scorer["status"] != "completed":
                        raise RuntimeError(f"scorer ToolCall is not completed in this run: {scorer_id}")
                    scorer_input = norm(scorer["input_json"])
                    if (
                        scorer_input.get("batch_kind") != binding["batch_kind"]
                        or scorer_input.get("candidate_ids") != binding["candidate_ids"]
                    ):
                        raise RuntimeError(f"scorer ToolCall candidate mapping mismatch: {scorer_id}")
                for gid in p["transaction_guards"]["generator_calls"]:
                    g = await c.fetchrow(
                        "select id::text,run_id::text,tool_name,status from tool_calls where id=$1::uuid",
                        gid,
                    )
                    if (
                        not g
                        or g["run_id"] != run_id
                        or g["tool_name"] != "pepmlm.generate"
                        or g["status"] != "completed"
                    ):
                        raise RuntimeError(f"generator guard failed {gid}")
                by = {x["authoritative_candidate_id"]: x["id"] for x in p["candidates"]}
                for cand in p["candidates"]:
                    par = await c.fetchrow(
                        "select id::text,run_id::text,sequence,generation,status from candidates where id=$1::uuid",
                        cand["parent_id"],
                    )
                    if (
                        not par
                        or par["run_id"] != run_id
                        or par["sequence"] != cand["parent_sequence"]
                        or int(par["generation"]) != cand["parent_generation"]
                        or par["status"] != "generated"
                    ):
                        raise RuntimeError(f"parent guard failed {cand['id']}")
                old = await c.fetchrow(
                    "select id::text,run_id::text,idempotency_key,output_sha256 from tool_calls where id=$1::uuid"
                    + (" for update" if execute else ""),
                    imp,
                )
                result = (
                    "replay" if old else ("validated_not_executed" if not execute else "inserted")
                )
                if old:
                    if (
                        old["run_id"] != run_id
                        or old["idempotency_key"] != tc["idempotency_key"]
                        or old["output_sha256"] != oh
                    ):
                        raise RuntimeError("import retry conflict")
                elif execute:
                    now = datetime.now(UTC)
                    await c.execute(
                        "insert into tool_calls (id,run_id,tool_name,tool_version,environment_sha256,idempotency_key,input_sha256,input_json,parameters_json,status,attempt,queued_at,started_at,finished_at,output_sha256) values ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,1,$11,$11,$11,$12)",
                        imp,
                        run_id,
                        tc["tool_name"],
                        f"{round_name}-score-import.v1",
                        digest(runtime),
                        tc["idempotency_key"],
                        ih,
                        json.dumps(inp, ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False),
                        "completed",
                        now,
                        oh,
                    )
                    result = "inserted"
                    for cand in p["candidates"]:
                        oldc = await c.fetchrow(
                            "select id::text,run_id::text,sequence,sequence_sha256,generation,parent_id::text,generator_call_id::text from candidates where id=$1::uuid"
                            + (" for update" if execute else ""),
                            cand["id"],
                        )
                        expected = (
                            run_id,
                            cand["sequence"],
                            cand["sequence_sha256"],
                            cand["generation"],
                            cand["parent_id"],
                            cand["generator_tool_call_id"],
                        )
                        if (
                            oldc
                            and (
                                oldc["run_id"],
                                oldc["sequence"],
                                oldc["sequence_sha256"],
                                int(oldc["generation"]),
                                oldc["parent_id"],
                                oldc["generator_call_id"],
                            )
                            != expected
                        ):
                            raise RuntimeError("candidate retry conflict")
                        if not oldc:
                            meta = {
                                k: v
                                for k, v in cand.items()
                                if k
                                not in {
                                    "id",
                                    "run_id",
                                    "sequence",
                                    "sequence_sha256",
                                    "generation",
                                    "parent_id",
                                    "generator_tool_call_id",
                                }
                            }
                            await c.execute(
                                "insert into candidates (id,run_id,sequence,sequence_sha256,generation,parent_id,status,generator_call_id,metadata_json) values ($1::uuid,$2::uuid,$3,$4,$5,$6::uuid,$7,$8::uuid,$9::jsonb)",
                                cand["id"],
                                run_id,
                                cand["sequence"],
                                cand["sequence_sha256"],
                                cand["generation"],
                                cand["parent_id"],
                                "generated",
                                cand["generator_tool_call_id"],
                                json.dumps(meta, ensure_ascii=False),
                            )
                    for ev in p["evaluations"]:
                        cid = by[ev["authoritative_candidate_id"]]
                        scorer_id = ev.get("scorer_tool_call_id", ev.get("tool_call_id", imp))
                        if scorer_bindings and scorer_id not in scorer_bindings:
                            raise RuntimeError("evaluation references unapproved scorer ToolCall")
                        olde = await c.fetchrow(
                            "select id::text,candidate_id::text,tool_call_id::text,metric_name,status from evaluations where id=$1::uuid"
                            + (" for update" if execute else ""),
                            ev["id"],
                        )
                        expected = (cid, scorer_id, ev["metric_name"], ev["status"])
                        if (
                            olde
                            and (
                                olde["candidate_id"],
                                olde["tool_call_id"],
                                olde["metric_name"],
                                olde["status"],
                            )
                            != expected
                        ):
                            raise RuntimeError("evaluation retry conflict")
                        if not olde:
                            raw = dict(ev.get("raw_source", {}))
                            raw.update(
                                {
                                    "authoritative_candidate_id": ev["authoritative_candidate_id"],
                                    "ood_status": ev.get("ood_status"),
                                    "candidate_admission_not_implied": True,
                                }
                            )
                            await c.execute(
                                "insert into evaluations (id,candidate_id,tool_call_id,metric_name,numeric_value,text_value,unit,status,out_of_domain,limitations_json,raw_json,subject_run_id,evidence_role,evidence_family,model_release_key,applicability_status,conflict_status) values ($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12::uuid,$13,$14,$15,$16,$17)",
                                ev["id"],
                                cid,
                                scorer_id,
                                ev["metric_name"],
                                ev["numeric_value"],
                                ev["text_value"],
                                ev["unit"],
                                ev["status"],
                                False,
                                json.dumps(ev.get("limitations", [])),
                                json.dumps(raw),
                                run_id,
                                ev.get("evidence_role"),
                                ev.get("evidence_family"),
                                ev.get("model_release_key"),
                                ev.get("applicability_status"),
                                ev.get("conflict_status"),
                            )
                    for oc in p["candidate_occurrences"]:
                        existing = await c.fetchrow(
                            "select id::text,run_id::text,tool_call_id::text,candidate_id::text,parent_candidate_id::text,occurrence_rank,occurrence_kind,opaque_arm_label,sequence,sequence_sha256,metadata_json from candidate_occurrences where tool_call_id=$1::uuid and occurrence_rank=$2"
                            + (" for update" if execute else ""),
                            oc["tool_call_id"],
                            oc["occurrence_rank"],
                        )
                        fields = (
                            "run_id",
                            "tool_call_id",
                            "candidate_id",
                            "parent_candidate_id",
                            "occurrence_rank",
                            "occurrence_kind",
                            "opaque_arm_label",
                            "sequence",
                            "sequence_sha256",
                            "metadata_json",
                        )
                        if existing:
                            for key in fields:
                                actual = (
                                    norm(existing[key]) if key == "metadata_json" else existing[key]
                                )
                                if actual != oc[key]:
                                    raise RuntimeError("occurrence retry conflict")
                        if not existing:
                            await c.execute(
                                "insert into candidate_occurrences (id,run_id,tool_call_id,candidate_id,parent_candidate_id,occurrence_rank,occurrence_kind,opaque_arm_label,sequence,sequence_sha256,metadata_json) values ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7,$8,$9,$10,$11::jsonb)",
                                oc["id"],
                                oc["run_id"],
                                oc["tool_call_id"],
                                oc["candidate_id"],
                                oc["parent_candidate_id"],
                                oc["occurrence_rank"],
                                oc["occurrence_kind"],
                                oc["opaque_arm_label"],
                                oc["sequence"],
                                oc["sequence_sha256"],
                                json.dumps(oc["metadata_json"], ensure_ascii=False),
                            )
                counts = await c.fetchrow(
                    "select (select count(*) from tool_calls where run_id=$1::uuid) toolcalls,(select count(*) from candidates where run_id=$1::uuid) candidates,(select count(*) from evaluations where subject_run_id=$1::uuid) evaluations,(select count(*) from candidate_occurrences where run_id=$1::uuid) occurrences",
                    run_id,
                )
    rec = {
        "executed": execute,
        "result": result,
        "tool_call_id": imp,
        "counts": dict(counts),
        "planned": {
            "candidates": len(p["candidates"]),
            "evaluations": len(p["evaluations"]),
            "occurrences": len(p["candidate_occurrences"]),
            "round": round_name,
        },
        "counts_as_scorer_invocation": False,
    }
    receipt_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    asyncio.run(main(a.plan, a.receipt, a.execute))
