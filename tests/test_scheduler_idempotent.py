"""Criterio F0.1: reiniciar el backend no duplica jobs ni efectos."""

from __future__ import annotations

from datetime import timedelta

from core.scheduler.jobs import JobStatus, NewJob, Scheduler
from core.storage.memory import InMemoryJobStore
from tests.conftest import Clock

INST = "inst-test"


def _job(key: str, at, kind: str = "pulse") -> NewJob:
    return NewJob(instance_id=INST, kind=kind, run_at=at, idempotency_key=key, subject_id="s1")


async def test_enqueue_same_key_twice_creates_one_job():
    store = InMemoryJobStore()
    clock = Clock()
    j1, c1 = await store.enqueue(_job("pulse:s1:t1", clock.now))
    j2, c2 = await store.enqueue(_job("pulse:s1:t1", clock.now))
    assert c1 and not c2 and j1.id == j2.id
    assert len(store.all()) == 1


async def test_restart_does_not_rerun_a_done_job():
    store = InMemoryJobStore()
    clock = Clock()
    executions: list[str] = []

    async def handler(job):
        executions.append(job.id)
        return {"ok": True}

    s1 = Scheduler(store, clock=clock)
    s1.register("pulse", handler)
    await store.enqueue(_job("pulse:s1:t1", clock.now))
    assert await s1.tick() == 1

    # "Reinicio": otro scheduler sobre el mismo almacen, misma hora o mas tarde.
    s2 = Scheduler(store, clock=clock)
    s2.register("pulse", handler)
    clock.advance(hours=1)
    assert await s2.tick() == 0
    assert executions == [executions[0]]
    assert (await store.get(executions[0])).status == JobStatus.DONE


async def test_restart_reenqueueing_the_same_pulse_is_a_noop():
    """El arranque suele re-programar lo que cree pendiente. Con la misma clave, no pasa nada."""
    store = InMemoryJobStore()
    clock = Clock()
    at = clock.now + timedelta(minutes=10)
    await store.enqueue(_job(f"pulse:s1:{at.isoformat()}", at))
    _, created = await store.enqueue(_job(f"pulse:s1:{at.isoformat()}", at))
    assert not created
    assert len(await store.pending(INST)) == 1


async def test_crashed_job_is_reclaimed_after_lease_without_duplicating_when_handler_is_idempotent():
    store = InMemoryJobStore()
    clock = Clock()
    effects: set[str] = set()  # efectos con clave: el handler escribe "una vez por clave"
    calls = 0

    async def flaky(job):
        nonlocal calls
        calls += 1
        effects.add(job.idempotency_key)
        if calls == 1:
            raise RuntimeError("se cayo el proceso a mitad")
        return {"ok": True}

    s = Scheduler(store, clock=clock, lease=timedelta(minutes=5))
    s.register("pulse", flaky)
    await store.enqueue(_job("pulse:s1:t1", clock.now))
    assert await s.tick() == 0  # fallo; vuelve a pending con backoff
    clock.advance(minutes=1)
    assert await s.tick() == 1  # reintento exitoso
    assert calls == 2
    assert effects == {"pulse:s1:t1"}, "el efecto es uno aunque hubo dos intentos"


async def test_expired_lease_is_reclaimed_by_another_process():
    """Simula un proceso que reclamo el job y murio sin reportar nada."""
    store = InMemoryJobStore()
    clock = Clock()
    await store.enqueue(_job("pulse:s1:t1", clock.now))
    claimed = await store.claim_due(clock.now, timedelta(minutes=5))
    assert len(claimed) == 1 and claimed[0].attempts == 1
    # Nadie lo completa. Antes de vencer el lease, nadie mas lo toma:
    clock.advance(minutes=4)
    assert await store.claim_due(clock.now, timedelta(minutes=5)) == []
    # Vencido el lease, se vuelve a reclamar con attempts + 1:
    clock.advance(minutes=2)
    again = await store.claim_due(clock.now, timedelta(minutes=5))
    assert len(again) == 1 and again[0].id == claimed[0].id and again[0].attempts == 2


async def test_cancel_only_touches_pending_of_that_subject_and_kind():
    store = InMemoryJobStore()
    clock = Clock()
    await store.enqueue(NewJob(INST, "pulse", clock.now, "p1", subject_id="s1"))
    await store.enqueue(NewJob(INST, "pulse", clock.now, "p2", subject_id="s2"))
    await store.enqueue(NewJob(INST, "nightly", clock.now, "n1", subject_id="s1"))
    n = await store.cancel(INST, subject_id="s1", kinds=["pulse"])
    assert n == 1
    kinds = {(j.kind, j.subject_id) for j in await store.pending(INST)}
    assert kinds == {("pulse", "s2"), ("nightly", "s1")}


async def test_job_without_handler_fails_cleanly():
    store = InMemoryJobStore()
    clock = Clock()
    s = Scheduler(store, clock=clock)
    job, _ = await store.enqueue(_job("x", clock.now, kind="desconocido"))
    assert await s.tick() == 0
    assert (await store.get(job.id)).status == JobStatus.FAILED


async def test_scheduler_only_claims_its_own_instance_jobs():
    """F0.4: cada instancia es su propio proceso sobre la misma base; un scheduler no toca jobs ajenos."""
    from core.scheduler.jobs import NewJob as _NewJob, Scheduler as _Scheduler
    from core.storage.memory import InMemoryJobStore as _Store
    from tests.conftest import Clock as _Clock

    clock = _Clock()
    store = _Store()
    ran: list[str] = []

    async def handler(job):
        ran.append(job.instance_id)
        return {}

    for inst in ("a", "b"):
        await store.enqueue(_NewJob(instance_id=inst, kind="k", run_at=clock.now, idempotency_key=f"k:{inst}"))
    sched = _Scheduler(store, lease=timedelta(minutes=5), clock=clock, instance_id="a")
    sched.register("k", handler)
    await sched.tick()
    assert ran == ["a"]
    assert [j.instance_id for j in await store.pending("b", kinds=["k"])] == ["b"]
