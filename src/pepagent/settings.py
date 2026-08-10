from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PEPAGENT_", extra="ignore")

    database_url: str = "postgresql+asyncpg://pepagent:change-me@localhost:55432/pepagent"
    database_url_sync: str = "postgresql+psycopg://pepagent:change-me@localhost:55432/pepagent"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "pepagent"
    s3_secret_key: str = "change-me-now"
    s3_bucket: str = "pepagent-artifacts"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    mlflow_tracking_uri: str = "http://localhost:5000"
    environment: str = "development"
    log_level: str = "INFO"
    worker_role: str = "control"
    worker_source_revision: str = "unknown"
    worker_max_concurrent_activities: int = 1
    work_root: str = "./var/work"
    metric_adapter_registry_path: str = "./config/metrics/runtime.local.yaml"
    pepmlm_model_path: str = "ChatterjeeLab/PepMLM-650M"
    pepmlm_model_revision: str = "898fca941a9057aebdd1a6164b5ee09a1a71780e"
    pepmlm_weights_sha256: str = (
        "8a3225bca1f9acd9f701ca2e46597c12bab92320e32b68f380ddf3b6d3b20770"
    )
    boltz2_revision: str = "b1ebfc46ecf57f5414e0d1a6f9027bbb122c53bc"
    boltz2_weights_sha256: str = (
        "090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1"
    )
    boltz2_cache_path: str = "./var/models/boltz2"
    pyrosetta_release: str = "2026.29+releasequarterly.80a0635615"
    pyrosetta_wheel_sha256: str = (
        "25254a10363eb5bdc0e1f3f36cbf846cb513958281041dd2b1b259610de2e733"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
