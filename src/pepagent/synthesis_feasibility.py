from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json, sha256_text

SYNTHESIS_RULESET_SCHEMA = "ampgent.synthesis-feasibility-rules.1"
CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


def _maximum_run(sequence: str, residues: frozenset[str] | None = None) -> int:
    maximum = 0
    current = 0
    previous: str | None = None
    for residue in sequence:
        if residues is None:
            current = current + 1 if residue == previous else 1
            previous = residue
        elif residue in residues:
            current += 1
        else:
            current = 0
        maximum = max(maximum, current)
    return maximum


def assess_synthesis_feasibility(
    sequence: str, *, ruleset: Mapping[str, Any]
) -> dict[str, Any]:
    """Return transparent sequence-only SPPS review flags.

    This is deliberately not a yield, purity, solubility, activity, or safety predictor. It never
    rejects a candidate: an in-domain sequence is either routine under the frozen heuristic or sent
    to a synthesis chemist for manual review.
    """

    if ruleset.get("schema_version") != SYNTHESIS_RULESET_SCHEMA:
        raise ValueError("synthesis feasibility ruleset schema is invalid")
    normalized = "".join(sequence.split()).upper()
    if not normalized:
        raise ValueError("peptide sequence cannot be empty")
    invalid = sorted(set(normalized) - CANONICAL_AMINO_ACIDS)
    if invalid:
        raise ValueError(f"peptide contains non-canonical amino acids: {''.join(invalid)}")
    applicability = ruleset.get("applicability_domain")
    thresholds = ruleset.get("review_thresholds")
    if not isinstance(applicability, Mapping) or not isinstance(thresholds, Mapping):
        raise ValueError("synthesis feasibility ruleset is incomplete")

    minimum_length = int(applicability["minimum_length"])
    maximum_length = int(applicability["maximum_length"])
    in_domain = minimum_length <= len(normalized) <= maximum_length
    hydrophobic = frozenset(str(thresholds["hydrophobic_alphabet"]))
    hydrophobic_count = sum(residue in hydrophobic for residue in normalized)
    hydrophobic_fraction = hydrophobic_count / len(normalized)
    maximum_hydrophobic_run = _maximum_run(normalized, hydrophobic)
    maximum_identical_run = _maximum_run(normalized)
    oxidation_count = sum(
        normalized.count(residue) for residue in str(thresholds["oxidation_sensitive_residues"])
    )
    aspartimide_motifs = tuple(str(item) for item in thresholds["aspartimide_review_motifs"])

    flags: list[str] = []
    if maximum_identical_run >= int(thresholds["identical_run_review_at"]):
        flags.append("long_identical_residue_run")
    if maximum_hydrophobic_run >= int(thresholds["hydrophobic_run_review_at"]):
        flags.append("long_hydrophobic_run")
    if hydrophobic_fraction >= float(thresholds["hydrophobic_fraction_review_at"]):
        flags.append("high_hydrophobic_fraction")
    if normalized.count("C") >= int(thresholds["cysteine_count_review_at"]):
        flags.append("multiple_cysteines_require_disulfide_strategy")
    if oxidation_count >= int(thresholds["oxidation_sensitive_count_review_at"]):
        flags.append("multiple_oxidation_sensitive_residues")
    if any(motif in normalized for motif in aspartimide_motifs):
        flags.append("aspartimide_susceptible_motif")
    if normalized.startswith("Q"):
        flags.append("n_terminal_glutamine_pyroglutamate_review")

    if not in_domain:
        status = "out_of_domain_manual_review_required"
    elif flags:
        status = "manual_review_required"
    else:
        status = "routine_sequence_only_spps"
    return {
        "schema_version": "ampgent.synthesis-feasibility-assessment.1",
        "sequence_sha256": sha256_text(normalized),
        "ruleset_sha256": sha256_json(ruleset),
        "sequence_length": len(normalized),
        "in_applicability_domain": in_domain,
        "status": status,
        "review_flags": flags,
        "observations": {
            "hydrophobic_fraction": hydrophobic_fraction,
            "maximum_hydrophobic_run": maximum_hydrophobic_run,
            "maximum_identical_residue_run": maximum_identical_run,
            "cysteine_count": normalized.count("C"),
            "oxidation_sensitive_residue_count": oxidation_count,
        },
        "limitations": list(ruleset["limitations"]),
    }
