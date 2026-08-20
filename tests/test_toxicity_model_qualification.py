from __future__ import annotations

from pepagent.toxicity_model_qualification import qualify_second_toxicity_family


def test_hypeptox_is_not_independent_when_trained_on_toxinpred3_benchmark() -> None:
    result = qualify_second_toxicity_family(
        model_id="hypeptox_fuse",
        license_commercial_use_confirmed=True,
        training_domain_independent_of_incumbent=False,
        sequence_only_inference=True,
        runtime_artifacts_transparent=True,
        pretrained_weights_available=True,
    )
    assert result.qualified_as_independent_sequence_gate is False
    assert result.blockers == ("same_training_evidence_family_as_incumbent",)


def test_structure_dependent_model_cannot_run_before_sequence_admission() -> None:
    result = qualify_second_toxicity_family(
        model_id="tamper",
        license_commercial_use_confirmed=True,
        training_domain_independent_of_incumbent=True,
        sequence_only_inference=False,
        runtime_artifacts_transparent=True,
        pretrained_weights_available=True,
    )
    assert result.blockers == ("requires_structure_before_sequence_gate",)


def test_unlicensed_opaque_pickle_model_fails_closed() -> None:
    result = qualify_second_toxicity_family(
        model_id="toxteller",
        license_commercial_use_confirmed=False,
        training_domain_independent_of_incumbent=False,
        sequence_only_inference=True,
        runtime_artifacts_transparent=False,
        pretrained_weights_available=True,
    )
    assert result.qualified_as_independent_sequence_gate is False
    assert result.blockers == (
        "commercial_use_not_confirmed",
        "same_training_evidence_family_as_incumbent",
        "opaque_or_unpinned_runtime_artifacts",
    )


def test_only_fully_independent_transparent_sequence_model_advances() -> None:
    result = qualify_second_toxicity_family(
        model_id="future_independent_model",
        license_commercial_use_confirmed=True,
        training_domain_independent_of_incumbent=True,
        sequence_only_inference=True,
        runtime_artifacts_transparent=True,
        pretrained_weights_available=True,
    )
    assert result.qualified_as_independent_sequence_gate is True
    assert result.permitted_usage == "candidate_for_independent_validation"
    assert result.blockers == ()
