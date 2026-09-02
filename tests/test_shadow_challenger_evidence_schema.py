from pepagent.db.models import Evaluation


def test_evaluation_exposes_queryable_shadow_challenger_dimensions() -> None:
    columns = Evaluation.__table__.columns
    expected = {
        "subject_run_id",
        "evidence_role",
        "evidence_family",
        "model_release_key",
        "applicability_status",
        "conflict_status",
    }
    assert expected <= set(columns.keys())
    assert columns.subject_run_id.nullable is True
    assert columns.evidence_role.nullable is True
    assert columns.model_release_key.nullable is True


def test_shadow_challenger_lookup_index_uses_exact_subject_identity() -> None:
    indexes = {index.name: index for index in Evaluation.__table__.indexes}
    index = indexes["ix_evaluation_shadow_challenger_lookup"]
    assert [column.name for column in index.columns] == [
        "subject_run_id",
        "candidate_id",
        "evidence_role",
        "model_release_key",
    ]
