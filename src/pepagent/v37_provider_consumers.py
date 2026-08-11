from __future__ import annotations

import math
import posixpath
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pepagent.provenance.hashing import sha256_json, sha256_text

PEPSHOT_RELEASE_ID = "pepshot-34487cf9667a64c3-fe1e5382de8cab09"
PEPSHOT_RELEASE_MANIFEST_SHA256 = (
    "b4f4b848f603f431e5db49bd66e018904c35c9eacf97ae83882d92e6710f2c5d"
)
PEPSHOT_RUNTIME_MANIFEST_SHA256 = (
    "332350d31e7feea6ec545b579bf680c0b46a1fb38c5110e652274451a725feba"
)
PEPSHOT_INSPECT_CONTRACT_ID = (
    "8378b91c7acd519acb2a6c3b18343d834eabdc1949c963f2b3f691087c5bb0a4"
)
PEPSHOT_REQUEST_SCHEMA_SHA256 = (
    "4b3fbcfba6dc178adfa64952a2bb0664df73060c2bddb61f734445bfdb29f331"
)
PEPSHOT_INSPECTION_SCHEMA_SHA256 = (
    "3f17ef6f9dcfe46f83569acc89d070c9f496ac4d4791e8468077a2460a673b1e"
)
PEPSHOT_SPATIAL_FINDING_SCHEMA_SHA256 = (
    "50e9d918ae5a6d6b84d20d13c0167a102cbfba9f46817094b5dfd78685db8482"
)
PEPSHOT_INSPECTION_VERSION = "pepshot-agent-inspection-v1"
PEPSHOT_INSPECT_ROUTE = {
    "route_version": "pepshot-single-owner-route-v1",
    "task": "inspect",
    "fallback_allowed": False,
    "capabilities": [
        {
            "capability_id": "structure_io",
            "module": "pepshot.structure",
            "responsibility": "read and write complex coordinates",
        },
        {
            "capability_id": "coordinate_audit",
            "module": "pepshot.audit",
            "responsibility": "produce precise spatial findings",
        },
        {
            "capability_id": "interface_analysis",
            "module": "pepshot.interface",
            "responsibility": "quantify the molecular interface",
        },
    ],
}
KNOWLEDGE_RELEASE_REVISION = "amp-kb-acea-shadow-6d0eea37f2c145df"
KNOWLEDGE_RELEASE_MANIFEST_SHA256 = (
    "7fd21012bcbcbe519dd964b6c9c826f16532d257cbb721951cb3ab0c4023e518"
)
KNOWLEDGE_RUNTIME_MANIFEST_SHA256 = (
    "15b0ab24d3290ab1fe63b9f3bca0cb3376e871ec45b2ea6a903bcd242a7a0d65"
)
KNOWLEDGE_ACTIVE_POLICY_SHA256 = (
    "4900ac9e54622132e5ea4e59ecfef6095329e77439977a8239c524e9cca73c52"
)
V37_BOLTZ_SEEDS = (20270380, 20270381, 20270382)


@dataclass(frozen=True)
class PepShotResult:
    candidate_id: str
    representative_pose_id: str
    boltz_seed: int
    disposition: Literal["retain", "reject", "insufficient"]
    reason: str
    request_sha256: str
    inspection_id: str
    inspection_sha256: str
    source_sha256: str
    interface_verdict: Literal["PASS", "WARN", "FAIL"]
    contract_id: str
    request_schema_sha256: str
    inspection_schema_sha256: str
    release_id: str
    release_manifest_sha256: str
    runtime_manifest_sha256: str


@dataclass(frozen=True)
class KnowledgeApplicabilityResult:
    candidate_id: str
    disposition: Literal["retain", "reject"]
    adopted_card_ids: tuple[str, ...]
    rejected_card_ids: tuple[str, ...]
    candidate_rejection_card_ids: tuple[str, ...]
    context_pack_sha256: str
    applicability_sha256: str
    retrieval_trace_id: str
    policy_sha256: str
    release_revision: str
    release_manifest_sha256: str
    runtime_manifest_sha256: str


@dataclass(frozen=True)
class KnowledgeContextProjection:
    """Canonical projection of the provider-owned v3 context-pack schema."""

    retrieval_trace_id: str
    policy_version: str
    cards: tuple[dict[str, Any], ...]
    passages: tuple[dict[str, Any], ...]
    adoption_edges: tuple[dict[str, Any], ...]
    context_pack_sha256: str


def consume_v37_knowledge_context_pack(
    *,
    context_pack: Mapping[str, Any],
    query_payload: Mapping[str, Any],
    candidate_ids: Sequence[str],
    provider_release_receipt: Mapping[str, Any],
) -> KnowledgeContextProjection:
    """Validate and project the immutable provider v3 pack without schema guessing."""

    exact_release = {
        "release_revision": KNOWLEDGE_RELEASE_REVISION,
        "release_manifest_sha256": KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
        "active_policy_sha256": KNOWLEDGE_ACTIVE_POLICY_SHA256,
    }
    if provider_release_receipt.get("provider_contract_verified") is not True:
        raise ValueError("knowledge provider release was not verified")
    if any(provider_release_receipt.get(key) != value for key, value in exact_release.items()):
        raise ValueError("knowledge provider release receipt drifted")

    required_top_level = {
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
    }
    if set(context_pack) != required_top_level:
        raise ValueError("knowledge context pack top-level schema drifted")
    task = context_pack.get("task")
    if not isinstance(task, Mapping) or set(task) != {
        "target_key",
        "query",
        "application",
    }:
        raise ValueError("knowledge context pack task schema drifted")
    if str(task.get("target_key", "")).lower() != "acea":
        raise ValueError("knowledge context pack target drifted")
    if task.get("query") != query_payload.get("query"):
        raise ValueError("knowledge context pack query drifted")
    if task.get("application") != "bacterial AceA inhibition and antibacterial validation":
        raise ValueError("knowledge context pack application drifted")
    if context_pack.get("policy_version") != "amp-design-context-v2":
        raise ValueError("knowledge context pack policy identity drifted")
    trace_id = _identity(context_pack.get("retrieval_trace_id"), "retrieval_trace_id")

    evidence_index = context_pack.get("evidence_index")
    if not isinstance(evidence_index, list) or not evidence_index:
        raise ValueError("knowledge context pack lacks its evidence index")
    passages: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for raw in evidence_index:
        if not isinstance(raw, Mapping):
            raise ValueError("knowledge evidence index contains a non-object item")
        evidence_id = _identity(raw.get("evidence_id"), "evidence_id")
        if evidence_id in evidence_ids:
            raise ValueError("knowledge evidence index contains duplicate evidence")
        evidence_ids.add(evidence_id)
        evidence_by_id[evidence_id] = dict(raw)
        if raw.get("status") not in {"candidate", "verified"}:
            raise ValueError("knowledge evidence index contains an invalid status")
        passages.append(
            {
                "evidence_id": evidence_id,
                "source_id": _identity(raw.get("source_id"), "source_id"),
                "status": raw["status"],
                "content_sha256": sha256_json(dict(raw)),
            }
        )

    design_rules = context_pack.get("design_rules")
    if not isinstance(design_rules, Mapping) or set(design_rules) != {"direct", "transfer"}:
        raise ValueError("knowledge context pack design-rule schema drifted")
    cards: list[dict[str, Any]] = []
    card_ids: set[str] = set()
    card_lanes: dict[str, str] = {}
    for lane in ("direct", "transfer"):
        rules = design_rules.get(lane)
        if not isinstance(rules, list):
            raise ValueError("knowledge context pack design-rule lane is not a list")
        for raw in rules:
            if not isinstance(raw, Mapping):
                raise ValueError("knowledge context pack contains a non-object design rule")
            card_id = _identity(raw.get("card_id"), "card_id")
            if card_id in card_ids:
                raise ValueError("knowledge context pack contains duplicate design rules")
            card_ids.add(card_id)
            evidence_refs = tuple(str(value) for value in raw.get("evidence_refs", []))
            if (
                not evidence_refs
                or len(evidence_refs) != len(set(evidence_refs))
                or any(value not in evidence_ids for value in evidence_refs)
            ):
                raise ValueError("knowledge design-rule evidence lineage drifted")
            if raw.get("status") not in {"candidate", "verified"}:
                raise ValueError("knowledge design rule contains an invalid status")
            cards.append(
                {
                    "card_id": card_id,
                    "revision": str(context_pack["policy_version"]),
                    "kind": lane,
                    "status": raw["status"],
                    "content_sha256": sha256_json(dict(raw)),
                    "passage_ids": list(evidence_refs),
                    "passage_manifest_sha256": sha256_json(
                        [evidence_by_id[value] for value in evidence_refs]
                    ),
                }
            )
            card_lanes[card_id] = lane
    if not cards:
        raise ValueError("knowledge context pack contains no design rules")

    ordered_candidate_ids = [
        _identity(candidate_id, "candidate_id") for candidate_id in candidate_ids
    ]
    if not ordered_candidate_ids or len(ordered_candidate_ids) != len(
        set(ordered_candidate_ids)
    ):
        raise ValueError("knowledge projection candidate identity/order is invalid")
    adoption_edges = tuple(
        {
            "evidence_id": f"{card['card_id']}:candidate-applicability",
            "disposition": "used",
            "reason": (
                f"provider {card_lanes[card['card_id']]} advisory rule; "
                "annotation only and never a selection score"
            ),
            "candidate_ids": list(ordered_candidate_ids),
        }
        for card in cards
    )
    return KnowledgeContextProjection(
        retrieval_trace_id=trace_id,
        policy_version=str(context_pack["policy_version"]),
        cards=tuple(cards),
        passages=tuple(passages),
        adoption_edges=adoption_edges,
        context_pack_sha256=sha256_json(dict(context_pack)),
    )


def _sha256(value: Any, field: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return text


def _relative_path(value: Any, field: str) -> str:
    text = str(value)
    if not text or "\\" in text or text.startswith("/") or ":" in text:
        raise ValueError(f"{field} must be a portable relative path")
    normalized = posixpath.normpath(text)
    if normalized != text or normalized in {".", ".."} or normalized.startswith("../"):
        raise ValueError(
            f"{field} must be a portable relative path and not escape its artifact root"
        )
    return text


def _identity(value: Any, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be blank")
    return text


def _representative_pose(
    candidate: Mapping[str, Any], poses: Sequence[Mapping[str, Any]]
) -> tuple[str, str, str, Mapping[str, Any], float]:
    candidate_id = _identity(candidate.get("candidate_id"), "candidate_id")
    run_id = _identity(candidate.get("run_id"), "run_id")
    sequence = _identity(candidate.get("sequence"), "sequence")
    sequence_sha256 = _sha256(candidate.get("sequence_sha256"), "sequence_sha256")
    if sha256_text(sequence) != sequence_sha256:
        raise ValueError("sequence_sha256 differs from the persisted sequence")
    if len(poses) != len(V37_BOLTZ_SEEDS):
        raise ValueError("PepShot inspect requires exactly three frozen-seed poses")
    by_seed: dict[int, Mapping[str, Any]] = {}
    for pose in poses:
        if str(pose.get("candidate_id")) != candidate_id or str(pose.get("run_id")) != run_id:
            raise ValueError("PepShot inspect pose belongs to another candidate or run")
        seed = int(pose.get("boltz_seed"))
        if seed in by_seed:
            raise ValueError("PepShot inspect poses contain a duplicate Boltz seed")
        score = float(pose.get("pair_iptm"))
        if not math.isfinite(score):
            raise ValueError("PepShot inspect pose pair-ipTM must be finite")
        _identity(pose.get("pose_id"), "pose_id")
        _relative_path(pose.get("coordinate_path"), "coordinate_path")
        _sha256(pose.get("coordinate_sha256"), "coordinate_sha256")
        by_seed[seed] = pose
    if tuple(sorted(by_seed)) != V37_BOLTZ_SEEDS:
        raise ValueError("PepShot inspect poses do not match the frozen three-seed contract")
    median = statistics.median(float(pose["pair_iptm"]) for pose in by_seed.values())
    representative = min(
        by_seed.values(),
        key=lambda pose: (abs(float(pose["pair_iptm"]) - median), int(pose["boltz_seed"])),
    )
    return candidate_id, run_id, sequence_sha256, representative, median


def build_v37_pepshot_inspect_request(
    *,
    candidate: Mapping[str, Any],
    poses: Sequence[Mapping[str, Any]],
    receptor_chains: Sequence[str],
    peptide_chains: Sequence[str],
    pocket_residues: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the official provider-owned deterministic inspect request."""
    forbidden = {"review", "reviews", "review_status", "decision", "disposition"}
    if forbidden.intersection(candidate) or any(forbidden.intersection(pose) for pose in poses):
        raise ValueError("PepShot inspect inputs must not contain a caller-prefilled review")
    candidate_id, run_id, sequence_sha256, pose, median = _representative_pose(
        candidate, poses
    )
    receptor = [_identity(value, "receptor chain") for value in receptor_chains]
    peptide = [_identity(value, "peptide chain") for value in peptide_chains]
    if not receptor or len(receptor) != len(set(receptor)):
        raise ValueError("PepShot inspect receptor chains must be non-empty and unique")
    if not peptide or len(peptide) != len(set(peptide)) or set(receptor).intersection(peptide):
        raise ValueError("PepShot inspect peptide chains must be unique and disjoint")
    residues: list[dict[str, Any]] = []
    for residue in pocket_residues:
        chain = _identity(residue.get("chain"), "pocket residue chain")
        if chain not in receptor:
            raise ValueError("PepShot inspect pocket residue is not on a receptor chain")
        normalized = {"chain": chain, "number": int(residue.get("number"))}
        insertion_code = str(residue.get("insertion_code", ""))
        if len(insertion_code) > 1:
            raise ValueError("PepShot inspect insertion code is invalid")
        if insertion_code:
            normalized["insertion_code"] = insertion_code
        residues.append(normalized)
    residue_keys = {
        (item["chain"], item["number"], item.get("insertion_code", ""))
        for item in residues
    }
    if len(residue_keys) != len(residues):
        raise ValueError("PepShot inspect pocket residues contain duplicates")
    return {
        "structure_path": str(pose["coordinate_path"]),
        "receptor_chains": receptor,
        "peptide_chains": peptide,
        "pocket_residues": residues,
        "candidate_id": candidate_id,
        "seed": int(pose["boltz_seed"]),
        "metadata": {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "sequence_sha256": sequence_sha256,
            "representative_pose_id": str(pose["pose_id"]),
            "source_sha256": str(pose["coordinate_sha256"]),
            "representative_rule": "closest_to_true_median_pair_iptm_then_seed_ascending",
            "three_seed_pair_iptm_median": median,
            "request_schema_sha256": PEPSHOT_REQUEST_SCHEMA_SHA256,
            "inspect_contract_id": PEPSHOT_INSPECT_CONTRACT_ID,
        },
    }


def consume_v37_pepshot_inspection(
    *,
    request: Mapping[str, Any],
    inspection: Mapping[str, Any],
    contract_receipt: Mapping[str, Any],
    provider_release_receipt: Mapping[str, Any],
) -> PepShotResult:
    """Validate official inspect output and project PASS/WARN/FAIL conservatively."""
    forbidden = {"review", "reviews", "review_status", "decision", "disposition"}
    if forbidden.intersection(request):
        raise ValueError("PepShot inspect request illegally contains a caller-prefilled review")
    if contract_receipt.get("contract_id") != PEPSHOT_INSPECT_CONTRACT_ID:
        raise ValueError("PepShot inspect contract identity drifted")
    expected_schema = {
        "request": PEPSHOT_REQUEST_SCHEMA_SHA256,
        "response": PEPSHOT_INSPECTION_SCHEMA_SHA256,
        "spatial_finding": PEPSHOT_SPATIAL_FINDING_SCHEMA_SHA256,
    }
    if contract_receipt.get("schema_sha256") != expected_schema:
        raise ValueError("PepShot inspect schema identities drifted")
    if (
        contract_receipt.get("task") != "inspect"
        or contract_receipt.get("fallback_allowed") is not False
        or contract_receipt.get("route") != PEPSHOT_INSPECT_ROUTE
    ):
        raise ValueError("PepShot inspect contract route drifted or enabled fallback")
    expected_release = {
        "release_id": PEPSHOT_RELEASE_ID,
        "release_manifest_sha256": PEPSHOT_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": PEPSHOT_RUNTIME_MANIFEST_SHA256,
    }
    if provider_release_receipt.get("provider_contract_verified") is not True or any(
        provider_release_receipt.get(key) != value
        for key, value in expected_release.items()
    ):
        raise ValueError("PepShot inspect provider release receipt drifted")
    candidate_id = _identity(request.get("candidate_id"), "candidate_id")
    seed = int(request.get("seed"))
    if seed not in V37_BOLTZ_SEEDS:
        raise ValueError("PepShot inspect request seed is outside the frozen set")
    metadata = request.get("metadata", {})
    pose_id = _identity(metadata.get("representative_pose_id"), "representative_pose_id")
    source_sha256 = _sha256(metadata.get("source_sha256"), "source_sha256")
    request_context = inspection.get("request_context", {})
    if (
        request_context.get("candidate_id") != candidate_id
        or int(request_context.get("seed")) != seed
        or request_context.get("metadata") != metadata
    ):
        raise ValueError("PepShot inspection belongs to another candidate, seed, or request")
    if inspection.get("source_sha256") != source_sha256:
        raise ValueError("PepShot inspection source structure SHA drifted")
    if inspection.get("inspection_version") != PEPSHOT_INSPECTION_VERSION:
        raise ValueError("PepShot inspection version drifted")
    if (
        inspection.get("fallback_allowed") is not False
        or inspection.get("route") != PEPSHOT_INSPECT_ROUTE
    ):
        raise ValueError("PepShot inspection route drifted or enabled fallback")
    if inspection.get("spatial_finding_schema_sha256") != PEPSHOT_SPATIAL_FINDING_SCHEMA_SHA256:
        raise ValueError("PepShot inspection finding schema drifted")
    identity = {
        key: value
        for key, value in inspection.items()
        if key not in {"inspection_id", "fallback_allowed", "route"}
    }
    inspection_id = _sha256(inspection.get("inspection_id"), "inspection_id")
    if inspection_id != sha256_json(identity):
        raise ValueError("PepShot inspection self-hash is invalid")
    verdict = inspection.get("audit", {}).get("interface_plausibility", {}).get("verdict")
    mapping = {
        "PASS": ("retain", "provider_inspect_interface_pass"),
        "WARN": ("insufficient", "provider_inspect_interface_warning"),
        "FAIL": ("reject", "provider_inspect_interface_fail"),
    }
    if verdict not in mapping:
        raise ValueError("PepShot inspection interface verdict is invalid or missing")
    disposition, reason = mapping[verdict]
    return PepShotResult(
        candidate_id=candidate_id,
        representative_pose_id=pose_id,
        boltz_seed=seed,
        disposition=disposition,
        reason=reason,
        request_sha256=sha256_json(dict(request)),
        inspection_id=inspection_id,
        inspection_sha256=sha256_json(dict(inspection)),
        source_sha256=source_sha256,
        interface_verdict=verdict,
        contract_id=PEPSHOT_INSPECT_CONTRACT_ID,
        request_schema_sha256=PEPSHOT_REQUEST_SCHEMA_SHA256,
        inspection_schema_sha256=PEPSHOT_INSPECTION_SCHEMA_SHA256,
        release_id=PEPSHOT_RELEASE_ID,
        release_manifest_sha256=PEPSHOT_RELEASE_MANIFEST_SHA256,
        runtime_manifest_sha256=PEPSHOT_RUNTIME_MANIFEST_SHA256,
    )


def project_v37_knowledge_applicability(
    *,
    candidate: Mapping[str, Any],
    context_pack: Mapping[str, Any],
    applicability: Sequence[Mapping[str, Any]],
    provider_release_receipt: Mapping[str, Any],
) -> KnowledgeApplicabilityResult:
    """Project a frozen run-level pack without converting positive support to score."""
    candidate_id = _identity(candidate.get("candidate_id"), "candidate_id")
    run_id = _identity(candidate.get("run_id"), "run_id")
    exact_release = {
        "release_revision": KNOWLEDGE_RELEASE_REVISION,
        "release_manifest_sha256": KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
        "active_policy_sha256": KNOWLEDGE_ACTIVE_POLICY_SHA256,
    }
    if provider_release_receipt.get("provider_contract_verified") is not True:
        raise ValueError("knowledge provider release was not verified")
    if any(provider_release_receipt.get(key) != value for key, value in exact_release.items()):
        raise ValueError("knowledge provider release receipt drifted")
    if context_pack.get("run_id") != run_id:
        raise ValueError("knowledge context pack belongs to another run")
    if context_pack.get("positive_support_is_not_a_selection_score") is not True:
        raise ValueError("knowledge context pack did not preserve the no-score boundary")
    trace_id = _identity(context_pack.get("retrieval_trace_id"), "retrieval_trace_id")
    if context_pack.get("active_policy_sha256") != KNOWLEDGE_ACTIVE_POLICY_SHA256:
        raise ValueError("knowledge context pack policy drifted")
    if any(context_pack.get(key) != value for key, value in exact_release.items()):
        raise ValueError("knowledge context pack release lineage drifted")
    card_by_id: dict[str, Mapping[str, Any]] = {}
    for card in context_pack.get("cards", []):
        card_id = _identity(card.get("card_id"), "card_id")
        if card_id in card_by_id:
            raise ValueError("knowledge context pack contains duplicate cards")
        if card.get("status") != "verified" or card.get("kind") not in {
            "warning",
            "positive_support",
            "mechanism",
        }:
            raise ValueError("knowledge context pack contains an inadmissible card")
        card_by_id[card_id] = card
    passage_by_id: dict[str, Mapping[str, Any]] = {}
    for passage in context_pack.get("passages", []):
        passage_id = _identity(passage.get("passage_id"), "passage_id")
        if passage_id in passage_by_id:
            raise ValueError("knowledge context pack contains duplicate passages")
        _sha256(passage.get("content_sha256"), "passage content_sha256")
        _relative_path(passage.get("artifact_path"), "passage artifact_path")
        passage_by_id[passage_id] = passage
    adopted: list[str] = []
    rejected: list[str] = []
    candidate_rejections: list[str] = []
    seen: set[str] = set()
    for decision in applicability:
        if decision.get("candidate_id") != candidate_id or decision.get("run_id") != run_id:
            raise ValueError("knowledge applicability belongs to another candidate or run")
        card_id = _identity(decision.get("card_id"), "card_id")
        if card_id in seen or card_id not in card_by_id:
            raise ValueError("knowledge applicability cites a duplicate or unknown card")
        seen.add(card_id)
        card = card_by_id[card_id]
        if decision.get("card_content_sha256") != card.get("content_sha256"):
            raise ValueError("knowledge applicability card SHA drifted")
        _sha256(card.get("content_sha256"), "card content_sha256")
        passage_ids = tuple(str(value) for value in decision.get("passage_ids", []))
        if not passage_ids or len(passage_ids) != len(set(passage_ids)):
            raise ValueError("knowledge applicability requires exact unique passages")
        if any(value not in passage_by_id for value in passage_ids):
            raise ValueError("knowledge applicability cites an unknown passage")
        if set(passage_ids) != set(str(value) for value in card.get("passage_ids", [])):
            raise ValueError("knowledge applicability passage lineage drifted")
        applicability_class = decision.get("applicability")
        action = decision.get("action")
        candidate_effect = decision.get("candidate_effect")
        if applicability_class not in {"direct", "transfer", "not_applicable"}:
            raise ValueError("knowledge applicability classification is invalid")
        if action not in {"adopt", "reject"}:
            raise ValueError("knowledge applicability action is invalid")
        if candidate_effect not in {"none", "annotate", "reject_candidate"}:
            raise ValueError("knowledge candidate effect is invalid")
        if action == "reject":
            if candidate_effect != "none":
                raise ValueError("a rejected knowledge card cannot affect the candidate")
            rejected.append(card_id)
        else:
            adopted.append(card_id)
            if applicability_class == "not_applicable":
                raise ValueError("a not-applicable knowledge card cannot be adopted")
            if candidate_effect == "reject_candidate":
                if card.get("kind") != "warning" or applicability_class != "direct":
                    raise ValueError(
                        "only an adopted directly applicable verified warning may reject candidate"
                    )
                candidate_rejections.append(card_id)
            elif candidate_effect != "annotate":
                raise ValueError("an adopted knowledge card must have an annotation effect")
    if seen != set(card_by_id):
        raise ValueError("knowledge applicability must decide every frozen context card")
    payload = [dict(value) for value in applicability]
    return KnowledgeApplicabilityResult(
        candidate_id=candidate_id,
        disposition="reject" if candidate_rejections else "retain",
        adopted_card_ids=tuple(adopted),
        rejected_card_ids=tuple(rejected),
        candidate_rejection_card_ids=tuple(candidate_rejections),
        context_pack_sha256=sha256_json(dict(context_pack)),
        applicability_sha256=sha256_json(payload),
        retrieval_trace_id=trace_id,
        policy_sha256=KNOWLEDGE_ACTIVE_POLICY_SHA256,
        release_revision=KNOWLEDGE_RELEASE_REVISION,
        release_manifest_sha256=KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        runtime_manifest_sha256=KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
    )
