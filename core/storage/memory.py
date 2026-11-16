"""Almacenes en memoria.

Implementan exactamente los mismos contratos que `core.storage.postgres`.
Sirven para pruebas y para correr el motor sin base de datos. Nada aqui
persiste entre procesos: la prueba de "reiniciar no duplica" simula el
reinicio construyendo un motor nuevo sobre el *mismo* almacen.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from core.events.store import AppendResult, Event, NewEvent, utcnow
from core.interfaces.subject_key import SubjectKey
from core.scheduler.jobs import Job, JobStatus, NewJob


def _uuid() -> str:
    return str(uuid.uuid4())


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._by_id: dict[str, Event] = {}
        self._by_key: dict[tuple[str, str], Event] = {}
        self._lock = asyncio.Lock()

    async def append(self, new: NewEvent) -> AppendResult:
        async with self._lock:
            key = (new.instance_id, new.idempotency_key)
            existing = self._by_key.get(key)
            if existing is not None:
                return AppendResult(existing, created=False)
            now = utcnow()
            event = Event(
                id=_uuid(),
                instance_id=new.instance_id,
                subject_id=new.subject_id,
                kind=new.kind,
                payload=dict(new.payload),
                idempotency_key=new.idempotency_key,
                occurred_at=new.occurred_at or now,
                recorded_at=now,
                corrects_event_id=new.corrects_event_id,
            )
            self._events.append(event)
            self._by_id[event.id] = event
            self._by_key[key] = event
            return AppendResult(event, created=True)

    async def get(self, event_id: str) -> Event | None:
        return self._by_id.get(event_id)

    async def by_key(self, instance_id: str, idempotency_key: str) -> Event | None:
        return self._by_key.get((instance_id, idempotency_key))

    async def list(
        self,
        instance_id: str,
        *,
        subject_id: str | None = None,
        kinds: Sequence[str] | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Event]:
        out = [
            e
            for e in self._events
            if e.instance_id == instance_id
            and (subject_id is None or e.subject_id == subject_id)
            and (kinds is None or e.kind in kinds)
            and (since is None or e.occurred_at >= since)
        ]
        out.sort(key=lambda e: (e.occurred_at, e.recorded_at))
        if limit is not None:
            out = out[-limit:]
        return out

    # Solo para pruebas: cuantas filas hay. No es parte del contrato.
    def count(self) -> int:
        return len(self._events)


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.executions: list[str] = []  # para pruebas: ids reclamados

    async def enqueue(self, new: NewJob) -> tuple[Job, bool]:
        async with self._lock:
            if new.idempotency_key in self._by_key:
                return self._jobs[self._by_key[new.idempotency_key]], False
            job = Job(
                id=_uuid(),
                instance_id=new.instance_id,
                subject_id=new.subject_id,
                kind=new.kind,
                run_at=new.run_at,
                idempotency_key=new.idempotency_key,
                payload=dict(new.payload),
                status=JobStatus.PENDING,
                attempts=0,
                created_at=utcnow(),
            )
            self._jobs[job.id] = job
            self._by_key[job.idempotency_key] = job.id
            return job, True

    async def claim_due(self, now: datetime, lease: timedelta, limit: int = 10, *, instance_id: str | None = None) -> Sequence[Job]:
        async with self._lock:
            claimed: list[Job] = []
            for job in sorted(self._jobs.values(), key=lambda j: j.run_at):
                if len(claimed) >= limit:
                    break
                if instance_id is not None and job.instance_id != instance_id:
                    continue
                due_pending = job.status == JobStatus.PENDING and job.run_at <= now
                lease_expired = (
                    job.status == JobStatus.RUNNING and job.lease_until is not None and job.lease_until <= now
                )
                if due_pending or lease_expired:
                    updated = _replace(job, status=JobStatus.RUNNING, attempts=job.attempts + 1, lease_until=now + lease)
                    self._jobs[job.id] = updated
                    self.executions.append(job.id)
                    claimed.append(updated)
            return claimed

    async def complete(self, job_id: str, result: Mapping[str, Any] | None = None) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            self._jobs[job_id] = _replace(
                job, status=JobStatus.DONE, finished_at=utcnow(), result=dict(result or {}), lease_until=None
            )

    async def fail(self, job_id: str, error: str, retry_at: datetime | None) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            if retry_at is None:
                self._jobs[job_id] = _replace(job, status=JobStatus.FAILED, finished_at=utcnow(), error=error, lease_until=None)
            else:
                self._jobs[job_id] = _replace(job, status=JobStatus.PENDING, run_at=retry_at, error=error, lease_until=None)

    async def cancel(
        self,
        instance_id: str,
        *,
        subject_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_keys: Sequence[str] = (),
    ) -> int:
        async with self._lock:
            n = 0
            for job in list(self._jobs.values()):
                if (
                    job.instance_id == instance_id
                    and job.status == JobStatus.PENDING
                    and (subject_id is None or job.subject_id == subject_id)
                    and (kinds is None or job.kind in kinds)
                    and job.idempotency_key not in exclude_keys
                ):
                    self._jobs[job.id] = _replace(job, status=JobStatus.CANCELLED, finished_at=utcnow())
                    n += 1
            return n

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def pending(
        self, instance_id: str, *, subject_id: str | None = None, kinds: Sequence[str] | None = None, include_running: bool = False
    ) -> Sequence[Job]:
        statuses = (JobStatus.PENDING, JobStatus.RUNNING) if include_running else (JobStatus.PENDING,)
        return [
            j
            for j in self._jobs.values()
            if j.instance_id == instance_id
            and j.status in statuses
            and (subject_id is None or j.subject_id == subject_id)
            and (kinds is None or j.kind in kinds)
        ]

    def all(self) -> Sequence[Job]:
        return list(self._jobs.values())


class InMemorySubjectResolver:
    def __init__(self) -> None:
        self._by_ref: dict[tuple[str, str], SubjectKey] = {}
        self._ref_by_key: dict[SubjectKey, str] = {}

    async def resolve(self, instance_id: str, subject_ref: str) -> SubjectKey:
        key = self._by_ref.get((instance_id, subject_ref))
        if key is None:
            key = SubjectKey(instance_id, _uuid())
            self._by_ref[(instance_id, subject_ref)] = key
            self._ref_by_key[key] = subject_ref
        return key

    async def ref_for(self, subject: SubjectKey) -> str:
        return self._ref_by_key[subject]


def _replace(job: Job, **changes: Any) -> Job:
    from dataclasses import replace

    return replace(job, **changes)


# ---------------------------------------------------------------------- memoria (F0.3)

import math  # noqa: E402

from core.knowledge.store import KnowledgeChunk, KnowledgeHit, KnowledgeSource, KnowledgeStats  # noqa: E402
from core.memory.episodes import Episode, EpisodeHit, NewEpisode  # noqa: E402


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryEpisodeStore:
    def __init__(self) -> None:
        self._episodes: dict[str, Episode] = {}
        self._by_event: dict[str, str] = {}
        self._vectors: dict[tuple[str, str], tuple[int, list[float], dict[str, int]]] = {}  # (episode_id, model)
        self._lock = asyncio.Lock()

    async def write(self, new: NewEpisode) -> tuple[Episode, bool]:
        async with self._lock:
            existing = self._by_event.get(new.event_id)
            if existing is not None:
                return self._episodes[existing], False
            ep = Episode(
                id=_uuid(),
                instance_id=new.instance_id,
                subject_id=new.subject_id,
                event_id=new.event_id,
                kind=new.kind,
                text=new.text,
                occurred_at=new.occurred_at,
                created_at=utcnow(),
                metadata=dict(new.metadata),
            )
            self._episodes[ep.id] = ep
            self._by_event[ep.event_id] = ep.id
            return ep, True

    async def get_many(self, ids: Sequence[str]) -> Sequence[Episode]:
        return [self._episodes[i] for i in ids if i in self._episodes]

    async def list(self, instance_id: str, subject_id: str, *, since: datetime | None = None, limit: int | None = None) -> Sequence[Episode]:
        out = [
            e
            for e in self._episodes.values()
            if e.instance_id == instance_id and e.subject_id == subject_id and (since is None or e.occurred_at > since)
        ]
        out.sort(key=lambda e: (e.occurred_at, e.created_at))
        return out[-limit:] if limit is not None else out

    async def set_embedding(self, episode_id: str, *, model: str, dim: int, vector: Sequence[float], usage: Mapping[str, int] | None = None) -> bool:
        key = (episode_id, model)
        if key in self._vectors or episode_id not in self._episodes:
            return False
        self._vectors[key] = (dim, list(vector), dict(usage or {}))
        return True

    async def missing_embeddings(self, instance_id: str, *, model: str, limit: int = 50) -> Sequence[Episode]:
        out = [e for e in self._episodes.values() if e.instance_id == instance_id and (e.id, model) not in self._vectors]
        out.sort(key=lambda e: e.occurred_at)
        return out[:limit]

    async def embedded_ids(self, ids: Sequence[str], *, model: str) -> set[str]:
        return {i for i in ids if (i, model) in self._vectors}

    async def search(
        self, instance_id: str, subject_id: str, *, model: str, vector: Sequence[float], limit: int, exclude_event_ids: Sequence[str] = ()
    ) -> Sequence[EpisodeHit]:
        excluded = set(exclude_event_ids)
        hits: list[EpisodeHit] = []
        for (eid, m), (_, vec, _) in self._vectors.items():
            ep = self._episodes.get(eid)
            if m != model or ep is None or ep.instance_id != instance_id or ep.subject_id != subject_id or ep.event_id in excluded:
                continue
            hits.append(EpisodeHit(ep, cosine(vector, vec)))
        hits.sort(key=lambda h: h.similarity, reverse=True)
        return hits[:limit]

    async def count(self, instance_id: str, subject_id: str | None = None) -> int:
        return sum(1 for e in self._episodes.values() if e.instance_id == instance_id and (subject_id is None or e.subject_id == subject_id))

    async def embedding_usage(self, instance_id: str, *, since: datetime | None = None) -> Mapping[str, int]:
        total: dict[str, int] = {}
        for (eid, _), (_, _, usage) in self._vectors.items():
            ep = self._episodes.get(eid)
            if ep is None or ep.instance_id != instance_id or (since is not None and ep.created_at < since):
                continue
            for k, v in usage.items():
                total[k] = total.get(k, 0) + int(v)
        return total


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self._sources: dict[tuple[str, str], KnowledgeSource] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._versions: set[tuple[str, str, str]] = set()  # (instance, source, hash)
        self._vectors: dict[tuple[str, str], tuple[int, list[float], dict[str, int]]] = {}

    async def sources(self, instance_id: str) -> Sequence[KnowledgeSource]:
        return [s for (i, _), s in self._sources.items() if i == instance_id]

    async def put_source(self, instance_id: str, source: str, *, content_hash: str, chunks: Sequence[str], now: datetime) -> tuple[KnowledgeSource, bool]:
        created = (instance_id, source, content_hash) not in self._versions
        if created:
            self._versions.add((instance_id, source, content_hash))
            for i, text in enumerate(chunks):
                c = KnowledgeChunk(id=_uuid(), instance_id=instance_id, source=source, source_hash=content_hash, index=i, text=text, created_at=now)
                self._chunks[c.id] = c
        src = KnowledgeSource(instance_id=instance_id, source=source, content_hash=content_hash, chunks=len(chunks), loaded_at=now)
        self._sources[(instance_id, source)] = src
        return src, created

    async def retire_source(self, instance_id: str, source: str, *, now: datetime) -> bool:
        src = self._sources.get((instance_id, source))
        if src is None or src.content_hash is None:
            return False
        self._sources[(instance_id, source)] = KnowledgeSource(instance_id, source, None, 0, now)
        return True

    def _current(self, instance_id: str) -> list[KnowledgeChunk]:
        current = {(s.source, s.content_hash) for (i, _), s in self._sources.items() if i == instance_id and s.content_hash}
        return [c for c in self._chunks.values() if c.instance_id == instance_id and (c.source, c.source_hash) in current]

    async def missing_embeddings(self, instance_id: str, *, model: str, limit: int = 100) -> Sequence[KnowledgeChunk]:
        out = [c for c in self._current(instance_id) if (c.id, model) not in self._vectors]
        out.sort(key=lambda c: (c.source, c.index))
        return out[:limit]

    async def set_embedding(self, chunk_id: str, *, model: str, dim: int, vector: Sequence[float], usage: Mapping[str, int] | None = None) -> bool:
        key = (chunk_id, model)
        if key in self._vectors or chunk_id not in self._chunks:
            return False
        self._vectors[key] = (dim, list(vector), dict(usage or {}))
        return True

    async def search(self, instance_id: str, *, model: str, vector: Sequence[float], limit: int) -> Sequence[KnowledgeHit]:
        hits = []
        for c in self._current(instance_id):
            v = self._vectors.get((c.id, model))
            if v is None:
                continue
            hits.append(KnowledgeHit(c, cosine(vector, v[1])))
        hits.sort(key=lambda h: h.similarity, reverse=True)
        return hits[:limit]

    async def stats(self, instance_id: str, *, model: str) -> KnowledgeStats:
        current = self._current(instance_id)
        sources = {c.source for c in current}
        embedded = sum(1 for c in current if (c.id, model) in self._vectors)
        return KnowledgeStats(sources=len(sources), chunks=len(current), embedded=embedded)


