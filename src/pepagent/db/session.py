from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from pepagent.settings import get_settings

settings = get_settings()
engine = create_async_engine(
    settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Observer telemetry is best-effort and is deliberately isolated from the
# scientific transaction pool.  A cancelled observer connect/rollback must
# never retain a checkout or pool mutex needed by a formal activity.  NullPool
# gives every observer write a short-lived connection and leaves no shared
# checkout state behind for SessionFactory consumers.
observer_engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
)
ObserverSessionFactory = async_sessionmaker(
    observer_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
