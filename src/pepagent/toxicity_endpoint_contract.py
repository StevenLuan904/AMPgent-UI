from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToxicityEndpointQualification:
    schema_version: str
    endpoint_kind: str
    evidence_domain: str
    formal_safety_gate_candidate: bool
    permitted_usage: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualify_toxicity_endpoint(
    *,
    endpoint_kind: str,
    experimentally_measured: bool,
    cell_line_present: bool = False,
    concentration_present: bool = False,
    exposure_duration_present: bool = False,
    assay_endpoint_present: bool = False,
) -> ToxicityEndpointQualification:
    """Fail closed when a peptide safety label lacks a measurable endpoint context.

    Hemolysis and mammalian-cell cytotoxicity are separate evidence domains. A generic
    toxin/non-toxin label may remain diagnostic, but it cannot substitute for either gate.
    """

    normalized = endpoint_kind.strip().lower()
    if not normalized:
        raise ValueError("endpoint_kind must be non-empty")

    if normalized == "hemolysis":
        blockers = () if experimentally_measured else ("experimental_measurement_missing",)
        return ToxicityEndpointQualification(
            schema_version="ampgent.toxicity-endpoint-qualification.1",
            endpoint_kind=normalized,
            evidence_domain="hemolysis",
            formal_safety_gate_candidate=not blockers,
            permitted_usage=(
                "candidate_for_hemolysis_validation"
                if not blockers
                else "diagnostic_only"
            ),
            blockers=blockers,
        )

    if normalized == "mammalian_cell_cytotoxicity":
        blockers: list[str] = []
        if not experimentally_measured:
            blockers.append("experimental_measurement_missing")
        if not cell_line_present:
            blockers.append("cell_line_missing")
        if not concentration_present:
            blockers.append("concentration_missing")
        if not exposure_duration_present:
            blockers.append("exposure_duration_missing")
        if not assay_endpoint_present:
            blockers.append("assay_endpoint_missing")
        return ToxicityEndpointQualification(
            schema_version="ampgent.toxicity-endpoint-qualification.1",
            endpoint_kind=normalized,
            evidence_domain="mammalian_cytotoxicity",
            formal_safety_gate_candidate=not blockers,
            permitted_usage=(
                "candidate_for_mammalian_cytotoxicity_validation"
                if not blockers
                else "diagnostic_only"
            ),
            blockers=tuple(blockers),
        )

    if normalized in {"database_toxin_keyword", "generic_toxicity_classification"}:
        return ToxicityEndpointQualification(
            schema_version="ampgent.toxicity-endpoint-qualification.1",
            endpoint_kind=normalized,
            evidence_domain="generic_toxicity_diagnostic",
            formal_safety_gate_candidate=False,
            permitted_usage="shadow_diagnostic_only",
            blockers=("endpoint_not_assay_specific",),
        )

    return ToxicityEndpointQualification(
        schema_version="ampgent.toxicity-endpoint-qualification.1",
        endpoint_kind=normalized,
        evidence_domain="unclassified_safety_endpoint",
        formal_safety_gate_candidate=False,
        permitted_usage="not_admitted",
        blockers=("endpoint_kind_not_recognized",),
    )
