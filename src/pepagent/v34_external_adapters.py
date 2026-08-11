from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_bytes, sha256_json

V34_CONTEXT_SCHEMA_SHA256 = (
    "1c358a48ca1c4d27554925c02f47d9c72aa273685288935b9fa9c7c7a0c745da"
)
V34_ACTIVE_POLICY_SHA256 = (
    "25fb7a5a4c8c1d001a2d313acefc065a98a709ee1f784661b3054fc01e146bb1"
)
V34_PEPSHOT_CONTRACT_SHA256 = (
    "28eb1ad5dc8a1124b4ccf7e228d30eb864222c75516fcd933737e1b60e288522"
)
V34_PEPSHOT_REQUEST_SCHEMA_SHA256 = (
    "4860a5404f10500e0844e836eda2f64f43fed702333410276cd7e8dd19ef8957"
)
V34_PEPSHOT_REVIEW_SCHEMA_SHA256 = (
    "e08a04a0dba156c0cccee59d668d2458b0c2301c1cf150834cfea26fa2d2b14d"
)


@dataclass(frozen=True)
class KnowledgeAdapterContract:
    context_schema_sha256: str = V34_CONTEXT_SCHEMA_SHA256
    active_policy_sha256: str = V34_ACTIVE_POLICY_SHA256
    required_pack_fields: tuple[str, ...] = (
        "task",
        "policy_version",
        "target_brief",
        "agent_brief",
        "design_rules",
        "evidence_index",
        "warnings",
        "knowledge_gaps",
        "retrieval_trace_id",
        "generated_at",
    )


@dataclass(frozen=True)
class PepShotAdapterContract:
    contract_sha256: str = V34_PEPSHOT_CONTRACT_SHA256
    request_schema_sha256: str = V34_PEPSHOT_REQUEST_SCHEMA_SHA256
    review_schema_sha256: str = V34_PEPSHOT_REVIEW_SCHEMA_SHA256
    maximum_priority_labeled_views: int = 3


DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT = KnowledgeAdapterContract()
DEFAULT_PEPSHOT_ADAPTER_CONTRACT = PepShotAdapterContract()


def build_knowledge_command(
    *,
    python_executable: Path,
    kbctl_path: Path,
    target_key: str,
    query: str,
    application: str,
    output_path: Path,
    config_path: Path | None = None,
) -> list[str]:
    """Build a shell-free, auditable knowledge context invocation."""
    if not target_key.strip() or not query.strip() or not application.strip():
        raise ValueError("knowledge adapter request fields cannot be blank")
    command = [str(python_executable), str(kbctl_path)]
    if config_path is not None:
        command.extend(("--config", str(config_path)))
    command.extend(
        (
            "context",
            "--target",
            target_key,
            "--query",
            query,
            "--application",
            application,
            "--output",
            str(output_path),
            "--json",
        )
    )
    return command


def validate_knowledge_context_pack(
    pack: Mapping[str, Any],
    *,
    schema_bytes: bytes,
    policy_bytes: bytes,
    policy_snapshot: Mapping[str, Any],
    expected_task: Mapping[str, str],
    contract: KnowledgeAdapterContract = DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT,
) -> dict[str, Any]:
    """Fail closed on source drift, unverified rules, or broken evidence references."""
    if sha256_bytes(schema_bytes) != contract.context_schema_sha256:
        raise ValueError("v34 knowledge context schema revision drifted")
    if sha256_bytes(policy_bytes) != contract.active_policy_sha256:
        raise ValueError("v34 active knowledge policy revision drifted")
    try:
        parsed_policy = json.loads(policy_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("v34 active knowledge policy is not valid UTF-8 JSON") from error
    if parsed_policy != dict(policy_snapshot):
        raise ValueError("v34 active knowledge policy bytes and parsed snapshot differ")
    missing = sorted(set(contract.required_pack_fields) - set(pack))
    if missing:
        raise ValueError(f"v34 knowledge context pack is missing fields: {missing}")
    if pack["policy_version"] != policy_snapshot.get("policy_version"):
        raise ValueError("v34 context pack and policy snapshot versions differ")
    observed_task = pack["task"]
    required_task_fields = {"target_key", "query", "application"}
    if not isinstance(observed_task, Mapping) or any(
        not str(observed_task.get(field, "")).strip() for field in required_task_fields
    ):
        raise ValueError("v34 knowledge context task identity is incomplete")
    if any(observed_task.get(field) != expected_task.get(field) for field in required_task_fields):
        raise ValueError("v34 knowledge context task differs from the frozen request")
    trace_id = str(pack["retrieval_trace_id"])
    if not trace_id.startswith("trace_") or len(trace_id) < 16:
        raise ValueError("v34 knowledge retrieval trace identity is invalid")
    rules = pack["design_rules"]
    if not isinstance(rules, Mapping) or set(rules) != {"direct", "transfer"}:
        raise ValueError("v34 knowledge design-rule families drifted")
    admitted_ids: list[str] = []
    allowed = {
        "direct": ({"D0", "D1"}, {"E3", "E4"}),
        "transfer": ({"D2", "D3"}, {"E2", "E3", "E4"}),
    }
    if not isinstance(pack["evidence_index"], list):
        raise ValueError("v34 knowledge evidence index must be an ordered list")
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in pack["evidence_index"]
        if isinstance(item, Mapping)
        if item.get("evidence_id") is not None
    }
    evidence_ids = set(evidence_by_id)
    for family, (distances, grades) in allowed.items():
        if not isinstance(rules[family], list):
            raise ValueError(f"v34 knowledge {family} rules must be an ordered list")
        for rule in rules[family]:
            if rule.get("status") != "verified":
                raise ValueError("v34 knowledge adapter admitted a non-verified card")
            if rule.get("distance") not in distances:
                raise ValueError("v34 knowledge adapter admitted an invalid task distance")
            if rule.get("evidence_grade") not in grades:
                raise ValueError("v34 knowledge adapter admitted insufficient evidence grade")
            refs = {str(value) for value in rule.get("evidence_refs", [])}
            if not refs or not refs.issubset(evidence_ids):
                raise ValueError("v34 knowledge rule has broken evidence references")
            if any(
                not evidence_by_id[ref].get("source_id")
                or not evidence_by_id[ref].get("locator")
                or not evidence_by_id[ref].get("asset_uri")
                for ref in refs
            ):
                raise ValueError("v34 knowledge rule cites an unlocatable passage")
            admitted_ids.append(str(rule["card_id"]))
    constraints = list(pack["target_brief"].get("constraints", []))
    if constraints and not any("advisory" in str(item).lower() for item in pack["warnings"]):
        raise ValueError("v34 E1 target constraints are not marked advisory")
    if any(item.get("distance") == "D4" for family in rules.values() for item in family):
        raise ValueError("v34 knowledge adapter cannot admit D4 cards")
    result = {
        "schema_version": "1.0",
        "policy_version": pack["policy_version"],
        "retrieval_trace_id": trace_id,
        "admitted_card_ids": admitted_ids,
        "context_pack_sha256": sha256_json(dict(pack)),
        "context_schema_sha256": contract.context_schema_sha256,
        "policy_snapshot_sha256": contract.active_policy_sha256,
    }
    result["validation_sha256"] = sha256_json(result)
    return result


def build_pepshot_command_plan(
    *,
    executable: Path,
    spec_path: Path,
    bundle_path: Path,
    review_path: Path,
) -> list[list[str]]:
    """Freeze the required build→verify→validate-review route without executing it."""
    return [
        [str(executable), "bundle", "--spec", str(spec_path), "--out", str(bundle_path)],
        [str(executable), "verify", str(bundle_path)],
        [
            str(executable),
            "validate-review",
            "--bundle",
            str(bundle_path),
            "--review",
            str(review_path),
        ],
    ]


def validate_pepshot_evidence(
    *,
    contract_bytes: bytes,
    request_schema_bytes: bytes,
    review_schema_bytes: bytes,
    agent_request: Mapping[str, Any],
    bundle_manifest: Mapping[str, Any],
    coordinate_audit: Mapping[str, Any],
    image_manifest: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
    verification_receipt: Mapping[str, Any],
    review_validation_receipt: Mapping[str, Any],
    contract: PepShotAdapterContract = DEFAULT_PEPSHOT_ADAPTER_CONTRACT,
) -> dict[str, Any]:
    """Validate a fully read, externally verified PepShot review before Agent use."""
    observed_hashes = (
        sha256_bytes(contract_bytes),
        sha256_bytes(request_schema_bytes),
        sha256_bytes(review_schema_bytes),
    )
    expected_hashes = (
        contract.contract_sha256,
        contract.request_schema_sha256,
        contract.review_schema_sha256,
    )
    if observed_hashes != expected_hashes:
        raise ValueError("v34 PepShot contract or schema revision drifted")
    if verification_receipt.get("valid") is not True:
        raise ValueError("v34 PepShot bundle verification did not succeed")
    if review_validation_receipt.get("valid") is not True:
        raise ValueError("v34 PepShot review validation did not succeed")
    bundle_id = str(agent_request.get("bundle_id", ""))
    if len(bundle_id) != 64 or review.get("bundle_id") != bundle_id:
        raise ValueError("v34 PepShot bundle/review identities differ")
    if bundle_manifest.get("bundle_id") != bundle_id:
        raise ValueError("v34 PepShot manifest identity differs from the request")
    if verification_receipt.get("bundle_id") != bundle_id or verification_receipt.get(
        "mismatches"
    ):
        raise ValueError("v34 PepShot verification receipt is incomplete or mismatched")
    if review_validation_receipt.get("bundle_id") != bundle_id or review_validation_receipt.get(
        "errors"
    ):
        raise ValueError("v34 PepShot review receipt is incomplete or mismatched")
    if review.get("status") not in {"reviewed", "insufficient_evidence"}:
        raise ValueError("v34 PepShot review status is not allowed")
    if review.get("scientific_boundary_acknowledged") is not True:
        raise ValueError("v34 PepShot review omitted its scientific boundary")
    requested = [str(value) for value in agent_request.get("images", [])]
    priority = [str(value) for value in agent_request.get("priority_images", [])]
    if len(requested) != len(set(requested)) or len(priority) != len(set(priority)):
        raise ValueError("v34 PepShot image reading order contains duplicates")
    if len(priority) > contract.maximum_priority_labeled_views:
        raise ValueError("v34 PepShot priority image budget was exceeded")
    if requested[: len(priority)] != priority or not set(priority).issubset(requested):
        raise ValueError("v34 PepShot priority images are not first in reading order")
    manifest_artifacts = {
        str(item["path"]): str(item["sha256"])
        for item in bundle_manifest.get("artifacts", [])
    }
    if len(manifest_artifacts) != len(bundle_manifest.get("artifacts", [])):
        raise ValueError("v34 PepShot bundle manifest contains duplicate paths")
    if verification_receipt.get("checked_artifact_count") != len(manifest_artifacts):
        raise ValueError("v34 PepShot verification receipt artifact count drifted")
    image_by_path = {str(item["path"]): item for item in image_manifest}
    if len(image_by_path) != len(image_manifest) or set(image_by_path) != set(requested):
        raise ValueError("v34 PepShot image reading manifest is incomplete or duplicated")
    for path in requested:
        image = image_by_path[path]
        if image.get("read_by_agent") is not True:
            raise ValueError("v34 PepShot requested image was not read by the Agent")
        if image.get("sha256") != manifest_artifacts.get(path):
            raise ValueError("v34 PepShot requested image hash differs from bundle manifest")
    manifest_view_ids = {
        str(item.get("view_id"))
        for item in bundle_manifest.get("views", [])
        if item.get("view_id")
    }
    read_view_ids = {
        str(item.get("view_id")) for item in image_manifest if item.get("view_id")
    }
    if not read_view_ids.issubset(manifest_view_ids):
        raise ValueError("v34 PepShot image reading manifest cites an unknown view")
    allowed_views = {"coordinate_audit"} | set(requested) | read_view_ids
    for flag in review.get("flags", []):
        for evidence in flag.get("evidence", []):
            if str(evidence.get("view_id")) not in allowed_views:
                raise ValueError("v34 PepShot flag cites an unread view")
    forbidden_claims = (" affinity", " kd ", "potency", "efficacy", "binding evidence")
    review_text = " " + str(review).lower() + " "
    if any(claim in review_text for claim in forbidden_claims):
        raise ValueError("v34 PepShot review crossed its scientific claim boundary")
    result = {
        "schema_version": "1.0",
        "bundle_id": bundle_id,
        "review_status": review["status"],
        "requested_image_count": len(requested),
        "priority_image_count": len(priority),
        "coordinate_audit_sha256": sha256_json(dict(coordinate_audit)),
        "review_sha256": sha256_json(dict(review)),
        "bundle_manifest_sha256": sha256_json(dict(bundle_manifest)),
        "verification_receipt_sha256": sha256_json(dict(verification_receipt)),
        "review_validation_receipt_sha256": sha256_json(
            dict(review_validation_receipt)
        ),
        "image_manifest_sha256": sha256_json(list(image_manifest)),
    }
    result["validation_sha256"] = sha256_json(result)
    return result


def build_knowledge_artifact_payloads(
    *,
    context_pack: Mapping[str, Any],
    retrieval_trace: Mapping[str, Any],
    policy_snapshot: Mapping[str, Any],
    passage_manifest: Mapping[str, Any],
    adapter_validation: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build the exact v34 knowledge artifact roles after source admission."""
    trace_id = str(context_pack["retrieval_trace_id"])
    if retrieval_trace.get("retrieval_trace_id") != trace_id:
        raise ValueError("v34 knowledge retrieval trace differs from the context pack")
    if passage_manifest.get("retrieval_trace_id") != trace_id:
        raise ValueError("v34 knowledge passage manifest differs from the context pack")
    admitted = set(adapter_validation.get("admitted_card_ids", []))
    rules = [
        item
        for family in context_pack["design_rules"].values()
        for item in family
        if item.get("card_id") in admitted
    ]
    expected_evidence = {
        str(reference) for rule in rules for reference in rule.get("evidence_refs", [])
    }
    passages = passage_manifest.get("passages", [])
    observed_evidence = {str(item.get("evidence_id")) for item in passages}
    if observed_evidence != expected_evidence or len(passages) != len(observed_evidence):
        raise ValueError("v34 knowledge passage manifest is incomplete or duplicated")
    for passage in passages:
        sha = str(passage.get("content_sha256", ""))
        if (
            len(sha) != 64
            or any(character not in "0123456789abcdef" for character in sha)
            or not passage.get("locator")
            or not passage.get("source_uri")
        ):
            raise ValueError("v34 knowledge passage identity is incomplete")
    return {
        "context_pack": dict(context_pack),
        "retrieval_trace": dict(retrieval_trace),
        "policy_snapshot": dict(policy_snapshot),
        "passage_manifest": {
            **dict(passage_manifest),
            "adapter_validation": dict(adapter_validation),
        },
    }


def build_pepshot_artifact_payloads(
    *,
    agent_request: Mapping[str, Any],
    bundle_manifest: Mapping[str, Any],
    coordinate_audit: Mapping[str, Any],
    image_manifest: Sequence[Mapping[str, Any]],
    review: Mapping[str, Any],
    verification_receipt: Mapping[str, Any],
    review_validation_receipt: Mapping[str, Any],
    adapter_validation: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build the exact v34 PepShot artifact roles, including validator receipts."""
    bundle_id = str(agent_request["bundle_id"])
    if adapter_validation.get("bundle_id") != bundle_id:
        raise ValueError("v34 PepShot adapter validation belongs to another bundle")
    return {
        "agent_request": dict(agent_request),
        "bundle_manifest": dict(bundle_manifest),
        "coordinate_audit": dict(coordinate_audit),
        "image_manifest": {"images": [dict(item) for item in image_manifest]},
        "validated_review": {
            "review": dict(review),
            "bundle_verification": dict(verification_receipt),
            "review_validation": dict(review_validation_receipt),
            "adapter_validation": dict(adapter_validation),
        },
    }
