from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from pepagent.enterprise_pipeline import (
    assert_enterprise_pipeline_contract,
    audit_target_identity,
)
from pepagent.workflows.v38_sequence_first import V38SequenceFirstAgentWorkflow

ROOT = Path(__file__).resolve().parents[1]


def test_enterprise_audit_contract_is_machine_valid() -> None:
    payload = yaml.safe_load(
        (ROOT / "config/enterprise/ampgent_core_pipeline_v39_audit.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert_enterprise_pipeline_contract(payload)
    assert payload["readiness"]["status"] == "not_ready"
    assert payload["scope"]["formal_science_run_authorized"] is False


def test_direct_structure_rejects_cross_species_metadata_even_for_matching_sequence() -> None:
    target = "AIEDKNFKQVYKDSSYISKSDNGEVEMTERPIKIYNSLGVKDINIQDRKIKKV"
    coordinate = "DKEINNTID" + target
    audit = audit_target_identity(
        target_sequence=target,
        coordinate_chain_sequence=coordinate,
        registered_organism="Staphylococcus epidermidis",
        coordinate_organism="Staphylococcus aureus subsp. aureus Mu50",
        registered_accession="WP_308061015.1",
        coordinate_polymer_accession="A0A0H3JPA5",
    )
    assert audit.target_sequence_coverage == 1.0
    assert not audit.organism_matches
    assert not audit.accession_matches
    assert not audit.accepted
    assert len(audit.findings) == 2


def test_direct_structure_accepts_matching_identity() -> None:
    audit = audit_target_identity(
        target_sequence="AIEDKNFKQVYKDSSY",
        coordinate_chain_sequence="AIEDKNFKQVYKDSSY",
        registered_organism="Escherichia coli",
        coordinate_organism="Escherichia coli",
        registered_accession="P0AES4",
        coordinate_polymer_accession="P0AES4",
    )
    assert audit.accepted
    assert audit.sequence_identity_fraction == 1.0
    assert audit.target_sequence_coverage == 1.0


def test_v38_workflow_reconciles_temporal_cancellation_to_durable_run() -> None:
    source = inspect.getsource(V38SequenceFirstAgentWorkflow.run)
    assert "except asyncio.CancelledError" in source
    assert '"mark_run_cancelled"' in source
    assert "workflow_cancelled_before_scientific_completion" in source
