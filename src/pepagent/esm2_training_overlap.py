from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TrainingOverlapDecision:
    schema_version: str
    audit_status: str
    exact_membership_audit_allowed: bool
    formal_ood_label_allowed: bool
    model_usage: str
    selected_source: str | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_esm2_training_overlap_audit(
    *,
    official_archive_bytes: int,
    maximum_download_bytes: int,
    official_archive_available: bool,
    alternative_source_available: bool,
    alternative_source_equivalence_verified: bool,
    training_membership_semantics_reproducible: bool,
) -> TrainingOverlapDecision:
    """Fail closed unless exact ESM-2 training membership can be reproduced.

    UR50/D samples clusters and their members during training. A smaller derivative or tokenized
    mirror is therefore not interchangeable with the official release merely because its name or
    release date matches. When exact membership cannot be established within the declared resource
    budget, ESM-2 remains useful as a descriptive shadow metric but must not emit a formal OOD
    label.
    """

    if official_archive_bytes <= 0 or maximum_download_bytes < 0:
        raise ValueError("training overlap audit byte budgets must be non-negative")

    reasons: list[str] = []
    selected_source: str | None = None
    if official_archive_available and official_archive_bytes <= maximum_download_bytes:
        selected_source = "official_archive"
    elif alternative_source_available and alternative_source_equivalence_verified:
        selected_source = "verified_equivalent_source"
    else:
        if not official_archive_available:
            reasons.append("official_archive_unavailable")
        elif official_archive_bytes > maximum_download_bytes:
            reasons.append("official_archive_exceeds_download_budget")
        if alternative_source_available and not alternative_source_equivalence_verified:
            reasons.append("alternative_source_equivalence_unverified")
        elif not alternative_source_available:
            reasons.append("no_alternative_source")

    if not training_membership_semantics_reproducible:
        reasons.append("ur50d_training_membership_sampling_not_reproducible")

    allowed = selected_source is not None and training_membership_semantics_reproducible
    if allowed:
        return TrainingOverlapDecision(
            schema_version="ampgent.esm2-training-overlap-decision.1",
            audit_status="exact_membership_audit_ready",
            exact_membership_audit_allowed=True,
            formal_ood_label_allowed=False,
            model_usage="shadow_until_overlap_results_pass",
            selected_source=selected_source,
            reasons=(),
        )
    return TrainingOverlapDecision(
        schema_version="ampgent.esm2-training-overlap-decision.1",
        audit_status="exact_membership_audit_not_feasible",
        exact_membership_audit_allowed=False,
        formal_ood_label_allowed=False,
        model_usage="shadow_diagnostic_only",
        selected_source=None,
        reasons=tuple(reasons),
    )
