import asyncio

from pepagent.db.base import Base
from pepagent.db.session import engine


async def create_schema() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def main() -> None:
    asyncio.run(create_schema())


if __name__ == "__main__":
    main()

