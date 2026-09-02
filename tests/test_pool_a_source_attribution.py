from analysis.audit_pool_a_source_attribution import classify_provider


def test_source_attribution_requires_explicit_provenance_fields():
    provider, evidence = classify_provider(
        {"sequence": "PEPGLAD", "notes": "pepmlm"},
        {"generator_id": "ordinary-mutation"},
    )
    assert provider is None
    assert evidence == []


def test_source_attribution_accepts_explicit_generator_identity():
    provider, evidence = classify_provider(
        {"provenance": {"operator_id": "pepmlm-target-conditioned-de-novo-v1"}},
        {"generator_id": "materializer"},
    )
    assert provider == "pepmlm"
    assert evidence[0]["path"].endswith("operator_id")


def test_source_attribution_refuses_ambiguous_explicit_identity():
    provider, evidence = classify_provider(
        {"generator_id": "pepglad"},
        {"model_uri": "model://pepflow/model2"},
    )
    assert provider is None
    assert {item["provider"] for item in evidence} == {"pepglad", "pepflow"}
