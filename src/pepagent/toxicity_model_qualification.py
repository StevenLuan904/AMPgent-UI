from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToxicityModelQualification:
    schema_version: str
    model_id: str
    qualified_as_independent_sequence_gate: bool
    permitted_usage: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualify_second_toxicity_family(
    *,
    model_id: str,
    license_commercial_use_confirmed: bool,
    training_domain_independent_of_incumbent: bool,
    sequence_only_inference: bool,
    runtime_artifacts_transparent: bool,
    pretrained_weights_available: bool,
) -> ToxicityModelQualification:
    """Evaluate whether a model can independently corroborate the sequence safety gate."""

    if not model_id.strip():
        raise ValueError("model_id must be non-empty")
    blockers: list[str] = []
    if not license_commercial_use_confirmed:
        blockers.append("commercial_use_not_confirmed")
    if not training_domain_independent_of_incumbent:
        blockers.append("same_training_evidence_family_as_incumbent")
    if not sequence_only_inference:
        blockers.append("requires_structure_before_sequence_gate")
    if not runtime_artifacts_transparent:
        blockers.append("opaque_or_unpinned_runtime_artifacts")
    if not pretrained_weights_available:
        blockers.append("pretrained_weights_unavailable")

    qualified = not blockers
    return ToxicityModelQualification(
        schema_version="ampgent.toxicity-model-qualification.1",
        model_id=model_id.strip(),
        qualified_as_independent_sequence_gate=qualified,
        permitted_usage=(
            "candidate_for_independent_validation"
            if qualified
            else "not_formal_sequence_gate"
        ),
        blockers=tuple(blockers),
    )
