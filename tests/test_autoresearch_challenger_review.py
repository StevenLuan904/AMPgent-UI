from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pepagent import autoresearch_challenger_worker as challenger_worker
from pepagent.autoresearch_challenger_review import (
    HEMOPI2_CLASSIFIER_SHA256,
    HEMOPI2_REGRESSOR_SHA256,
    ChallengerReviewBundle,
    ChallengerRuntimeEvidence,
    HemoPI2ChallengerPrediction,
    _calibrated_probability,
)
from pepagent.autoresearch_wetlab_gold import ChallengerReviewEvidence
from pepagent.hemopi2_v27_worker import REQUIRED_ENVIRONMENT
from pepagent.provenance.hashing import sha256_text


def _prediction(*, conflict: bool = False) -> HemoPI2ChallengerPrediction:
    return HemoPI2ChallengerPrediction(
        target_key="gyra",
        candidate_id="candidate-1",
        sequence_sha256=sha256_text("AKRWLAKIRKL"),
        raw_classification_score=0.8 if conflict else 0.01,
        raw_classification_label=1 if conflict else 0,
        calibrated_hemolysis_probability=0.8 if conflict else 0.01,
        calibration_risk_threshold=0.28,
        calibration_threshold_exceeded=conflict,
        reported_hc50_um=50.0 if conflict else 200.0,
        reported_hc50_below_100_um=conflict,
        conflict_status=("cross_model_disagreement_retained" if conflict else "no_conflict"),
    )


def _review(identity: str, *, conflict: bool = False) -> ChallengerReviewEvidence:
    return ChallengerReviewEvidence(
        target_key="gyra",
        sequence_sha256=sha256_text("AKRWLAKIRKL"),
        review_receipt_sha256=identity,
        verified_models=("hemopi2_v27",),
        missing_verified_runtimes=("apex", "peptiverse"),
        conflict_status=("cross_model_disagreement_retained" if conflict else "no_conflict"),
        unresolved_severe_conflict=False,
        limitations=(
            "challenger_predictions_are_not_wetlab_measurements",
            "hemopi2_candidate_hard_gate_forbidden",
        ),
    )


def _runtime() -> ChallengerRuntimeEvidence:
    return ChallengerRuntimeEvidence(
        runtime_python_sha256=sha256_text("python"),
        worker_sha256=sha256_text("worker"),
        inference_sha256=sha256_text("inference"),
        classifier_sha256=HEMOPI2_CLASSIFIER_SHA256,
        regressor_sha256=HEMOPI2_REGRESSOR_SHA256,
        calibration_sha256=sha256_text("calibration"),
        ood_witness_sha256=sha256_text("ood"),
        lineage_witness_sha256=sha256_text("lineage"),
        deterministic_environment=REQUIRED_ENVIRONMENT,
    )


def test_challenger_disagreement_is_retained_not_used_as_hard_gate() -> None:
    prediction = _prediction(conflict=True)
    identity = sha256_text("review")
    bundle = ChallengerReviewBundle(
        structure_cohort_sha256=sha256_text("cohort"),
        structure_cohort_receipt_sha256=sha256_text("cohort-receipt"),
        worker_input_sha256=sha256_text("input"),
        worker_output_sha256=sha256_text("output"),
        review_identity_sha256=identity,
        runtime=_runtime(),
        predictions=(prediction,),
        reviews=(_review(identity, conflict=True),),
    )

    assert bundle.reviews[0].unresolved_severe_conflict is False
    assert bundle.candidate_hard_gate_applied is False
    assert prediction.conflict_status == "cross_model_disagreement_retained"
    assert len(bundle.bundle_sha256) == 64


def test_challenger_conflict_flags_must_be_self_consistent() -> None:
    payload = _prediction(conflict=True).model_dump()
    payload["conflict_status"] = "no_conflict"
    with pytest.raises(ValidationError, match="conflict classification drifted"):
        HemoPI2ChallengerPrediction.model_validate(payload)


def test_platt_calibration_is_finite_and_monotonic() -> None:
    low = _calibrated_probability(0.01, 1.5975514426497999, 0.05690631914826638)
    high = _calibrated_probability(0.99, 1.5975514426497999, 0.05690631914826638)

    assert 0.0 < low < high < 1.0


def test_challenger_preloads_ssl_and_sklearn_before_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imports: list[str] = []

    def runner(*args: object, **kwargs: object) -> list[dict[str, object]]:
        del args, kwargs
        return []

    def fake_import(name: str) -> SimpleNamespace:
        imports.append(name)
        if name == "pepagent.hemopi2_v27_inference":
            return SimpleNamespace(run_v27_predictions=runner)
        return SimpleNamespace()

    monkeypatch.setattr(challenger_worker.importlib, "import_module", fake_import)

    assert challenger_worker._load_inference_runner() is runner
    assert imports == [
        "ssl",
        "sklearn.ensemble._forest",
        "pepagent.hemopi2_v27_inference",
    ]
