"""Explicit r133 score-import context; no scoring or database writes."""

import json
from pathlib import Path

try:
    from analysis.r132_occurrence_import_contract import build_occurrence_rows
except ModuleNotFoundError:  # pragma: no cover - CLI execution from analysis/
    from r132_occurrence_import_contract import build_occurrence_rows

RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"
ROOT = "f72805f4-7547-5017-a069-74042708d228"
CAMPAIGN = "acea-vegfa-dual-autoresearch-20260923-v1"
ROUND = 133
GENERATOR_CALLS = {
    "acea": "d10a433e-e3e8-531f-9442-a232aaad8578",
    "vegfa": "168cb388-dc3f-5c2c-abf5-77f11bc9eec7",
}
TARGET_UUID = {
    "acea": "6c45ae22-e578-4b3e-a885-dee1f11b6390",
    "vegfa": "8dd7eb39-6c4a-4c3e-bbc3-b6add9aecd35",
}
REQUEST_NAMES = {target: f"r133_{target}_request.json" for target in TARGET_UUID}
REMOTE_SOURCE_ROOT = (
    "/data1/huangyueshan/pepagent/runs/"
    "acea-vegfa-dual-autoresearch-20260923-v1/"
    "postprocess_round133_qd_20261009"
)


def load_approved_requests(request_dir: Path) -> dict[str, dict]:
    """Load and validate the four frozen actions from the approved requests."""
    requests = {}
    for target, name in REQUEST_NAMES.items():
        path = request_dir / name
        request = json.loads(path.read_text(encoding="utf-8"))
        if request.get("proposal_round") != ROUND or request.get("target_key") != target:
            raise ValueError(f"request identity mismatch: {path}")
        actions = request.get("action_plans")
        if not isinstance(actions, list) or len(actions) != 2:
            raise ValueError(f"r133 requires exactly two actions for {target}")
        action_ids = [action.get("action_id") for action in actions]
        if len(set(action_ids)) != 2 or any(
            not x.startswith(f"r133-{target}-") for x in action_ids
        ):
            raise ValueError(f"action identity mismatch: {path}")
        for action in actions:
            if (
                action.get("target") != target
                or action.get("proposal_round") != ROUND
                or not action.get("parent_typed_uuid")
                or not action.get("parent_sequence")
            ):
                raise ValueError(f"action parent/target mismatch: {action.get('action_id')}")
        requests[target] = {"path": str(path), "request": request, "actions": actions}
    return requests


def build_r133_occurrences(request_dir: Path, generated_rows: dict, candidate_by_sequence: dict):
    """Build all four typed raw occurrences after real scoring is available."""
    requests = load_approved_requests(request_dir)
    return build_occurrence_rows(
        run_id=RUN,
        target_tool_calls=GENERATOR_CALLS,
        action_plans={target: value["actions"] for target, value in requests.items()},
        generated_rows=generated_rows,
        canonical_candidate_by_sequence=candidate_by_sequence,
        root_uuid=ROOT,
    )
