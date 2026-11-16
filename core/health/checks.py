"""Chequeos genericos del doctor. Los de proveedores (canal, modelo) van en adaptadores."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from core.events.store import utcnow
from core.health.breakers import Breakers
from core.health.doctor import Check, CheckFn
from core.health.heartbeat import Heartbeat
from core.scheduler.jobs import JobStore


def jobs_check(jobs: JobStore, instance_id: str, *, stale_after: timedelta, clock: Callable[[], datetime] = utcnow) -> CheckFn:
    async def run() -> Check:
        now = clock()
        pending = await jobs.pending(instance_id)
        stale = [j for j in pending if now - j.run_at > stale_after]
        if stale:
            kinds = sorted({j.kind for j in stale})
            return Check("jobs", False, f"{len(stale)} job(s) atascados (vencidos hace mas de {stale_after}): {', '.join(kinds)}")
        return Check("jobs", True, f"{len(pending)} pendiente(s), ninguno atascado")

    return run


def heartbeat_check(heartbeat: Heartbeat, *, clock: Callable[[], datetime] = utcnow) -> CheckFn:
    async def run() -> Check:
        if not heartbeat.enabled:
            return Check("latido", False, "sin URL configurada: nadie avisa si el proceso muere", severity="warning")
        last = await heartbeat.last()
        if last is None:
            return Check("latido", False, "todavia no hay ningun latido registrado", severity="warning")
        age = clock() - last.at
        if age > heartbeat.every * 2:
            return Check("latido", False, f"ultimo latido hace {_fmt(age)} (se esperaba cada {_fmt(heartbeat.every)})")
        if not last.ok:
            return Check("latido", False, f"ultimo latido fallo: {last.detail.get('detail')}", severity="warning")
        return Check("latido", True, f"hace {_fmt(age)}")

    return run


def breaker_check(breakers: Breakers) -> CheckFn:
    async def run() -> Check:
        st = await breakers.state()
        if st.tripped:
            return Check("cortacircuito", False, f"ACTIVO: {st.code} — {st.detail}")
        c = await breakers.counters(floor=st.released_at)
        return Check("cortacircuito", True, c.render())

    return run


def database_check(db, migrations_dir: Path) -> CheckFn:
    """Base alcanzable y con todas las migraciones del repo. `db` es `core.storage.pg.Database`."""

    async def run() -> Check:
        expected = sorted(p.stem for p in migrations_dir.glob("*.sql"))
        async with db.cursor() as cur:
            await cur.execute("select name from schema_migrations")
            rows = await cur.fetchall()
        applied = {r["name"] for r in rows}
        missing = [m for m in expected if m not in applied]
        if missing:
            return Check("base", False, f"migraciones pendientes: {', '.join(missing)}")
        return Check("base", True, f"alcanzable, {len(applied)} migracion(es)")

    return run


def _fmt(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h{minutes % 60:02d}"


def mind_check(*, enabled: bool, required: bool, key_id: str | None, audit=None, accept_plaintext: bool = True) -> CheckFn:
    """El cifrado en reposo. Rojo si se exige cifrado y no hay clave, o si lo cifrado mas reciente no
    abre (clave equivocada o alteracion). Las filas en claro de antes del cifrado se informan; no ponen el
    doctor en rojo. Nunca muestra contenido."""

    async def run() -> Check:
        if not enabled:
            if required:
                return Check("cifrado", False, "cifrado exigido y sin MIND_KEY: lo de las personas se escribiria en claro")
            return Check("cifrado", True, "sin cifrado (instancia sin exigencia)", severity="warning")
        if audit is None:
            return Check("cifrado", True, f"cifrada (clave {key_id})")
        rows = await audit()
        broken = [r["field"] for r in rows if r["probe"] not in ("-", "abre")]
        if broken:
            return Check("cifrado", False, f"lo cifrado no abre en: {', '.join(broken)} (clave equivocada o alteracion)")
        sealed = sum(r["sealed"] for r in rows)
        plain = sum(r["plain"] for r in rows)
        if plain and not accept_plaintext:
            # r2: la instancia ya no acepta legado y hay filas en claro: o el reset no las borro, o alguien
            # se escribio por fuera del motor. Leerlas levanta MindTampered en el turno: rojo.
            return Check("cifrado", False, f"{plain} fila(s) en claro en campos cifrados y la instancia ya no acepta texto en claro (mind.accept_plaintext=false)")
        legacy = f" · {plain} en claro de antes del cifrado (el reset las borra)" if plain else ""
        return Check("cifrado", True, f"cifrada (clave {key_id}) · {sealed} campo(s) sellado(s){legacy}")

    return run
