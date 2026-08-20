from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.enterprise_pipeline import audit_target_identity
from pepagent.provenance.hashing import sha256_json, sha256_text

TARGET_IDENTITY_BUNDLE_SCHEMA = "v39.target-identity-bundle.1"
VERIFIED_TARGET_IDENTITY_SCHEMA = "v39.verified-target-identity.1"


def _require_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _normalized_sequence(value: object, *, label: str) -> str:
    sequence = "".join(_require_text(value, label=label).split()).upper()
    if not sequence or any(residue not in "ACDEFGHIKLMNPQRSTVWY" for residue in sequence):
        raise ValueError(f"{label} is not a canonical amino-acid sequence")
    return sequence


def verify_target_identity_bundle(
    *,
    bundle: Mapping[str, Any],
    panel: Mapping[str, Any],
    target_runtime_by_id: Mapping[str, Mapping[str, Any]],
    target_panel_sha256: str,
) -> dict[str, Any]:
    """Recompute target/coordinate identity before a formal workflow can be authorized.

    The input bundle is intentionally separate from the frozen target panel.  It carries the
    source metadata and coordinate-chain sequence needed to reproduce the identity audit while
    the database remains the authority for the registered target sequence.  A direct structure
    must match organism and accession; a cross-species structure is accepted only when it is
    explicitly labelled ``homology`` and still meets the frozen sequence thresholds.
    """

    if bundle.get("schema_version") != TARGET_IDENTITY_BUNDLE_SCHEMA:
        raise ValueError("target identity bundle schema is invalid")
    if bundle.get("target_panel_sha256") != target_panel_sha256:
        raise ValueError("target identity bundle is not bound to the target panel bytes")
    panel_branches = panel.get("branches")
    bundle_branches = bundle.get("branches")
    if not isinstance(panel_branches, list) or not isinstance(bundle_branches, list):
        raise ValueError("target identity bundle or panel has no branches")

    panel_by_key = {
        _require_text(branch.get("target_key"), label="panel target key"): branch
        for branch in panel_branches
        if isinstance(branch, Mapping)
    }
    bundle_by_key = {
        _require_text(branch.get("target_key"), label="identity target key"): branch
        for branch in bundle_branches
        if isinstance(branch, Mapping)
    }
    if len(panel_by_key) != len(panel_branches) or len(bundle_by_key) != len(bundle_branches):
        raise ValueError("target identity branches must be objects with unique keys")
    if set(bundle_by_key) != set(panel_by_key):
        raise ValueError("target identity bundle does not cover the exact target panel")

    verified: list[dict[str, Any]] = []
    for target_key in sorted(panel_by_key):
        panel_branch = _require_mapping(panel_by_key[target_key], label="target panel branch")
        identity_branch = _require_mapping(
            bundle_by_key[target_key], label="target identity branch"
        )
        target_id = _require_text(panel_branch.get("target_id"), label="target id")
        if identity_branch.get("target_id") != target_id:
            raise ValueError(f"target identity id drifted: {target_key}")
        runtime = _require_mapping(
            target_runtime_by_id.get(target_id), label=f"target runtime {target_key}"
        )
        target_sequence = _normalized_sequence(
            runtime.get("target_sequence"), label=f"registered sequence {target_key}"
        )
        if sha256_text(target_sequence) != panel_branch.get("target_sequence_sha256"):
            raise ValueError(f"registered target sequence drifted: {target_key}")
        if identity_branch.get("coordinate_sha256") != panel_branch.get("coordinate_sha256"):
            raise ValueError(f"coordinate identity drifted: {target_key}")

        evidence_mode = identity_branch.get("structure_evidence_mode")
        if evidence_mode not in {"direct_experimental", "homology"}:
            raise ValueError(f"structure evidence mode is invalid: {target_key}")
        coordinate_chain_sequence = _normalized_sequence(
            identity_branch.get("coordinate_chain_sequence"),
            label=f"coordinate chain sequence {target_key}",
        )
        minimum_coverage = float(identity_branch.get("minimum_target_coverage", 0.95))
        minimum_identity = float(identity_branch.get("minimum_sequence_identity", 0.90))
        if not 0 < minimum_coverage <= 1 or not 0 < minimum_identity <= 1:
            raise ValueError(f"target identity thresholds are invalid: {target_key}")

        audit = audit_target_identity(
            target_sequence=target_sequence,
            coordinate_chain_sequence=coordinate_chain_sequence,
            registered_organism=_require_text(
                identity_branch.get("registered_organism"),
                label=f"registered organism {target_key}",
            ),
            coordinate_organism=_require_text(
                identity_branch.get("coordinate_organism"),
                label=f"coordinate organism {target_key}",
            ),
            registered_accession=_require_text(
                identity_branch.get("registered_accession"),
                label=f"registered accession {target_key}",
            ),
            coordinate_polymer_accession=_require_text(
                identity_branch.get("coordinate_polymer_accession"),
                label=f"coordinate polymer accession {target_key}",
            ),
            direct_experimental_structure=evidence_mode == "direct_experimental",
            minimum_target_coverage=minimum_coverage,
            minimum_sequence_identity=minimum_identity,
        )
        if not audit.accepted:
            raise ValueError(
                f"target identity rejected for {target_key}: " + "; ".join(audit.findings)
            )
        source_artifact_sha256 = _require_text(
            identity_branch.get("source_artifact_sha256"),
            label=f"identity source artifact SHA {target_key}",
        )
        pocket_mapping_sha256 = _require_text(
            identity_branch.get("pocket_mapping_sha256"),
            label=f"pocket mapping SHA {target_key}",
        )
        for label, value in (
            ("identity source artifact", source_artifact_sha256),
            ("pocket mapping", pocket_mapping_sha256),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{label} SHA is invalid: {target_key}")
        verified.append(
            {
                "target_key": target_key,
                "target_id": target_id,
                "structure_evidence_mode": evidence_mode,
                "registered_sequence_sha256": sha256_text(target_sequence),
                "coordinate_chain_sequence_sha256": sha256_text(coordinate_chain_sequence),
                "coordinate_sha256": panel_branch["coordinate_sha256"],
                "source_artifact_sha256": source_artifact_sha256,
                "pocket_mapping_sha256": pocket_mapping_sha256,
                "sequence_identity_fraction": audit.sequence_identity_fraction,
                "target_sequence_coverage": audit.target_sequence_coverage,
                "organism_matches": audit.organism_matches,
                "accession_matches": audit.accession_matches,
                "accepted": True,
            }
        )

    witness = {
        "schema_version": VERIFIED_TARGET_IDENTITY_SCHEMA,
        "target_panel_sha256": target_panel_sha256,
        "all_branches_accepted": True,
        "branches": verified,
    }
    witness["witness_sha256"] = sha256_json(witness)
    return witness


def require_verified_target_identity_witness(
    witness: Mapping[str, Any], *, target_panel_sha256: str
) -> str:
    """Validate the compact recomputed witness at the final authorization boundary."""

    if witness.get("schema_version") != VERIFIED_TARGET_IDENTITY_SCHEMA:
        raise ValueError("verified target identity witness schema is invalid")
    if witness.get("target_panel_sha256") != target_panel_sha256:
        raise ValueError("verified target identity witness panel binding drifted")
    branches = witness.get("branches")
    if (
        witness.get("all_branches_accepted") is not True
        or not isinstance(branches, list)
        or len(branches) < 1
        or any(
            not isinstance(branch, Mapping) or branch.get("accepted") is not True
            for branch in branches
        )
    ):
        raise ValueError("verified target identity witness has rejected or absent branches")
    identity = {key: value for key, value in witness.items() if key != "witness_sha256"}
    expected_sha256 = sha256_json(identity)
    if witness.get("witness_sha256") != expected_sha256:
        raise ValueError("verified target identity witness SHA drifted")
    return expected_sha256
