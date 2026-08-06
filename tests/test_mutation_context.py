import pytest
from pydantic import ValidationError

from pepagent.domain.schemas import ExperimentSpec
from pepagent.mutation_context import (
    build_mutation_brief,
    canonical_sha256,
    evidence_hashes,
    normalize_knowledge_cards,
)


def test_every_mutation_brief_section_has_explicit_content_hashes() -> None:
    evidence_sha = "a" * 64
    brief = build_mutation_brief(
        {
            "id": "candidate-1",
            "sequence_sha256": "b" * 64,
            "metrics": {"net_charge": 3.0, "boltz2_pair_iptm_median": 0.42},
        },
        {
            "net_charge": [
                {
                    "source_type": "tool_call_output",
                    "source_sha256": evidence_sha,
                    "evidence_sha256": "d" * 64,
                    "tool_call_id": "tool-1",
                }
            ],
            "boltz2_pair_iptm_median": [
                {
                    "source_type": "tool_call_output",
                    "source_sha256": "c" * 64,
                    "evidence_sha256": "e" * 64,
                    "tool_call_id": "tool-2",
                }
            ],
        },
        None,
    )
    assert brief["brief_sha256"] == canonical_sha256(
        {key: value for key, value in brief.items() if key != "brief_sha256"}
    )
    assert evidence_sha in evidence_hashes(brief)
    assert brief["sections"]["knowledge_cards"][0]["source_sha256"]
    assert brief["sections"]["structure_information"]["pepshot"]["source_sha256"]
    assert "[1. Knowledge cards]" in brief["natural_language_guidance"]
    assert "[2. Physicochemical and model/script analysis]" in brief["natural_language_guidance"]
    assert "[3. Structure information]" in brief["natural_language_guidance"]
    assert f"source_sha256={evidence_sha}" in brief["natural_language_guidance"]
    assert f"evidence_sha256={'d' * 64}" in brief["natural_language_guidance"]
    assert brief["sampling_contract"]["pepmlm_consumes_natural_language"] is False


def test_knowledge_hash_is_per_atomic_card_not_per_collection() -> None:
    cards = normalize_knowledge_cards(
        [
            {"item_id": "card-a", "text": "first fact", "source_locator": "p.1"},
            {"item_id": "card-b", "text": "second fact", "source_locator": "p.2"},
        ]
    )
    assert cards[0]["source_sha256"] != cards[1]["source_sha256"]
    assert all(len(card["source_sha256"]) == 64 for card in cards)


def test_llm_internal_knowledge_is_allowed_but_never_masquerades_as_a_citation() -> None:
    cards = normalize_knowledge_cards(
        [
            {
                "item_id": "agent-prior-1",
                "text": "Cationic amphipathic peptides often interact with bacterial membranes.",
                "source_type": "llm_internal_knowledge",
                "model_name": "example-model",
                "model_revision": "example-revision",
            }
        ]
    )
    card = cards[0]
    assert card["source_type"] == "llm_internal_knowledge"
    assert card["external_citation_available"] is False
    assert card["epistemic_status"] == "uncited_model_prior"
    assert card["model_name"] == "example-model"
    assert len(card["source_sha256"]) == 64


def test_mutation_brief_fails_closed_when_a_metric_has_no_evidence_hash() -> None:
    with pytest.raises(ValueError, match="no content-addressed evidence"):
        build_mutation_brief(
            {
                "id": "candidate-1",
                "sequence_sha256": "b" * 64,
                "metrics": {"net_charge": 3.0},
            },
            {},
            None,
        )


def test_knowledge_cards_are_atomic_and_require_text() -> None:
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "target": {"name": "target", "sequence": "ACDEFGHIK"},
                "mutation_knowledge_cards": [{"item_id": "missing-text"}],
            }
        )

    with pytest.raises(ValidationError, match="model_name and model_revision"):
        ExperimentSpec.model_validate(
            {
                "target": {"name": "target", "sequence": "ACDEFGHIK"},
                "mutation_knowledge_cards": [
                    {
                        "item_id": "uncited-prior",
                        "text": "a model prior",
                        "source_type": "llm_internal_knowledge",
                    }
                ],
            }
        )
