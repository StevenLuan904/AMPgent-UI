from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

REQUIRED_EVIDENCE_DOMAINS = frozenset(
    {
        "pathogen_conditioned_potency",
        "amp_likeness",
        "hemolysis",
        "mammalian_cytotoxicity",
        "toxicity",
        "physicochemical",
        "solubility_and_aggregation",
        "serum_and_protease_stability",
        "novelty_and_ood",
        "synthesis_feasibility",
        "resistance_propensity",
        "commensal_selectivity",
    }
)

ENSEMBLE_DOMAINS = frozenset(
    {
        "pathogen_conditioned_potency",
        "hemolysis",
        "toxicity",
    }
)


@dataclass(frozen=True)
class TargetIdentityAudit:
    sequence_identity_fraction: float
    target_sequence_coverage: float
    organism_matches: bool
    accession_matches: bool
    findings: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.findings


def audit_target_identity(
    *,
    target_sequence: str,
    coordinate_chain_sequence: str,
    registered_organism: str,
    coordinate_organism: str,
    registered_accession: str,
    coordinate_polymer_accession: str,
    direct_experimental_structure: bool = True,
    minimum_target_coverage: float = 0.95,
    minimum_sequence_identity: float = 0.90,
) -> TargetIdentityAudit:
    """Fail closed when a registered target and coordinate chain are not the same entity.

    The matcher is deliberately transparent and dependency-free. It reports matching-residue
    coverage for the registered target. A production witness must also persist the source bytes
    and accession metadata used to build this audit.
    """
    target = "".join(target_sequence.split()).upper()
    coordinate = "".join(coordinate_chain_sequence.split()).upper()
    if not target or not coordinate:
        raise ValueError("target and coordinate-chain sequences must be non-empty")

    matcher = SequenceMatcher(a=target, b=coordinate, autojunk=False)
    matching_residues = sum(block.size for block in matcher.get_matching_blocks())
    coverage = matching_residues / len(target)
    identity = matching_residues / min(len(target), len(coordinate))
    organism_matches = (
        registered_organism.strip().casefold()
        == coordinate_organism.strip().casefold()
    )
    accession_matches = (
        registered_accession.strip().casefold()
        == coordinate_polymer_accession.strip().casefold()
    )

    findings: list[str] = []
    if coverage < minimum_target_coverage:
        findings.append("coordinate chain does not cover enough of the registered target sequence")
    if identity < minimum_sequence_identity:
        findings.append("coordinate chain identity is below the registered minimum")
    if direct_experimental_structure and not organism_matches:
        findings.append("direct experimental structure organism does not match registered organism")
    if direct_experimental_structure and not accession_matches:
        findings.append(
            "direct experimental structure polymer accession does not match target accession"
        )
    return TargetIdentityAudit(
        sequence_identity_fraction=identity,
        target_sequence_coverage=coverage,
        organism_matches=organism_matches,
        accession_matches=accession_matches,
        findings=tuple(findings),
    )


def audit_enterprise_pipeline_contract(contract: dict[str, Any]) -> list[str]:
    """Return actionable readiness gaps without pretending an audit-only contract is runnable."""
    findings: list[str] = []
    scope = contract.get("scope", {})
    if scope.get("formal_science_run_authorized") is not False:
        findings.append("enterprise audit contract must not authorize a formal science run")

    coverage = contract.get("target_coverage", {})
    if not coverage.get("pathogen_phenotype_panels"):
        findings.append("at least one pathogen phenotype panel is required")
    if not coverage.get("commensal_counter_screen_panels"):
        findings.append("at least one commensal counter-screen panel is required")
    identity_gate = coverage.get("protein_target_identity_gate", {})
    for field in (
        "organism_and_strain_match_required",
        "polymer_accession_match_required",
        "coordinate_chain_sequence_alignment_required",
        "native_and_wrong_pocket_controls_required",
    ):
        if identity_gate.get(field) is not True:
            findings.append(f"protein target identity gate must require {field}")

    registry = contract.get("evidence_domains", {})
    missing_domains = sorted(REQUIRED_EVIDENCE_DOMAINS - registry.keys())
    if missing_domains:
        findings.append("missing evidence domains: " + ", ".join(missing_domains))
    for domain in sorted(ENSEMBLE_DOMAINS & registry.keys()):
        if int(registry[domain].get("minimum_independent_models", 0)) < 2:
            findings.append(f"{domain} requires at least two independent models")
        if not registry[domain].get("calibration_and_ood_required"):
            findings.append(f"{domain} requires calibration and OOD evidence")

    selection = contract.get("selection", {})
    required_selection = {
        "quality_admission_before_pareto": True,
        "weighted_total_score_forbidden": True,
        "current_batch_quantile_thresholds_forbidden": True,
        "inactive_candidate_cannot_be_rescued_by_one_extreme_axis": True,
        "no_forced_fill": True,
    }
    for field, required in required_selection.items():
        if selection.get(field) is not required:
            findings.append(f"selection contract must set {field}={required}")
    if selection.get("pareto_policy") != "constrained_epsilon_reference_point":
        findings.append("selection must use constrained epsilon/reference-point Pareto")

    structure = contract.get("adaptive_structure", {})
    for field in (
        "sequence_quality_gate_required",
        "preregistered_sequential_stopping",
        "target_interleaving_required",
        "boltz_rosetta_pipeline_required",
    ):
        if structure.get(field) is not True:
            findings.append(f"adaptive structure contract must require {field}")
    if int(structure.get("maximum_candidates", 0)) > 24:
        findings.append("adaptive structure maximum_candidates must be <= 24")

    loop = contract.get("autoresearch_loop", {})
    if loop.get("cycle") != [
        "observe",
        "diagnose",
        "propose_experiment",
        "act",
        "verify",
        "persist",
        "learn",
    ]:
        findings.append("autoresearch loop must encode the full learning cycle")
    if loop.get("monitor_only_iteration_forbidden") is not True:
        findings.append("monitor-only autoresearch iterations must be forbidden")
    return findings


def assert_enterprise_pipeline_contract(contract: dict[str, Any]) -> None:
    findings = audit_enterprise_pipeline_contract(contract)
    if findings:
        raise ValueError("enterprise pipeline contract is invalid: " + "; ".join(findings))
