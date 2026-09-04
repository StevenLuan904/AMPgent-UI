from pathlib import Path

from pepagent.db.models import (
    Candidate,
    Skill,
    SkillCampaignComparison,
    SkillEvidence,
    SkillExperiment,
)

ROOT = Path(__file__).parents[1]


def test_orm_contains_restored_skill_evolution_schema_and_index() -> None:
    expected_tables = {
        "skills",
        "skill_experiments",
        "skill_campaign_comparisons",
        "skill_evidence",
    }
    assert expected_tables <= {
        Skill.__tablename__,
        SkillExperiment.__tablename__,
        SkillCampaignComparison.__tablename__,
        SkillEvidence.__tablename__,
    }
    assert {"skill_key", "revision", "compiled_ir_json", "family_fingerprint"} <= {
        column.name for column in Skill.__table__.columns
    }
    assert {"skill_revision_id", "phase", "denominator_complete", "replay_verified"} <= {
        column.name for column in SkillExperiment.__table__.columns
    }
    assert {"experiment_id", "comparison_sha256", "replay_verified"} <= {
        column.name for column in SkillCampaignComparison.__table__.columns
    }
    assert {"skill_revision_id", "evidence_grade", "promotion_eligible", "evidence_sha256"} <= {
        column.name for column in SkillEvidence.__table__.columns
    }
    indexes = {index.name: index for index in Candidate.__table__.indexes}
    assert [column.name for column in indexes["ix_candidate_sequence_sha256"].columns] == [
        "sequence_sha256"
    ]
    assert indexes["ix_candidate_sequence_sha256"].unique is False


def test_migration_chain_and_concurrent_index_contract() -> None:
    migration_0018 = (
        ROOT / "migrations" / "versions" / "0018_skill_evolution.py"
    ).read_text(encoding="utf-8")
    migration_0019 = (
        ROOT / "migrations" / "versions" / "0019_merge_shadow_skill_evolution.py"
    ).read_text(encoding="utf-8")
    migration_0020 = (
        ROOT / "migrations" / "versions" / "0020_candidate_sequence_sha256_index.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "0018_skill_evolution"' in migration_0018
    assert 'down_revision = "0017_artifact_location_witnesses"' in migration_0018
    assert 'revision = "0019_merge_shadow_skill_evolution"' in migration_0019
    assert 'down_revision = ("0018_shadow_challenger_evidence", "0018_skill_evolution")' in (
        migration_0019
    )
    assert 'revision = "0020_candidate_sequence_sha256_index"' in migration_0020
    assert 'down_revision = "0019_merge_shadow_skill_evolution"' in migration_0020
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in migration_0020
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in migration_0020
    assert "autocommit_block" in migration_0020
