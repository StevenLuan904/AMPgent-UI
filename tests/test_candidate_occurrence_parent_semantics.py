import uuid
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config

from pepagent.db.repository import (
    _candidate_occurrence_identity_matches,
    _validate_occurrence_parent_semantics,
    _validate_occurrence_run_semantics,
)


def test_de_novo_occurrence_requires_no_parent() -> None:
    _validate_occurrence_parent_semantics("de_novo", None)
    with pytest.raises(ValueError, match="cannot declare a parent"):
        _validate_occurrence_parent_semantics("de_novo", uuid.uuid4())


@pytest.mark.parametrize("kind", ["raw", "mutation", "analogue"])
def test_parent_derived_occurrence_requires_parent(kind: str) -> None:
    _validate_occurrence_parent_semantics(kind, uuid.uuid4())
    with pytest.raises(ValueError, match="must have de_novo kind"):
        _validate_occurrence_parent_semantics(kind, None)


def test_migration_downgrade_is_fail_closed_for_parentless_rows() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "migrations" / "versions" / "0012_de_novo_candidate_occurrences.py"
    ).read_text(encoding="utf-8")
    assert "IF EXISTS" in source
    assert "parent_candidate_id IS NULL" in source
    assert "RAISE EXCEPTION" in source


def test_migration_widens_alembic_revision_column_before_long_revision() -> None:
    root = Path(__file__).resolve().parents[1]
    output = StringIO()
    config = Config(str(root / "alembic.ini"), output_buffer=output)

    command.upgrade(
        config,
        "0008_research_experience_views:0013_formal_submission_exact_once",
        sql=True,
    )

    sql = output.getvalue()
    widen = "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
    first_long_revision = (
        "UPDATE alembic_version SET "
        "version_num='0011_target_qualification_lineage'"
    )
    assert widen in sql
    assert first_long_revision in sql
    assert sql.index(widen) < sql.index(first_long_revision)


def test_occurrence_parent_and_materialization_must_share_run() -> None:
    run_id = uuid.uuid4()
    _validate_occurrence_run_semantics(
        run_id,
        parent=SimpleNamespace(run_id=run_id),  # type: ignore[arg-type]
        candidate=SimpleNamespace(run_id=run_id),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="parent is cross-run"):
        _validate_occurrence_run_semantics(
            run_id,
            parent=SimpleNamespace(run_id=uuid.uuid4()),  # type: ignore[arg-type]
            candidate=None,
        )
    with pytest.raises(ValueError, match="materialization is cross-run"):
        _validate_occurrence_run_semantics(
            run_id,
            parent=None,
            candidate=SimpleNamespace(run_id=uuid.uuid4()),  # type: ignore[arg-type]
        )


def test_occurrence_retry_requires_exact_identity() -> None:
    identity = {
        "occurrence_kind": "de_novo",
        "occurrence_rank": 1,
        "sequence": "KRW",
        "metadata_json": {"disposition": "retained"},
    }
    existing = SimpleNamespace(**identity)
    assert _candidate_occurrence_identity_matches(existing, identity)  # type: ignore[arg-type]
    drifted = {**identity, "metadata_json": {"disposition": "discarded"}}
    assert not _candidate_occurrence_identity_matches(existing, drifted)  # type: ignore[arg-type]
