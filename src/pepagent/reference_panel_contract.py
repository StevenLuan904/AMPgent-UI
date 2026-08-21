from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReferencePanelQualification:
    schema_version: str
    panel_kind: str
    qualified_for_measurement_acquisition: bool
    entry_count: int
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualify_reference_panel(payload: Mapping[str, Any]) -> ReferencePanelQualification:
    """Validate a frozen endpoint panel without treating planned assays as measurements."""

    panel_kind = str(payload.get("panel_kind", "")).strip()
    entries = payload.get("entries")
    if panel_kind not in {"mammalian_cytotoxicity", "skin_commensal_counter_screen"}:
        raise ValueError("reference panel kind is invalid")
    if not isinstance(entries, list) or not entries:
        raise ValueError("reference panel entries must be a non-empty list")

    blockers: list[str] = []
    seen_catalogs: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"reference panel entry {index} must be an object")
        catalog_id = str(entry.get("catalog_id", "")).strip()
        source_uri = str(entry.get("source_uri", "")).strip()
        if not catalog_id:
            blockers.append(f"entry_{index}:catalog_id_missing")
        elif catalog_id in seen_catalogs:
            blockers.append(f"entry_{index}:duplicate_catalog_id")
        else:
            seen_catalogs.add(catalog_id)
        if not source_uri.startswith("https://"):
            blockers.append(f"entry_{index}:immutable_source_identity_missing")

        if panel_kind == "mammalian_cytotoxicity":
            if entry.get("organism") != "Homo sapiens":
                blockers.append(f"entry_{index}:human_identity_missing")
            if entry.get("tissue") not in {"skin_epidermis", "skin_dermis"}:
                blockers.append(f"entry_{index}:skin_tissue_identity_missing")
            if entry.get("normal_primary_cells") is not True:
                blockers.append(f"entry_{index}:normal_primary_cell_requirement_failed")
            endpoints = set(entry.get("orthogonal_endpoints", []))
            if not {"atp_viability", "ldh_membrane_integrity"}.issubset(endpoints):
                blockers.append(f"entry_{index}:orthogonal_cell_health_endpoints_missing")
        else:
            required = {"strain_designation", "isolation_source", "medium", "atmosphere"}
            missing = sorted(field for field in required if not str(entry.get(field, "")).strip())
            if missing:
                blockers.append(f"entry_{index}:missing_" + "_and_".join(missing))

    assay = payload.get("assay_contract")
    if not isinstance(assay, Mapping):
        blockers.append("assay_contract_missing")
    else:
        if assay.get("candidate_batch_threshold_fit_forbidden") is not True:
            blockers.append("current_candidate_threshold_fit_not_forbidden")
        if assay.get("raw_measurements_and_controls_required") is not True:
            blockers.append("raw_measurements_and_controls_not_required")
        if panel_kind == "mammalian_cytotoxicity":
            required_fields = set(assay.get("required_observation_fields", []))
            required = {"concentration_um", "exposure_hours", "replicate", "raw_signal"}
            if not required.issubset(required_fields):
                blockers.append("cell_assay_observation_context_incomplete")
        elif assay.get("protocol_family") not in {"CLSI_M07", "CLSI_M11"}:
            blockers.append("recognized_ast_protocol_family_missing")

    return ReferencePanelQualification(
        schema_version="ampgent.reference-panel-qualification.1",
        panel_kind=panel_kind,
        qualified_for_measurement_acquisition=not blockers,
        entry_count=len(entries),
        blockers=tuple(blockers),
    )
