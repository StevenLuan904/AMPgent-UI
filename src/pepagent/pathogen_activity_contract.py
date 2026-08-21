from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PathogenActivityQualification:
    schema_version: str
    qualified_for_dataset_acquisition: bool
    strain_count: int
    assay_profile_count: int
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualify_pathogen_activity_reference(
    *,
    assay_table: Mapping[str, Any],
    normalization_contract: Mapping[str, Any],
    split_witness: Mapping[str, Any],
) -> PathogenActivityQualification:
    """Validate the pathogen-conditioned MIC acquisition contract.

    This validator qualifies identities and future data-collection semantics. It intentionally
    cannot turn an empty reference contract into a calibrated potency model or a candidate gate.
    """

    blockers: list[str] = []
    if assay_table.get("panel_kind") != "pathogen_conditioned_potency":
        raise ValueError("pathogen activity panel kind is invalid")
    entries = assay_table.get("entries")
    profiles = assay_table.get("assay_profiles")
    if not isinstance(entries, list) or not entries:
        raise ValueError("pathogen activity entries must be a non-empty list")
    if not isinstance(profiles, Mapping) or not profiles:
        raise ValueError("pathogen activity assay profiles must be a non-empty object")

    seen_catalogs: set[str] = set()
    covered_roles: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"pathogen activity entry {index} must be an object")
        catalog_id = str(entry.get("catalog_id", "")).strip()
        if not catalog_id:
            blockers.append(f"entry_{index}:catalog_id_missing")
        elif catalog_id in seen_catalogs:
            blockers.append(f"entry_{index}:duplicate_catalog_id")
        else:
            seen_catalogs.add(catalog_id)
        for field in ("organism", "strain_designation", "isolation_source"):
            if not str(entry.get(field, "")).strip():
                blockers.append(f"entry_{index}:{field}_missing")
        if not str(entry.get("source_uri", "")).startswith("https://"):
            blockers.append(f"entry_{index}:catalog_source_identity_missing")
        profile_id = str(entry.get("assay_profile_id", "")).strip()
        if profile_id not in profiles:
            blockers.append(f"entry_{index}:assay_profile_missing")
        roles = entry.get("panel_roles")
        if not isinstance(roles, list) or not roles:
            blockers.append(f"entry_{index}:panel_roles_missing")
        else:
            covered_roles.update(str(role) for role in roles)

    required_roles = {
        "gram_positive_reference",
        "methicillin_resistant_staphylococcus",
        "biofilm_associated_coagulase_negative_staphylococcus",
        "gram_negative_reference",
        "nonfermenting_gram_negative_reference",
    }
    if not required_roles.issubset(covered_roles):
        blockers.append("minimum_skin_wound_pathogen_roles_missing")

    for profile_id, profile in profiles.items():
        if not isinstance(profile, Mapping):
            raise ValueError(f"assay profile {profile_id} must be an object")
        required = {
            "protocol_family",
            "protocol_edition",
            "method",
            "assay_medium",
            "final_inoculum_cfu_ml",
            "incubation_temperature_c",
            "incubation_hours",
            "atmosphere",
            "endpoint",
        }
        missing = sorted(field for field in required if profile.get(field) in (None, "", []))
        if missing:
            blockers.append(f"profile_{profile_id}:missing_" + "_and_".join(missing))
        if profile.get("protocol_family") != "CLSI_M07":
            blockers.append(f"profile_{profile_id}:unsupported_protocol_family")
        controls = set(profile.get("required_controls", []))
        if not {"growth_control", "sterility_control", "reference_qc_control"}.issubset(
            controls
        ):
            blockers.append(f"profile_{profile_id}:required_controls_missing")

    required_fields = set(normalization_contract.get("required_raw_fields", []))
    if not {
        "candidate_sequence_sha256",
        "organism",
        "strain_designation",
        "assay_profile_id",
        "reported_mic_value",
        "reported_mic_unit",
        "mic_qualifier",
        "biological_replicate",
    }.issubset(required_fields):
        blockers.append("normalization_raw_context_incomplete")
    if normalization_contract.get("candidate_batch_threshold_fit_forbidden") is not True:
        blockers.append("current_candidate_threshold_fit_not_forbidden")
    if normalization_contract.get("cross_condition_numeric_pooling_forbidden") is not True:
        blockers.append("cross_condition_pooling_not_forbidden")
    if normalization_contract.get("raw_value_and_unit_preserved") is not True:
        blockers.append("raw_mic_not_preserved")
    conversion = normalization_contract.get("mass_to_molar_conversion")
    if not isinstance(conversion, Mapping) or conversion.get("formula") != (
        "mic_um=(mic_ug_per_ml*1000)/material_molecular_weight_g_per_mol"
    ):
        blockers.append("mass_to_molar_conversion_not_frozen")

    if split_witness.get("current_candidate_sequences_used_to_define_split") is not False:
        blockers.append("current_candidate_sequences_influenced_split")
    if split_witness.get("exact_sequence_groups_must_be_disjoint") is not True:
        blockers.append("exact_sequence_disjointness_missing")
    if split_witness.get("publication_groups_must_be_disjoint") is not True:
        blockers.append("publication_group_disjointness_missing")
    cluster = split_witness.get("sequence_family_cluster")
    if not isinstance(cluster, Mapping):
        blockers.append("sequence_family_cluster_policy_missing")
    else:
        identity = float(cluster.get("minimum_global_identity", 0.0))
        coverage = float(cluster.get("minimum_bidirectional_coverage", 0.0))
        if not (0.0 < identity < 1.0 and 0.0 < coverage <= 1.0):
            blockers.append("sequence_family_cluster_policy_invalid")
    if split_witness.get("candidate_overlap_audit_required_before_training") is not True:
        blockers.append("candidate_overlap_audit_not_required")
    if split_witness.get("dataset_measurements_present") is not False:
        blockers.append("unverified_dataset_measurement_claim")

    return PathogenActivityQualification(
        schema_version="ampgent.pathogen-activity-qualification.1",
        qualified_for_dataset_acquisition=not blockers,
        strain_count=len(entries),
        assay_profile_count=len(profiles),
        blockers=tuple(blockers),
    )
