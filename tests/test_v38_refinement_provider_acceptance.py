import yaml

REQUEST = "config/experiments/v38_refinement_provider_acceptance.yaml"


def test_v38_refinement_provider_request_preserves_provider_ownership() -> None:
    with open(REQUEST, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    assert payload["status"] == "requested_not_delivered"
    assert payload["owner_task_id"] == "019fad3e-76b8-7e32-8455-d2e9b31d33e5"
    assert payload["required_temporal_interface"]["activity_name"] == (
        "refine_v38_sequences_with_knowledge"
    )
    boundaries = payload["scientific_boundaries"]
    assert boundaries["ampgent_provider_compatibility_layer_forbidden"] is True
    assert boundaries["acea_specific_claims_cannot_support_gyra_or_pbp2a"] is True
    assert payload["delivery_gate"][
        "formal_science_submission_before_acceptance_forbidden"
    ] is True


def test_v38_refinement_provider_request_requires_traceable_exact_children() -> None:
    with open(REQUEST, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    response = payload["response_requirements"]
    assert response["exact_children_per_parent"] is True
    assert response["exact_parent_sequence_echo"] is True
    assert response["mutation_rationale_required"] is True
    assert response["adopted_card_trace_required"] is True
    assert response["query_and_passage_sha256_required"] is True
    assert "output_passes_ampgent_fail_closed_consumer_without_repair" in payload[
        "acceptance_tests"
    ]
