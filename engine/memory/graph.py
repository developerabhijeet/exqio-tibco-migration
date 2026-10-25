"""``MemoryGraph``: the async Neo4j connection of the memory, its schema and test reset."""
from __future__ import annotations

import os

from neo4j import AsyncDriver, AsyncGraphDatabase

from engine.memory import cypher


class MemoryUnavailable(Exception):
    """Neo4j cannot be reached or refused the request; the message is the exception class name."""


class MemoryGraph:
    def __init__(self, driver: AsyncDriver, uri: str, database: str, exact_scan_max: int) -> None:
        self._driver = driver
        self.uri = uri
        self.database = database
        self.exact_scan_max = exact_scan_max

    @classmethod
    def connect(cls, uri: str, user: str, password: str, database: str = "neo4j", *,
                exact_scan_max: int = 50000) -> MemoryGraph:
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password), connection_timeout=5)
        return cls(driver, uri, database, exact_scan_max)

    async def close(self) -> None:
        await self._driver.close()

    async def ping(self) -> None:
        try:
            async with self._driver.session(database=self.database) as session:
                await (await session.run(cypher.PING)).consume()
        except Exception as exc:  # callers only need "unavailable" and the class
            raise MemoryUnavailable(f"{type(exc).__name__}") from exc

    async def ensure_schema(self) -> None:
        async with self._driver.session(database=self.database) as session:
            for statement in cypher.SCHEMA:
                await (await session.run(statement)).consume()

    async def reset(self) -> None:
        if not self.uri or self.uri != os.environ.get("NEO4J_TEST_URI"):
            raise ValueError("refusing to reset a Neo4j that is not NEO4J_TEST_URI")
        async with self._driver.session(database=self.database) as session:
            await (await session.run(cypher.RESET)).consume()
