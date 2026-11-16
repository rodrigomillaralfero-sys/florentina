"""Almacenes sobre Postgres.

Mismos contratos que `core.storage.memory`. La idempotencia la garantizan
las restricciones del esquema (`unique`, PK) con `on conflict do nothing`;
el codigo solo consulta lo que ya existia cuando el insert no creo nada.

Estas clases no tienen prueba automatizada en F0.1 porque el entorno de
desarrollo no trae un Postgres; la primera verificacion es
`scripts/smoke_db.py` contra la base real.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from psycopg.types.json import Jsonb

from core.events.store import AppendResult, Event, NewEvent
from core.interfaces.subject_key import SubjectKey
from core.scheduler.jobs import Job, JobStatus, NewJob
from core.mind.access import AccessEntry
from core.mind.cipher import Mind
from core.storage.pg import Database

PLAIN_MIND = Mind()


def _st(m: Mind, instance_id: str, column: str, value: Any) -> Any:
    """Cifra un texto. Sin clave, pasa en claro."""
    return m.codec.seal_text(value, aad=m.text_aad(instance_id, column))


def _ot(m: Mind, instance_id: str, column: str, value: Any) -> Any:
    """Abre un texto cifrado. Un valor en claro (legado) pasa tal cual."""
    return m.codec.open_text(value, aad=m.text_aad(instance_id, column))


def _event(row: Mapping[str, Any], m: Mind = PLAIN_MIND) -> Event:
    return Event(
        id=str(row["id"]),
        instance_id=row["instance_id"],
        subject_id=str(row["subject_id"]) if row["subject_id"] else None,
        kind=row["kind"],
        payload=m.open_event(row["instance_id"], row["kind"], row["payload"] or {}),
        idempotency_key=row["idempotency_key"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        corrects_event_id=str(row["corrects_event_id"]) if row["corrects_event_id"] else None,
    )


class PostgresEventStore:
    def __init__(self, db: Database, mind: Mind | None = None) -> None:
        self._db = db
        self._m = mind or PLAIN_MIND  # campos cifrados por tipo de evento

    async def append(self, new: NewEvent) -> AppendResult:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                insert into events (instance_id, subject_id, kind, payload, idempotency_key, occurred_at, corrects_event_id)
                values (%s, %s, %s, %s, %s, coalesce(%s, now()), %s)
                on conflict (instance_id, idempotency_key) do nothing
                returning *
                """,
                (
                    new.instance_id,
                    new.subject_id,
                    new.kind,
                    Jsonb(self._m.seal_event(new.instance_id, new.kind, new.payload)),
                    new.idempotency_key,
                    new.occurred_at,
                    new.corrects_event_id,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return AppendResult(_event(row, self._m), created=True)
            await cur.execute(
                "select * from events where instance_id = %s and idempotency_key = %s",
                (new.instance_id, new.idempotency_key),
            )
            row = await cur.fetchone()
            assert row is not None
            return AppendResult(_event(row, self._m), created=False)

    async def get(self, event_id: str) -> Event | None:
        async with self._db.cursor() as cur:
            await cur.execute("select * from events where id = %s", (event_id,))
            row = await cur.fetchone()
            return _event(row, self._m) if row else None

    async def by_key(self, instance_id: str, idempotency_key: str) -> Event | None:
        async with self._db.cursor() as cur:
            await cur.execute(
                "select * from events where instance_id = %s and idempotency_key = %s", (instance_id, idempotency_key)
            )
            row = await cur.fetchone()
            return _event(row, self._m) if row else None

    async def list(
        self,
        instance_id: str,
        *,
        subject_id: str | None = None,
        kinds: Sequence[str] | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Event]:
        clauses = ["instance_id = %s"]
        params: list[Any] = [instance_id]
        if subject_id is not None:
            clauses.append("subject_id = %s")
            params.append(subject_id)
        if kinds is not None:
            clauses.append("kind = any(%s)")
            params.append(list(kinds))
        if since is not None:
            clauses.append("occurred_at >= %s")
            params.append(since)
        sql = f"select * from events where {' and '.join(clauses)} order by occurred_at desc, recorded_at desc"
        if limit is not None:
            sql += " limit %s"
            params.append(limit)
        async with self._db.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [_event(r, self._m) for r in reversed(rows)]


def _job(row: Mapping[str, Any]) -> Job:
    return Job(
        id=str(row["id"]),
        instance_id=row["instance_id"],
        subject_id=str(row["subject_id"]) if row["subject_id"] else None,
        kind=row["kind"],
        run_at=row["run_at"],
        idempotency_key=row["idempotency_key"],
        payload=row["payload"] or {},
        status=JobStatus(row["status"]),
        attempts=row["attempts"],
        created_at=row["created_at"],
        lease_until=row["lease_until"],
        finished_at=row["finished_at"],
        result=row["result"],
        error=row["error"],
    )


class PostgresJobStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enqueue(self, new: NewJob) -> tuple[Job, bool]:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                insert into jobs (instance_id, subject_id, kind, run_at, idempotency_key, payload)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (idempotency_key) do nothing
                returning *
                """,
                (new.instance_id, new.subject_id, new.kind, new.run_at, new.idempotency_key, Jsonb(dict(new.payload))),
            )
            row = await cur.fetchone()
            if row is not None:
                return _job(row), True
            await cur.execute("select * from jobs where idempotency_key = %s", (new.idempotency_key,))
            row = await cur.fetchone()
            assert row is not None
            return _job(row), False

    async def claim_due(self, now: datetime, lease: timedelta, limit: int = 10, *, instance_id: str | None = None) -> Sequence[Job]:
        async with self._db.transaction() as cur:
            await cur.execute(
                """
                with due as (
                    select id from jobs
                    where ((status = 'pending' and run_at <= %s)
                       or (status = 'running' and lease_until is not null and lease_until <= %s))
                      and (%s::text is null or instance_id = %s::text)
                    order by run_at
                    limit %s
                    for update skip locked
                )
                update jobs j
                set status = 'running', attempts = j.attempts + 1, lease_until = %s
                from due
                where j.id = due.id
                returning j.*
                """,
                (now, now, instance_id, instance_id, limit, now + lease),
            )
            rows = await cur.fetchall()
        return [_job(r) for r in rows]

    async def complete(self, job_id: str, result: Mapping[str, Any] | None = None) -> None:
        async with self._db.cursor() as cur:
            await cur.execute(
                "update jobs set status = 'done', finished_at = now(), result = %s, lease_until = null where id = %s",
                (Jsonb(dict(result or {})), job_id),
            )

    async def fail(self, job_id: str, error: str, retry_at: datetime | None) -> None:
        async with self._db.cursor() as cur:
            if retry_at is None:
                await cur.execute(
                    "update jobs set status = 'failed', finished_at = now(), error = %s, lease_until = null where id = %s",
                    (error, job_id),
                )
            else:
                await cur.execute(
                    "update jobs set status = 'pending', run_at = %s, error = %s, lease_until = null where id = %s",
                    (retry_at, error, job_id),
                )

    async def cancel(
        self,
        instance_id: str,
        *,
        subject_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_keys: Sequence[str] = (),
    ) -> int:
        clauses = ["instance_id = %s", "status = 'pending'"]
        params: list[Any] = [instance_id]
        if subject_id is not None:
            clauses.append("subject_id = %s")
            params.append(subject_id)
        if kinds is not None:
            clauses.append("kind = any(%s)")
            params.append(list(kinds))
        if exclude_keys:
            clauses.append("idempotency_key <> all(%s)")
            params.append(list(exclude_keys))
        async with self._db.cursor() as cur:
            await cur.execute(
                f"update jobs set status = 'cancelled', finished_at = now() where {' and '.join(clauses)}", params
            )
            return cur.rowcount or 0

    async def get(self, job_id: str) -> Job | None:
        async with self._db.cursor() as cur:
            await cur.execute("select * from jobs where id = %s", (job_id,))
            row = await cur.fetchone()
            return _job(row) if row else None

    async def pending(
        self, instance_id: str, *, subject_id: str | None = None, kinds: Sequence[str] | None = None, include_running: bool = False
    ) -> Sequence[Job]:
        clauses = ["instance_id = %s", "status in ('pending','running')" if include_running else "status = 'pending'"]
        params: list[Any] = [instance_id]
        if subject_id is not None:
            clauses.append("subject_id = %s")
            params.append(subject_id)
        if kinds is not None:
            clauses.append("kind = any(%s)")
            params.append(list(kinds))
        async with self._db.cursor() as cur:
            await cur.execute(f"select * from jobs where {' and '.join(clauses)} order by run_at", params)
            rows = await cur.fetchall()
        return [_job(r) for r in rows]


class PostgresSubjectResolver:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def resolve(self, instance_id: str, subject_ref: str) -> SubjectKey:
        async with self._db.cursor() as cur:
            await cur.execute("insert into instances (id) values (%s) on conflict do nothing", (instance_id,))
            await cur.execute(
                """
                insert into subjects (instance_id, external_ref) values (%s, %s)
                on conflict (instance_id, external_ref) do nothing
                returning id
                """,
                (instance_id, subject_ref),
            )
            row = await cur.fetchone()
            if row is None:
                await cur.execute(
                    "select id from subjects where instance_id = %s and external_ref = %s", (instance_id, subject_ref)
                )
                row = await cur.fetchone()
            assert row is not None
            return SubjectKey(instance_id, str(row["id"]))

    async def ref_for(self, subject: SubjectKey) -> str:
        async with self._db.cursor() as cur:
            await cur.execute("select external_ref from subjects where id = %s", (subject.subject_id,))
            row = await cur.fetchone()
            if row is None:
                raise KeyError(f"sujeto desconocido: {subject}")
            return row["external_ref"]


# ---------------------------------------------------------------------- salud (F0.2b)

from core.health.log import HealthEntry  # noqa: E402


class PostgresHealthLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, entry: HealthEntry) -> None:
        async with self._db.cursor() as cur:
            await cur.execute(
                "insert into health_log (instance_id, kind, ok, detail, at) values (%s, %s, %s, %s, %s)",
                (entry.instance_id, entry.kind, entry.ok, Jsonb(dict(entry.detail)), entry.at),
            )

    async def last(self, instance_id: str, kind: str) -> HealthEntry | None:
        async with self._db.cursor() as cur:
            await cur.execute(
                "select * from health_log where instance_id = %s and kind = %s order by at desc limit 1",
                (instance_id, kind),
            )
            row = await cur.fetchone()
        return _health(row) if row else None

    async def list(
        self, instance_id: str, kind: str, *, since: datetime | None = None, limit: int | None = None
    ) -> Sequence[HealthEntry]:
        clauses = ["instance_id = %s", "kind = %s"]
        params: list[Any] = [instance_id, kind]
        if since is not None:
            clauses.append("at >= %s")
            params.append(since)
        sql = f"select * from health_log where {' and '.join(clauses)} order by at desc"
        if limit is not None:
            sql += " limit %s"
            params.append(limit)
        async with self._db.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [_health(r) for r in reversed(rows)]


def _health(row: Mapping[str, Any]) -> HealthEntry:
    return HealthEntry(instance_id=row["instance_id"], kind=row["kind"], ok=bool(row["ok"]), at=row["at"], detail=row["detail"] or {})


# ---------------------------------------------------------------------- memoria (F0.3)

from core.knowledge.store import KnowledgeChunk, KnowledgeHit, KnowledgeSource, KnowledgeStats  # noqa: E402
from core.memory.episodes import Episode, EpisodeHit, NewEpisode  # noqa: E402


def _vec(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _dim(vector: Sequence[float]) -> int:
    n = len(vector)
    if n <= 0 or n > 16000:
        raise ValueError(f"dimension de embedding invalida: {n}")
    return n


def _episode(r: Mapping[str, Any], m: Mind = PLAIN_MIND) -> Episode:
    return Episode(
        id=str(r["id"]),
        instance_id=r["instance_id"],
        subject_id=str(r["subject_id"]),
        event_id=str(r["event_id"]),
        kind=r["kind"],
        text=_ot(m, r["instance_id"], "episodes.text", r["text"]),
        occurred_at=r["occurred_at"],
        created_at=r["created_at"],
        metadata=r["metadata"] or {},
    )


class PostgresEpisodeStore:
    def __init__(self, db: Database, mind: Mind | None = None) -> None:
        self._db = db
        self._m = mind or PLAIN_MIND

    async def write(self, new: NewEpisode) -> tuple[Episode, bool]:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                insert into episodes (instance_id, subject_id, event_id, kind, text, occurred_at, metadata)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (event_id) do nothing
                returning *
                """,
                (new.instance_id, new.subject_id, new.event_id, new.kind, _st(self._m, new.instance_id, "episodes.text", new.text), new.occurred_at, Jsonb(dict(new.metadata))),
            )
            row = await cur.fetchone()
            if row is not None:
                return _episode(row, self._m), True
            await cur.execute("select * from episodes where event_id = %s", (new.event_id,))
            row = await cur.fetchone()
            assert row is not None
            return _episode(row, self._m), False

    async def get_many(self, ids: Sequence[str]) -> Sequence[Episode]:
        if not ids:
            return []
        async with self._db.cursor() as cur:
            await cur.execute("select * from episodes where id = any(%s::uuid[]) order by occurred_at", (list(ids),))
            rows = await cur.fetchall()
        return [_episode(r, self._m) for r in rows]

    async def list(self, instance_id: str, subject_id: str, *, since: datetime | None = None, limit: int | None = None) -> Sequence[Episode]:
        clauses = ["instance_id = %s", "subject_id = %s"]
        params: list[Any] = [instance_id, subject_id]
        if since is not None:
            clauses.append("occurred_at > %s")
            params.append(since)
        sql = f"select * from episodes where {' and '.join(clauses)} order by occurred_at desc, created_at desc"
        if limit is not None:
            sql += " limit %s"
            params.append(limit)
        async with self._db.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [_episode(r, self._m) for r in reversed(rows)]

    async def set_embedding(self, episode_id: str, *, model: str, dim: int, vector: Sequence[float], usage: Mapping[str, int] | None = None) -> bool:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                insert into episode_embeddings (episode_id, model, dim, embedding, usage)
                values (%s, %s, %s, %s::vector, %s) on conflict (episode_id, model) do nothing
                """,
                (episode_id, model, dim, _vec(vector), Jsonb(dict(usage or {}))),
            )
            return (cur.rowcount or 0) > 0

    async def missing_embeddings(self, instance_id: str, *, model: str, limit: int = 50) -> Sequence[Episode]:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                select e.* from episodes e
                left join episode_embeddings m on m.episode_id = e.id and m.model = %s
                where e.instance_id = %s and m.episode_id is null
                order by e.occurred_at
                limit %s
                """,
                (model, instance_id, limit),
            )
            rows = await cur.fetchall()
        return [_episode(r, self._m) for r in rows]

    async def embedded_ids(self, ids: Sequence[str], *, model: str) -> set[str]:
        if not ids:
            return set()
        async with self._db.cursor() as cur:
            await cur.execute(
                "select episode_id from episode_embeddings where model = %s and episode_id = any(%s::uuid[])", (model, list(ids))
            )
            rows = await cur.fetchall()
        return {str(r["episode_id"]) for r in rows}

    async def search(
        self, instance_id: str, subject_id: str, *, model: str, vector: Sequence[float], limit: int, exclude_event_ids: Sequence[str] = ()
    ) -> Sequence[EpisodeHit]:
        dim = _dim(vector)
        clauses = ["e.instance_id = %s", "e.subject_id = %s", "m.model = %s", "m.dim = %s"]
        params: list[Any] = [instance_id, subject_id, model, dim]
        if exclude_event_ids:
            clauses.append("e.event_id <> all(%s::uuid[])")
            params.append(list(exclude_event_ids))
        sql = f"""
            select e.*, 1 - (m.embedding::vector({dim}) <=> %s::vector({dim})) as similarity
            from episodes e join episode_embeddings m on m.episode_id = e.id
            where {' and '.join(clauses)}
            order by m.embedding::vector({dim}) <=> %s::vector({dim})
            limit %s
        """
        v = _vec(vector)
        async with self._db.cursor() as cur:
            await cur.execute(sql, [v, *params, v, limit])
            rows = await cur.fetchall()
        return [EpisodeHit(_episode(r, self._m), float(r["similarity"])) for r in rows]

    async def count(self, instance_id: str, subject_id: str | None = None) -> int:
        async with self._db.cursor() as cur:
            if subject_id is None:
                await cur.execute("select count(*) as n from episodes where instance_id = %s", (instance_id,))
            else:
                await cur.execute("select count(*) as n from episodes where instance_id = %s and subject_id = %s", (instance_id, subject_id))
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def embedding_usage(self, instance_id: str, *, since: datetime | None = None) -> Mapping[str, int]:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                select coalesce(sum((m.usage->>'prompt_tokens')::bigint), 0) as prompt_tokens,
                       coalesce(sum((m.usage->>'total_tokens')::bigint), 0) as total_tokens,
                       coalesce(sum((m.usage->>'cost_microusd')::bigint), 0) as cost_microusd,
                       count(*) as vectors
                from episode_embeddings m join episodes e on e.id = m.episode_id
                where e.instance_id = %s and (%s::timestamptz is null or m.created_at >= %s)
                """,
                (instance_id, since, since),
            )
            row = await cur.fetchone()
        return {k: int(v) for k, v in (row or {}).items()}


def _chunk(r: Mapping[str, Any]) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=str(r["id"]), instance_id=r["instance_id"], source=r["source"], source_hash=r["source_hash"], index=r["idx"], text=r["text"], created_at=r["created_at"]
    )


class PostgresKnowledgeStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def sources(self, instance_id: str) -> Sequence[KnowledgeSource]:
        async with self._db.cursor() as cur:
            await cur.execute("select * from knowledge_sources where instance_id = %s order by source", (instance_id,))
            rows = await cur.fetchall()
        return [KnowledgeSource(r["instance_id"], r["source"], r["current_hash"], r["chunks"], r["loaded_at"]) for r in rows]

    async def put_source(self, instance_id: str, source: str, *, content_hash: str, chunks: Sequence[str], now: datetime) -> tuple[KnowledgeSource, bool]:
        async with self._db.transaction() as cur:
            await cur.execute("insert into instances (id) values (%s) on conflict do nothing", (instance_id,))
            created = False
            for i, text in enumerate(chunks):
                await cur.execute(
                    """
                    insert into knowledge_chunks (instance_id, source, source_hash, idx, text, created_at)
                    values (%s, %s, %s, %s, %s, %s) on conflict (instance_id, source, source_hash, idx) do nothing
                    """,
                    (instance_id, source, content_hash, i, text, now),
                )
                created = created or (cur.rowcount or 0) > 0
            await cur.execute(
                """
                insert into knowledge_sources (instance_id, source, current_hash, chunks, loaded_at)
                values (%s, %s, %s, %s, %s)
                on conflict (instance_id, source) do update
                set current_hash = excluded.current_hash, chunks = excluded.chunks, loaded_at = excluded.loaded_at
                """,
                (instance_id, source, content_hash, len(chunks), now),
            )
        return KnowledgeSource(instance_id, source, content_hash, len(chunks), now), created

    async def retire_source(self, instance_id: str, source: str, *, now: datetime) -> bool:
        async with self._db.cursor() as cur:
            await cur.execute(
                "update knowledge_sources set current_hash = null, chunks = 0, loaded_at = %s where instance_id = %s and source = %s and current_hash is not null",
                (now, instance_id, source),
            )
            return (cur.rowcount or 0) > 0

    async def missing_embeddings(self, instance_id: str, *, model: str, limit: int = 100) -> Sequence[KnowledgeChunk]:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                select c.* from knowledge_chunks c
                join knowledge_sources s on s.instance_id = c.instance_id and s.source = c.source and s.current_hash = c.source_hash
                left join knowledge_embeddings m on m.chunk_id = c.id and m.model = %s
                where c.instance_id = %s and m.chunk_id is null
                order by c.source, c.idx
                limit %s
                """,
                (model, instance_id, limit),
            )
            rows = await cur.fetchall()
        return [_chunk(r) for r in rows]

    async def set_embedding(self, chunk_id: str, *, model: str, dim: int, vector: Sequence[float], usage: Mapping[str, int] | None = None) -> bool:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                insert into knowledge_embeddings (chunk_id, model, dim, embedding, usage)
                values (%s, %s, %s, %s::vector, %s) on conflict (chunk_id, model) do nothing
                """,
                (chunk_id, model, dim, _vec(vector), Jsonb(dict(usage or {}))),
            )
            return (cur.rowcount or 0) > 0

    async def search(self, instance_id: str, *, model: str, vector: Sequence[float], limit: int) -> Sequence[KnowledgeHit]:
        dim = _dim(vector)
        v = _vec(vector)
        async with self._db.cursor() as cur:
            await cur.execute(
                f"""
                select c.*, 1 - (m.embedding::vector({dim}) <=> %s::vector({dim})) as similarity
                from knowledge_chunks c
                join knowledge_sources s on s.instance_id = c.instance_id and s.source = c.source and s.current_hash = c.source_hash
                join knowledge_embeddings m on m.chunk_id = c.id
                where c.instance_id = %s and m.model = %s and m.dim = %s
                order by m.embedding::vector({dim}) <=> %s::vector({dim})
                limit %s
                """,
                (v, instance_id, model, dim, v, limit),
            )
            rows = await cur.fetchall()
        return [KnowledgeHit(_chunk(r), float(r["similarity"])) for r in rows]

    async def stats(self, instance_id: str, *, model: str) -> KnowledgeStats:
        async with self._db.cursor() as cur:
            await cur.execute(
                """
                select count(distinct c.source) as sources, count(*) as chunks, count(m.chunk_id) as embedded
                from knowledge_chunks c
                join knowledge_sources s on s.instance_id = c.instance_id and s.source = c.source and s.current_hash = c.source_hash
                left join knowledge_embeddings m on m.chunk_id = c.id and m.model = %s
                where c.instance_id = %s
                """,
                (model, instance_id),
            )
            row = await cur.fetchone()
        return KnowledgeStats(sources=int(row["sources"]), chunks=int(row["chunks"]), embedded=int(row["embedded"]))


class PostgresAccessLog:
    """Registro de accesos (F0.8) sobre `access_log` (append-only desde 0001)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, entry: AccessEntry) -> None:
        async with self._db.cursor() as cur:
            await cur.execute(
                "insert into access_log (instance_id, what, why, actor, created_at) values (%s, %s, %s, %s, coalesce(%s, now()))",
                (entry.instance_id, entry.what, entry.why, entry.actor, entry.created_at),
            )

    async def list(self, instance_id: str, *, limit: int = 100) -> Sequence[AccessEntry]:
        async with self._db.cursor() as cur:
            await cur.execute("select * from access_log where instance_id = %s order by created_at desc limit %s", (instance_id, limit))
            rows = await cur.fetchall()
        return [AccessEntry(r["instance_id"], r["what"], r["why"], r["actor"], r["created_at"]) for r in reversed(rows)]


# ---------------------------------------------------------------------- auditoria del cifrado

# (tabla, columna, tipo, campo AAD). Lo que el cifrado cubre en columnas; los eventos van por `Mind.event_fields`.
MIND_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("episodes", "text", "text", "episodes.text"),
)


async def mind_audit(db: Database, instance_id: str, mind: Mind) -> list[dict[str, Any]]:
    """Cuenta, por columna y campo de evento sellado, cuantas filas estan cifradas y cuantas en claro, y
    prueba abrir la mas reciente cifrada (detecta clave equivocada o alteracion). NUNCA devuelve contenido."""
    out: list[dict[str, Any]] = []
    async with db.cursor() as cur:
        for table, col, kind, field_ in MIND_COLUMNS:
            sealed_expr = f"({col} ? 'enc')" if kind == "json" else f"({col} like 'enc:v1:%%')"
            empty_expr = f"({col} is null)" if kind == "json" else f"({col} is null or {col} = '')"
            await cur.execute(
                f"select count(*) filter (where {sealed_expr}) as sealed, count(*) filter (where not {sealed_expr} and not {empty_expr}) as plain "
                f"from {table} where instance_id = %s",
                (instance_id,),
            )
            r = await cur.fetchone() or {}
            probe = "-"
            if r.get("sealed"):
                await cur.execute(f"select {col} as v from {table} where instance_id = %s and {sealed_expr} order by created_at desc limit 1", (instance_id,))
                v = (await cur.fetchone() or {}).get("v")
                try:
                    if kind == "json":
                        mind.codec.open_json(v, aad=mind.text_aad(instance_id, field_))
                    else:
                        mind.codec.open_text(v, aad=mind.text_aad(instance_id, field_))
                    probe = "abre"
                except Exception as exc:  # noqa: BLE001 - el tipo de error es el diagnostico
                    probe = type(exc).__name__
            out.append({"field": f"{table}.{col}", "sealed": int(r.get("sealed") or 0), "plain": int(r.get("plain") or 0), "probe": probe})
        for ev_kind, paths in mind.event_fields.items():
            for path in paths:
                parts = path.split(".")
                await cur.execute(
                    """
                    select count(*) filter (where jsonb_typeof(payload #> %s::text[]) = 'object' and (payload #> %s::text[]) ? 'enc') as sealed,
                           count(*) filter (where payload #> %s::text[] is not null and jsonb_typeof(payload #> %s::text[]) <> 'null'
                                            and not (jsonb_typeof(payload #> %s::text[]) = 'object' and (payload #> %s::text[]) ? 'enc')) as plain
                    from events where instance_id = %s and kind = %s
                    """,
                    (parts, parts, parts, parts, parts, parts, instance_id, ev_kind),
                )
                r = await cur.fetchone() or {}
                out.append({"field": f"events:{ev_kind}:{path}", "sealed": int(r.get("sealed") or 0), "plain": int(r.get("plain") or 0), "probe": "-"})
    return out
