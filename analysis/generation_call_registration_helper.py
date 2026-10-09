"""Pure request validator/builder for future generation ToolCall registration.

This module performs no database writes and never infers round/action metadata
from a filename or by string replacement.  Callers must compare its returned
input_json with the DB row before launch.
"""

import argparse
import hashlib
import json
from pathlib import Path

TARGET_UUID = {
    "acea": "6c45ae22-e578-4b3e-a885-dee1f11b6390",
    "vegfa": "8dd7eb39-6c4a-4c3e-bbc3-b6add9aecd35",
}
TARGET_MANIFEST = (
    Path(__file__).parents[1]
    / "config"
    / "targets"
    / "ampgent_six_target_sequence_manifest_20260824.json"
)


def resolve_self_identity(preflight: dict, authoritative_candidate_id: str) -> dict:
    """Resolve a candidate's own PG identity from machine preflight rows.

    The authoritative lineage label is only a lookup key.  The returned UUID,
    sequence, generation, parent and generator call are copied from the row;
    callers must never substitute ``parent_id`` for the candidate's ``id``.
    """
    rows = preflight.get("candidates") if isinstance(preflight, dict) else None
    if not isinstance(rows, list) or not authoritative_candidate_id:
        raise ValueError("preflight candidates and authoritative id are required")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("metadata"), dict)
        and row["metadata"].get("authoritative_candidate_id")
        == authoritative_candidate_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"authoritative candidate must resolve uniquely: {authoritative_candidate_id!r}"
        )
    row = matches[0]
    required = ("id", "sequence", "generation", "parent_id", "generator_call_id")
    if any(key not in row for key in required):
        raise ValueError(f"candidate row missing identity fields: {authoritative_candidate_id!r}")
    nonempty_fields = ("id", "sequence", "generation", "generator_call_id")
    if any(row.get(key) in (None, "") for key in nonempty_fields):
        raise ValueError(f"candidate row missing identity fields: {authoritative_candidate_id!r}")
    if row["id"] == row["parent_id"]:
        raise ValueError("candidate id must not be its parent id")
    return {
        "authoritative_candidate_id": authoritative_candidate_id,
        "candidate_id": row["id"],
        "sequence": row["sequence"],
        "generation": row["generation"],
        "parent_id": row["parent_id"],
        "generator_call_id": row["generator_call_id"],
        "metadata": row["metadata"],
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_registration_input(
    request_path: Path,
    *,
    run_id: str,
    campaign_id: str,
    target_uuid: str,
    expected_round: int | None = None,
) -> dict:
    req = json.loads(request_path.read_text(encoding="utf-8"))
    round_no = req.get("proposal_round")
    target = req.get("target_key")
    if not isinstance(round_no, int) or (expected_round is not None and round_no != expected_round):
        raise ValueError(
            f"proposal_round mismatch: request={round_no!r}, expected={expected_round!r}"
        )
    if req.get("operational_run_id") != run_id or not target:
        raise ValueError("request run/target identity mismatch")
    if TARGET_UUID.get(target) != target_uuid:
        raise ValueError(f"target UUID mismatch for frozen target {target!r}")
    manifest = json.loads(TARGET_MANIFEST.read_text(encoding="utf-8"))
    frozen = next((x for x in manifest["targets"] if x["target_key"] == target), None)
    if (
        frozen is None
        or req.get("target_identity", {}).get("sequence_sha256") != frozen["sequence_sha256"]
    ):
        raise ValueError(f"target sequence does not match frozen manifest for {target!r}")
    actions = req.get("action_plans")
    if not isinstance(actions, list) or not actions:
        raise ValueError("request has no action_plans")
    action_ids = [a.get("action_id") for a in actions]
    if any(not x for x in action_ids) or len(action_ids) != len(set(action_ids)):
        raise ValueError("duplicate or missing action_id in request")
    for action in actions:
        required = (
            "action_id",
            "target",
            "proposal_round",
            "parent_typed_uuid",
            "parent_sequence",
            "parent_lineage_generation",
        )
        if any(k not in action for k in required):
            raise ValueError(f"action missing identity fields: {action.get('action_id')}")
        if action["target"] != target or action["proposal_round"] != round_no:
            raise ValueError(f"action/request identity mismatch: {action.get('action_id')}")
        if not action["parent_typed_uuid"] or not action["parent_sequence"]:
            raise ValueError(f"action parent unresolved: {action.get('action_id')}")
    request_sha = file_sha256(request_path)
    return {
        "campaign_id": campaign_id,
        "operational_run_id": run_id,
        "proposal_round": round_no,
        "target_key": target,
        "target_id": target_uuid,
        "target_sequence_sha256": req["target_identity"]["sequence_sha256"],
        "action_ids": action_ids,
        "request_sha256": request_sha,
        "request_path": str(request_path),
        "canonical_request_authoritative": True,
        "parent_action_plans": actions,
    }


def assert_db_input_matches_request(
    db_input: dict,
    request_path: Path,
    *,
    run_id: str,
    campaign_id: str,
    target_uuid: str,
    expected_round: int | None = None,
) -> None:
    expected = build_registration_input(
        request_path,
        run_id=run_id,
        campaign_id=campaign_id,
        target_uuid=target_uuid,
        expected_round=expected_round,
    )
    for key, value in expected.items():
        if db_input.get(key) != value:
            raise ValueError(f"DB input mismatch at {key}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--campaign", required=True)
    p.add_argument("--target-uuid", required=True)
    a = p.parse_args()
    out = build_registration_input(
        a.request,
        run_id=a.run,
        campaign_id=a.campaign,
        target_uuid=a.target_uuid,
        expected_round=a.round,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "request_sha256": out["request_sha256"],
                "proposal_round": out["proposal_round"],
                "target_key": out["target_key"],
                "action_ids": out["action_ids"],
                "parent_action_plans": out["parent_action_plans"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
