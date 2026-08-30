from __future__ import annotations

from pepagent.db import session as db_session
from pepagent.settings import Settings


def test_remote_postgres_pool_defaults_are_bounded() -> None:
    fields = Settings.model_fields
    assert fields["database_pool_recycle_seconds"].default == 300
    assert fields["database_pool_timeout_seconds"].default == 30.0
    assert fields["database_connect_timeout_seconds"].default == 15.0
    assert fields["database_command_timeout_seconds"].default == 60.0


def test_scientific_pool_uses_bounded_tunnel_connections() -> None:
    assert db_session.engine.pool._recycle == int(  # noqa: SLF001
        db_session.settings.database_pool_recycle_seconds
    )
    assert db_session.engine.pool._timeout == float(  # noqa: SLF001
        db_session.settings.database_pool_timeout_seconds
    )
    assert db_session._database_connect_args() == {
        "timeout": float(db_session.settings.database_connect_timeout_seconds),
        "command_timeout": float(db_session.settings.database_command_timeout_seconds)
    }
