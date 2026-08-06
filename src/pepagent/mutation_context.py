from __future__ import annotations

import hashlib
import json
from typing import Any

STRUCTURE_METRIC_TOKENS = (
    "boltz",
    "iptm",
    "contact",
    "clash",
    "interface",
    "pocket",
    "rosetta",
    "structure",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _placeholder(item_id: str, text: str, future_provider: str) -> dict[str, Any]:
    content = {
        "item_id": item_id,
        "status": "placeholder_unavailable",
        "text": text,
        "future_provider": future_provider,
    }
    return {**content, "source_sha256": canonical_sha256(content)}


def normalize_knowledge_cards(cards: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not cards:
        return [
            _placeholder(
                "knowledge-card-placeholder-v1",
                "No retrieved knowledge card was available for this decision.",
                "versioned knowledge-card retrieval",
            )
        ]
    normalized: list[dict[str, Any]] = []
    for index, card in enumerate(cards):
        source_type = str(card.get("source_type") or "external_evidence")
        is_model_prior = source_type == "llm_internal_knowledge"
        content = {
            "item_id": str(card.get("item_id") or f"knowledge-card-{index + 1}"),
            "status": "available",
            "text": str(card["text"]),
            "source_type": source_type,
            "external_citation_available": not is_model_prior,
            "epistemic_status": (
                "uncited_model_prior" if is_model_prior else "externally_attributed"
            ),
            "source_uri": card.get("source_uri"),
            "source_locator": card.get("source_locator"),
            "model_name": card.get("model_name") if is_model_prior else None,
            "model_revision": card.get("model_revision") if is_model_prior else None,
        }
        normalized.append({**content, "source_sha256": canonical_sha256(content)})
    return normalized


def build_mutation_brief(
    candidate: dict[str, Any],
    metric_evidence: dict[str, list[dict[str, Any]]],
    knowledge_cards: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    physicochemical: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    for metric_name, value in sorted(candidate.get("metrics", {}).items()):
        evidence = metric_evidence.get(metric_name, [])
        if not evidence:
            raise ValueError(
                f"metric {metric_name!r} has no content-addressed evidence source"
            )
        item = {
            "metric_name": metric_name,
            "value": value,
            "evidence": evidence,
        }
        if any(token in metric_name.lower() for token in STRUCTURE_METRIC_TOKENS):
            structural.append(item)
        else:
            physicochemical.append(item)
    pepshot = _placeholder(
        "pepshot-placeholder-v1",
        "PepShot structural interpretation is not yet available and did not guide sampling.",
        "PepShot",
    )
    sequence_sha256 = str(candidate["sequence_sha256"])
    cards = normalize_knowledge_cards(knowledge_cards)
    guidance_lines = ["[1. Knowledge cards]"]
    guidance_lines.extend(
        f"- {item['item_id']}: {item['text']} "
        f"[source_type={item.get('source_type', 'system_placeholder')}; "
        f"external_citation_available={item.get('external_citation_available', False)}; "
        f"source_sha256={item['source_sha256']}]"
        for item in cards
    )
    guidance_lines.append("[2. Physicochemical and model/script analysis]")
    guidance_lines.extend(
        f"- {item['metric_name']}={item['value']} "
        "[evidence_sha256="
        f"{','.join(ref['evidence_sha256'] for ref in item['evidence'])}; "
        f"source_sha256={','.join(ref['source_sha256'] for ref in item['evidence'])}]"
        for item in physicochemical
    )
    guidance_lines.append("[3. Structure information]")
    guidance_lines.extend(
        f"- {item['metric_name']}={item['value']} "
        "[evidence_sha256="
        f"{','.join(ref['evidence_sha256'] for ref in item['evidence'])}; "
        f"source_sha256={','.join(ref['source_sha256'] for ref in item['evidence'])}]"
        for item in structural
    )
    guidance_lines.append(
        f"- {pepshot['item_id']}: {pepshot['text']} "
        f"[source_sha256={pepshot['source_sha256']}]"
    )
    guidance_text = "\n".join(guidance_lines)
    brief = {
        "schema_version": "mutation-brief-v1",
        "parent_candidate_id": str(candidate["id"]),
        "parent_sequence_sha256": sequence_sha256,
        "sections": {
            "knowledge_cards": cards,
            "physicochemical_and_model_evidence": physicochemical,
            "structure_information": {
                "metrics": structural,
                "pepshot": pepshot,
            },
        },
        "natural_language_guidance": guidance_text,
        "natural_language_guidance_sha256": hashlib.sha256(
            guidance_text.encode("utf-8")
        ).hexdigest(),
        "sampling_contract": {
            "pepmlm_consumes_natural_language": False,
            "applied_inputs": [
                "parent_sequence",
                "mutation_count_range",
                "top_k",
                "temperature",
                "random_seed",
            ],
            "advisory_only_inputs": [
                "knowledge_cards",
                "physicochemical_and_model_evidence",
                "structure_information",
            ],
        },
    }
    return {**brief, "brief_sha256": canonical_sha256(brief)}


def evidence_hashes(brief: dict[str, Any]) -> list[str]:
    hashes: set[str] = set()
    sections = brief["sections"]
    hashes.update(item["source_sha256"] for item in sections["knowledge_cards"])
    hashes.add(sections["structure_information"]["pepshot"]["source_sha256"])
    for group in (
        sections["physicochemical_and_model_evidence"],
        sections["structure_information"]["metrics"],
    ):
        for metric in group:
            for evidence in metric["evidence"]:
                hashes.add(evidence["source_sha256"])
                hashes.add(evidence["evidence_sha256"])
    return sorted(hashes)
