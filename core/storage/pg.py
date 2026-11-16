"""Conexion Postgres compartida por los almacenes.

Una conexion asincrona con autocommit y un lock: suficiente para un sujeto
y un proceso. Si la conexion se cae, la siguiente operacion reconecta una
vez. Las transacciones explicitas (claim de jobs) usan
`transaction()`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, dsn: str, *, schema: str = "motor") -> None:
        self._dsn = dsn
        self._schema = schema
        self._conn: psycopg.AsyncConnection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True, row_factory=dict_row)
        await self._conn.execute(f"set search_path to {self._schema}, public")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _ensure(self) -> psycopg.AsyncConnection:
        if self._conn is None or self._conn.closed:
            await self.connect()
        assert self._conn is not None
        return self._conn

    @asynccontextmanager
    async def cursor(self) -> AsyncIterator[psycopg.AsyncCursor]:
        async with self._lock:
            conn = await self._ensure()
            try:
                async with conn.cursor() as cur:
                    yield cur
            except psycopg.OperationalError:
                await self.close()
                raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[psycopg.AsyncCursor]:
        async with self._lock:
            conn = await self._ensure()
            try:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        yield cur
            except psycopg.OperationalError:
                await self.close()
                raise
