import yaml

REQUEST = "config/experiments/v38_refinement_provider_acceptance.yaml"
BENCHMARK = "config/benchmarks/amp_sequence_first_multitarget_v38.yaml"


def test_v38_refinement_provider_request_preserves_provider_ownership() -> None:
    with open(REQUEST, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    assert payload["status"] == "accepted_immutable_release"
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
    accepted = payload["accepted_release"]
    assert accepted["release_revision"] == "amp-kb-v38-refinement-99e27f376dc955eb91bb"
    assert accepted["runtime_files_independently_verified"] == 2769
    assert accepted["native_child_proposals_independently_validated"] == 13
    assert all(
        len(accepted[field]) == 64
        for field in (
            "release_manifest_sha256",
            "runtime_manifest_sha256",
            "environment_sha256",
            "immutable_release_receipt_sha256",
            "acceptance_receipt_sha256",
            "smoke_receipt_sha256",
        )
    )


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


def test_v38_benchmark_freezes_accepted_refinement_provider() -> None:
    with open(REQUEST, encoding="utf-8") as handle:
        request = yaml.safe_load(handle)
    with open(BENCHMARK, encoding="utf-8") as handle:
        benchmark = yaml.safe_load(handle)
    accepted = request["accepted_release"]
    frozen = benchmark["knowledge_use"]["refinement_provider"]
    assert frozen["release_revision"] == accepted["release_revision"]
    assert frozen["release_manifest_sha256"] == accepted["release_manifest_sha256"]
    assert frozen["runtime_manifest_sha256"] == accepted["runtime_manifest_sha256"]
    assert frozen["environment_sha256"] == accepted["environment_sha256"]
    assert frozen["acceptance_receipt_sha256"] == accepted["acceptance_receipt_sha256"]
    assert frozen["smoke_receipt_sha256"] == accepted["smoke_receipt_sha256"]
