import importlib.util
import json
from pathlib import Path

from pepagent.db import models  # noqa: F401
from pepagent.db.base import Base

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "manage_run_aggregation", ROOT / "analysis/manage_run_aggregation.py"
)
assert SPEC is not None and SPEC.loader is not None
aggregation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aggregation)


def test_frozen_phase_order_is_scientific_not_temporal() -> None:
    assert aggregation.PHASE_ORDER == {
        "generation": 10,
        "score_all": 20,
        "challenger": 30,
        "qd_lineage": 40,
        "boltz": 50,
        "rosetta": 60,
        "md": 70,
        "pool_s": 80,
        "unclassified": 900,
    }


def test_contract_forbids_heuristic_run_merges() -> None:
    contract = json.loads(
        (ROOT / "config/contracts/ampgent_run_aggregation_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["historical_policy"]["scientific_rows_are_immutable"] is True
    assert contract["historical_policy"]["failed_is_invalidated"] is False
    assert set(contract["identity_rules"]["forbidden_inference"]) == {
        "title",
        "target",
        "sequence",
        "created_at",
        "storage_uri",
    }


def test_models_expose_explicit_group_attachment_and_invalidation_schema() -> None:
    expected_tables = {
        "scientific_roots",
        "scientific_run_groups",
        "run_lineage_edges",
        "run_evidence_attachments",
        "run_invalidation_events",
        "evidence_invalidation_events",
    }
    assert expected_tables <= set(Base.metadata.tables)
    run_columns = Base.metadata.tables["experiment_runs"].c
    assert run_columns.run_group_id.nullable is True
    assert run_columns.root_run_id.nullable is True
    assert run_columns.phase_code.nullable is True
    assert run_columns.phase_ordinal.nullable is True


def test_migration_is_expand_only_and_views_keep_subject_and_producer_separate() -> None:
    source = (ROOT / "migrations/versions/0022_run_aggregation.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE VIEW scientific_run_timeline_v1" in source
    assert "CREATE VIEW scientific_run_lineage_v1" in source
    assert "CREATE VIEW scientific_operational_evidence_v1" in source
    assert "CREATE VIEW scientific_family_aggregate_v1" in source
    for count_name in (
        "candidate_count",
        "score_all_evidence_count",
        "challenger_evidence_count",
        "rosetta_evidence_count",
        "md_evidence_count",
    ):
        assert count_name in source
    assert "CREATE VIEW scientific_evidence_mismatches_v1" in source
    assert "CREATE VIEW scientific_evidence_validity_v1" in source
    assert "subject_run_id" in source
    assert "producer_run_id" in source
    assert "UPDATE candidates" not in source
    assert "UPDATE evaluations" not in source
    assert "ORDER BY e.created_at" not in source
    assert "aggregation evidence is append-only" in source


def test_run_edge_identity_is_deterministic() -> None:
    identity = {
        "parent_run_id": "11111111-1111-4111-8111-111111111111",
        "child_run_id": "22222222-2222-4222-8222-222222222222",
        "relation_type": "parent_run",
        "relation_ordinal": 1,
        "binding_basis": "experiment_runs.parent_run_id",
    }
    digest = aggregation._sha(identity)
    assert digest == aggregation._sha(dict(reversed(list(identity.items()))))
    assert aggregation._uuid5("run-edge", digest) == aggregation._uuid5(
        "run-edge", digest
    )
