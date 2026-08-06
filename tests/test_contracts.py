import hashlib

import pytest
import yaml

from pepagent.domain.schemas import (
    ExperimentSpec,
    InitialParentSpec,
    PocketCatalogSpec,
    TargetSpec,
)
from pepagent.model_workers.boltz2_cli import build_input
from pepagent.provenance.environment import runtime_manifest
from pepagent.provenance.hashing import sha256_json
from pepagent.workers.activities import _boltz_weight_manifest
from pepagent.workers.temporal_worker import ROLE_CONFIG
from pepagent.workflows.design import seed_parent_payloads


def test_spec_normalizes_sequence_and_hash_is_canonical() -> None:
    spec = ExperimentSpec(target=TargetSpec(name="target", sequence="ac dE"))
    assert spec.target.sequence == "ACDE"
    assert spec.boltz_no_kernels is True
    left = sha256_json({"b": 2, "a": 1})
    right = sha256_json({"a": 1, "b": 2})
    assert left == right


def test_spec_rejects_noncanonical_residues() -> None:
    with pytest.raises(ValueError, match="invalid amino-acid"):
        TargetSpec(name="target", sequence="ACDX")


def test_initial_parent_requires_exact_sequence_and_evidence_hashes() -> None:
    with pytest.raises(ValueError, match="does not match"):
        InitialParentSpec(
            source_run_id="00000000-0000-0000-0000-000000000001",
            source_candidate_id="00000000-0000-0000-0000-000000000002",
            sequence="KLLK",
            sequence_sha256="0" * 64,
            evidence_sha256=["1" * 64],
            selection_rationale="activity lead",
        )


def test_seed_parent_payload_preserves_cross_run_provenance() -> None:
    sequence = "KLLK"
    digest = hashlib.sha256(sequence.encode()).hexdigest()
    spec = ExperimentSpec(
        target=TargetSpec(name="target", sequence="ACDE"),
        initial_parents=[
            InitialParentSpec(
                source_run_id="00000000-0000-0000-0000-000000000001",
                source_candidate_id="00000000-0000-0000-0000-000000000002",
                sequence=sequence,
                sequence_sha256=digest,
                evidence_sha256=["a" * 64],
                selection_rationale="structure lead",
            )
        ],
    )
    parent = seed_parent_payloads(spec.model_dump(mode="json"))[0]
    assert parent["id"] == "00000000-0000-0000-0000-000000000002"
    assert parent["source_run_id"] == "00000000-0000-0000-0000-000000000001"
    assert parent["evidence_sha256"] == ["a" * 64]


def test_boltz_input_represents_peptide_as_protein_chain_without_affinity() -> None:
    payload = build_input(
        {"target_sequence": "ACDE", "peptide_sequence": "KLLK", "pocket_residues": [2, 4]}
    )
    assert payload["sequences"][1] == {
        "protein": {"id": "B", "sequence": "KLLK", "msa": "empty"}
    }
    assert "ligand" not in str(payload).lower()
    assert payload["constraints"][0]["pocket"]["contacts"] == [["A", 2], ["A", 4]]


def test_boltz_input_can_make_blind_single_sequence_run_explicit() -> None:
    payload = build_input(
        {
            "target_sequence": "ACDE",
            "peptide_sequence": "KLLK",
            "use_msa_server": False,
        }
    )
    assert payload["sequences"][0]["protein"]["msa"] == "empty"
    assert payload["sequences"][1]["protein"]["msa"] == "empty"
    assert "constraints" not in payload


def test_peppap_is_frozen_out_of_active_contract() -> None:
    spec = ExperimentSpec(target=TargetSpec(name="target", sequence="ACDE"))
    assert spec.affinity_evaluators == []
    assert "affinity" not in ROLE_CONFIG
    with pytest.raises(ValueError, match="PepPAP is frozen"):
        ExperimentSpec(
            target=TargetSpec(name="target", sequence="ACDE"),
            affinity_evaluators=["peppap"],
        )


def test_decision_bearing_rosetta_requires_200_decoys() -> None:
    with pytest.raises(ValueError, match="at least 200"):
        ExperimentSpec(
            target=TargetSpec(name="target", sequence="ACDE"),
            structure_top_k=1,
            rosetta_enabled=True,
            rosetta_nstruct=20,
        )


def test_runtime_manifest_records_exact_platform_release(monkeypatch: pytest.MonkeyPatch) -> None:
    release = "a" * 64
    monkeypatch.setenv("PEPAGENT_PLATFORM_RELEASE_SHA256", release)
    assert runtime_manifest()["application"]["release_sha256"] == release


def test_boltz_execution_manifest_includes_molecular_resources(tmp_path) -> None:
    (tmp_path / "boltz2_conf.ckpt").write_bytes(b"w" * (1024 * 1024))
    (tmp_path / "mols.tar").write_bytes(b"molecular-resources")
    manifest = _boltz_weight_manifest(str(tmp_path))
    assert [(item["path"], item["role"]) for item in manifest] == [
        ("boltz2_conf.ckpt", "weights"),
        ("mols.tar", "molecular_resource_archive"),
    ]


def test_mvp_v2_pocket_catalog_is_versioned_and_role_aware() -> None:
    with open("config/pockets/mvp_v2_pocket_catalog.yaml", encoding="utf-8") as stream:
        catalog = PocketCatalogSpec.model_validate(yaml.safe_load(stream))

    assert catalog.catalog_version == "2026-07-31.1"
    assert len(catalog.targets) == 6
    targets = {target.accession: target for target in catalog.targets}
    assert targets["P0A9G6"].pockets[0].conditioning_priority == "primary"
    pbp_pockets = {pocket.key: pocket for pocket in targets["WP_308061015.1"].pockets}
    assert 368 in pbp_pockets["transpeptidase_active_site"].residue_indices
    assert pbp_pockets["ceftaroline_muramate_allosteric_site"].conditioning_enabled is True
    for accession in ("NP_001020421.2", "NP_032032.1", "NP_001272991.1"):
        assert targets[accession].role == "healing_payload"
        assert all(not pocket.conditioning_enabled for pocket in targets[accession].pockets)
