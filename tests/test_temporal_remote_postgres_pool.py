from __future__ import annotations

from pathlib import Path


def test_temporal_remote_postgres_pool_is_bounded_and_recycled() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    expected = {
        "SQL_MAX_CONNS": "${PEPAGENT_TEMPORAL_SQL_MAX_CONNS:-12}",
        "SQL_MAX_IDLE_CONNS": "${PEPAGENT_TEMPORAL_SQL_MAX_IDLE_CONNS:-4}",
        "SQL_MAX_CONN_TIME": "${PEPAGENT_TEMPORAL_SQL_MAX_CONN_TIME:-10m}",
        "SQL_VIS_MAX_CONNS": "${PEPAGENT_TEMPORAL_SQL_VIS_MAX_CONNS:-4}",
        "SQL_VIS_MAX_IDLE_CONNS": "${PEPAGENT_TEMPORAL_SQL_VIS_MAX_IDLE_CONNS:-2}",
        "SQL_VIS_MAX_CONN_TIME": "${PEPAGENT_TEMPORAL_SQL_VIS_MAX_CONN_TIME:-10m}",
    }
    for key, value in expected.items():
        assert f"{key}: {value}" in compose

    assert "POSTGRES_SEEDS: ${PEPAGENT_POSTGRES_SEEDS:-host.docker.internal}" in compose
    assert "docker compose restart" not in compose
