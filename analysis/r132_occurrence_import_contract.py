"""Pure r132 occurrence payload builder for the append-only candidate import.

The caller must insert the returned rows in the same transaction as the
Candidate rows.  This module performs no database I/O and treats a raw alias
as a distinct occurrence while allowing it to point at the canonical Candidate.
"""

import hashlib
import math
import uuid
from typing import Any

AA_ALPHABET = set("ACDEFGHIKLMNPQRSTVWY")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def build_occurrence_rows(
    *,
    run_id: str,
    target_tool_calls: dict[str, str],
    action_plans: dict[str, list[dict[str, Any]]],
    generated_rows: dict[str, list[dict[str, Any]]],
    canonical_candidate_by_sequence: dict[str, str],
    root_uuid: str,
) -> list[dict[str, Any]]:
    """Build deterministic rows; aliases retain target/action evidence."""
    rows: list[dict[str, Any]] = []
    expected_targets = set(action_plans)
    if expected_targets != set(generated_rows) or expected_targets != set(target_tool_calls):
        raise ValueError("target set mismatch")
    for target in sorted(action_plans):
        tool_call_id = target_tool_calls[target]
        plans = action_plans[target]
        generated = generated_rows[target]
        plan_ids = [row.get("action_id") for row in plans]
        generated_ids = [row.get("action_id") for row in generated]
        if not all(plan_ids) or len(plan_ids) != len(set(plan_ids)):
            raise ValueError(f"duplicate or missing action IDs in plans for {target}")
        if not all(generated_ids) or len(generated_ids) != len(set(generated_ids)):
            raise ValueError(f"duplicate or missing action IDs in generated rows for {target}")
        if set(plan_ids) != set(generated_ids):
            raise ValueError(f"action ID set mismatch for {target}")
        generated_by_action = {row["action_id"]: row for row in generated}
        for rank, action in enumerate(action_plans[target], start=1):
            generated = generated_by_action[action["action_id"]]
            sequence = generated.get("sequence")
            if (
                not isinstance(sequence, str)
                or not sequence
                or sequence != sequence.strip()
                or sequence != sequence.upper()
                or any(char.isspace() for char in sequence)
                or not set(sequence) <= AA_ALPHABET
            ):
                raise ValueError(f"sequence is not canonical for {action['action_id']}")
            required_action = (
                "parent_typed_uuid",
                "seed",
                "mutation_positions",
                "lineage_generation",
            )
            if any(key not in action or action[key] in (None, "", []) for key in required_action):
                raise ValueError(f"action identity incomplete for {action['action_id']}")
            required_score = ("conditional_nll", "conditional_ppl")
            if any(key not in generated or generated[key] is None for key in required_score):
                raise ValueError(f"score evidence incomplete for {action['action_id']}")
            if any(
                isinstance(generated[key], bool)
                or not isinstance(generated[key], (int, float))
                or not math.isfinite(float(generated[key]))
                for key in required_score
            ):
                raise ValueError(f"score evidence non-finite for {action['action_id']}")
            parent_id = action["parent_typed_uuid"]
            candidate_id = canonical_candidate_by_sequence[sequence]
            identity = f"{run_id}:{tool_call_id}:{rank}"
            metadata = {
                "target": target,
                "action_id": action["action_id"],
                "action_sha256": action.get("action_sha256"),
                "seed": action.get("seed", action.get("action_seed")),
                "mutation_positions": action.get("mutation_positions", []),
                "lineage_generation": action.get("lineage_generation"),
                "generator_call_id": tool_call_id,
                "conditional_nll": generated.get("conditional_nll"),
                "conditional_ppl": generated.get("conditional_ppl"),
                "canonical_sequence": sequence,
            }
            rows.append(
                {
                    "id": str(uuid.uuid5(uuid.UUID(root_uuid), f"candidate_occurrence:{identity}")),
                    "run_id": run_id,
                    "tool_call_id": tool_call_id,
                    "candidate_id": candidate_id,
                    "parent_candidate_id": parent_id,
                    "occurrence_rank": rank,
                    "occurrence_kind": action.get("action_kind", "masked_substitution"),
                    "opaque_arm_label": target,
                    "sequence": sequence,
                    "sequence_sha256": _sha(sequence),
                    "metadata_json": metadata,
                }
            )
    return rows


def occurrence_identity(row: dict[str, Any]) -> dict[str, Any]:
    """The immutable retry identity for `(tool_call_id, occurrence_rank)`."""
    return {
        key: row[key]
        for key in (
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
    }


def assert_retry_identity(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    if occurrence_identity(existing) != occurrence_identity(incoming):
        raise ValueError("candidate occurrence retry payload drifted")
