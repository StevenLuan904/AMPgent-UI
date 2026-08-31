from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from pepagent.settings import get_settings

settings = get_settings()


def _database_connect_args() -> dict[str, float]:
    return {
        "timeout": float(settings.database_connect_timeout_seconds),
        "command_timeout": float(settings.database_command_timeout_seconds),
    }


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=int(settings.database_pool_size),
    max_overflow=int(settings.database_max_overflow),
    pool_recycle=int(settings.database_pool_recycle_seconds),
    pool_timeout=float(settings.database_pool_timeout_seconds),
    connect_args=_database_connect_args(),
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Observer progress telemetry is best-effort, while activity boundary events
# are authoritative and finitely retried by the interceptor.  Both use this
# engine so a cancelled observer connect/rollback can never retain a checkout
# or pool mutex needed by a scientific activity.  NullPool gives every audit
# write a short-lived connection and leaves no shared checkout state behind for
# SessionFactory consumers.
observer_engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args=_database_connect_args(),
)
ObserverSessionFactory = async_sessionmaker(
    observer_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
