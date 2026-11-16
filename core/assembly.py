"""Armado generico: con el contrato de una instancia, devuelve el motor listo para correr.

Una instancia entrega solo lo suyo (`InstanceContract`): su id, sus comandos, su armador de prompts,
su canal, su adaptador de modelo, su zona horaria y su configuracion en un diccionario. El armado
conecta lo comun, igual para toda instancia:

* almacenes (en memoria o Postgres), con cifrado en reposo si hay clave;
* salud: alertas, cortacircuitos (llamadas por hora, pulsos por dia, costo diario), latido, doctor y
  OK diario;
* medidor de gasto alrededor del modelo: toda llamada, de cualquier pieza, cuenta para el tope;
* memoria por persona: episodio por turno con embedding, recuperacion, cierre de sesion;
* conocimiento base: cargador y busqueda con umbral;
* motor, politica de turno que responde y scheduler.

Nada de aqui sabe de que trata la instancia. Las piezas propias de una instancia (consolidador,
procesos de fondo, reportes) se enchufan despues sobre `Assembled`.

Claves de `config` (todas opcionales, con defecto):

    engine:    recent_window, recent_messages, resume_pulse_minutes, group_window_seconds,
               typing_indicator, job_lease_minutes
    health:    limits (ver BreakerLimits.from_mapping), heartbeat_minutes, alerts.dedupe_minutes,
               doctor.stale_job_minutes, daily_ok_at ("HH:MM")
    memory:    recall (episodes, candidates, similarity_weight, recency_half_life_days),
               session_gap_minutes
    knowledge: min_similarity, chunks_per_turn, chunk_chars
    turn:      temperature, max_tokens
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.engine.answer_turn import AnswerPromptBuilder, AnswerRules, AnswerTurnPolicy
from core.engine.loop import Engine, TurnHook
from core.engine.turn import TurnPolicy
from core.events.store import EventStore
from core.health.alerts import Alert, Alerter, AlertSink
from core.health.breakers import BreakerLimits, Breakers
from core.health.checks import breaker_check, database_check, heartbeat_check, jobs_check, mind_check
from core.health.doctor import CheckFn, DailyReport, Doctor
from core.health.heartbeat import Heartbeat, Pinger, UrllibPinger
from core.health.log import HealthLog, InMemoryHealthLog
from core.health.spend import MeteredAdapter
from core.interfaces.channel import Channel
from core.interfaces.interaction_policy import InteractionPolicy
from core.interfaces.model_adapter import ModelAdapter
from core.interfaces.subject_key import SubjectResolver
from core.knowledge.base import KnowledgeBase
from core.knowledge.loader import KnowledgeLoader
from core.knowledge.store import KnowledgeStore
from core.memory.episodes import EpisodeStore
from core.memory.episodic import EpisodicMemory
from core.memory.recall import Recall, RecallConfig
from core.memory.sessions import Consolidator, SessionTracker
from core.mind.access import AccessLog, InMemoryAccessLog
from core.mind.cipher import PLAIN, AesGcmCodec, Mind
from core.scheduler.jobs import Job, JobStore, Scheduler
from core.storage.memory import InMemoryEpisodeStore, InMemoryEventStore, InMemoryJobStore, InMemoryKnowledgeStore, InMemorySubjectResolver

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"


# ---------------------------------------------------------------------- almacenes


@dataclass
class Stores:
    events: EventStore
    jobs: JobStore
    subjects: SubjectResolver
    episodes: EpisodeStore
    knowledge: KnowledgeStore
    health_log: HealthLog
    access: AccessLog
    mind: Mind = field(default_factory=Mind)
    db: Any = None  # core.storage.pg.Database cuando es Postgres


def build_mind(*, key_b64: str | None, required: bool, sealed_events: Mapping[str, Sequence[str]] | None = None,
               accept_plaintext: bool = True, persistent: bool = True) -> Mind:
    """Cifrado en reposo. Exigido y sin clave = no arranca contra una base persistente."""
    fields = {str(k): tuple(v or ()) for k, v in (sealed_events or {}).items()}
    if key_b64:
        return Mind(codec=AesGcmCodec.from_b64(key_b64, accept_plaintext=accept_plaintext), event_fields=fields)
    if required and persistent:
        raise RuntimeError("cifrado exigido y sin clave: lo de las personas no se escribe en claro")
    return Mind(codec=PLAIN, event_fields=fields)


def memory_stores(mind: Mind | None = None) -> Stores:
    log.warning("almacenes en memoria: nada persiste entre reinicios")
    return Stores(events=InMemoryEventStore(), jobs=InMemoryJobStore(), subjects=InMemorySubjectResolver(),
                  episodes=InMemoryEpisodeStore(), knowledge=InMemoryKnowledgeStore(), health_log=InMemoryHealthLog(),
                  access=InMemoryAccessLog(), mind=mind or Mind())


async def postgres_stores(*, instance_id: str, database_url: str, schema: str, mind: Mind) -> Stores:
    from core.storage.pg import Database
    from core.storage.postgres import (
        PostgresAccessLog, PostgresEpisodeStore, PostgresEventStore, PostgresHealthLog, PostgresJobStore,
        PostgresKnowledgeStore, PostgresSubjectResolver,
    )

    db = Database(database_url, schema=schema)
    await db.connect()
    async with db.cursor() as cur:  # la instancia existe desde que abre la base
        await cur.execute("insert into instances (id) values (%s) on conflict do nothing", (instance_id,))
    return Stores(events=PostgresEventStore(db, mind), jobs=PostgresJobStore(db), subjects=PostgresSubjectResolver(db),
                  episodes=PostgresEpisodeStore(db, mind), knowledge=PostgresKnowledgeStore(db), health_log=PostgresHealthLog(db),
                  access=PostgresAccessLog(db), mind=mind, db=db)


# ---------------------------------------------------------------------- contrato y resultado


@dataclass
class InstanceContract:
    """Lo unico que una instancia tiene que entregar."""

    instance_id: str
    interaction: InteractionPolicy
    prompts: AnswerPromptBuilder
    channel: Channel
    adapter: ModelAdapter | None  # None = sin modelo (eco): sirve para probar canal y memoria
    timezone: str = "America/Santiago"
    config: Mapping[str, Any] = field(default_factory=dict)
    alert_sink: AlertSink | None = None
    heartbeat_url: str | None = None
    extra_checks: Sequence[tuple[str, CheckFn]] = ()  # los de sus proveedores (canal, saldo del modelo)
    consolidator: Consolidator | None = None  # lo que corre al cerrar una sesion (p. ej. el acta)
    turn_policy: TurnPolicy | None = None  # None = AnswerTurnPolicy con `prompts` (o eco si no hay modelo)


@dataclass
class Health:
    alerter: Alerter
    breakers: Breakers
    heartbeat: Heartbeat
    doctor: Doctor
    daily: DailyReport

    def attach(self, scheduler: Scheduler) -> None:
        self.heartbeat.attach(scheduler)
        self.daily.attach(scheduler)
        scheduler.set_hooks(on_failed=self.on_job_failed, on_tick_error=self.on_tick_error)

    async def start(self) -> None:
        await self.heartbeat.start()
        await self.daily.start()

    # Todos terminan en una alerta sin contenido de las personas.

    async def on_job_failed(self, job: Job, error: str) -> None:
        await self.alerter.emit(Alert(code="job.failed", severity="critical", title=f"job fallido definitivo: {job.kind}",
                                      detail=f"tras {job.attempts} intento(s): {error[:200]}", dedupe_key=f"job.failed:{job.kind}"))

    async def on_tick_error(self, exc: BaseException) -> None:
        await self.alerter.emit(Alert(code="scheduler.error", severity="critical", title=f"scheduler: {type(exc).__name__}",
                                      detail=f"un tick completo fallo (¿base inalcanzable?): {str(exc)[:200]}",
                                      dedupe_key=f"scheduler.error:{type(exc).__name__}"))

    async def on_turn_error(self, exc: BaseException) -> None:
        await self.alerter.emit(Alert(code="turn.error", severity="critical", title=f"turno fallo: {type(exc).__name__}",
                                      detail=f"el motor no pudo procesar un mensaje: {str(exc)[:200]}",
                                      dedupe_key=f"turn.error:{type(exc).__name__}"))


@dataclass
class Memory:
    episodic: EpisodicMemory
    recall: Recall
    knowledge: KnowledgeBase
    loader: KnowledgeLoader
    sessions: SessionTracker

    @property
    def hooks(self) -> list[TurnHook]:
        return [self.episodic.after_turn, self.sessions.after_turn]


@dataclass
class Assembled:
    contract: InstanceContract
    stores: Stores
    health: Health
    adapter: ModelAdapter | None  # ya con el medidor de gasto
    memory: Memory
    engine: Engine
    scheduler: Scheduler


# ---------------------------------------------------------------------- armado


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return dict(config.get(name) or {})


def _hhmm(raw: str) -> time:
    h, m = str(raw).split(":")
    return time(int(h), int(m))


def build_health(contract: InstanceContract, stores: Stores, *, pinger: Pinger | None = None) -> Health:
    h = _section(contract.config, "health")
    alerter = Alerter(instance_id=contract.instance_id, sink=contract.alert_sink, events=stores.events,
                      dedupe_window=timedelta(minutes=float((h.get("alerts") or {}).get("dedupe_minutes", 60))))
    breakers = Breakers(instance_id=contract.instance_id, events=stores.events,
                        limits=BreakerLimits.from_mapping({"cost_scope": "all", **dict(h.get("limits") or {})}), alerter=alerter)
    heartbeat = Heartbeat(instance_id=contract.instance_id, jobs=stores.jobs, health_log=stores.health_log, pinger=pinger or UrllibPinger(),
                          url=contract.heartbeat_url, every=timedelta(minutes=float(h.get("heartbeat_minutes", 5))))
    stale = timedelta(minutes=float((h.get("doctor") or {}).get("stale_job_minutes", 15)))
    checks: list[tuple[str, CheckFn]] = []
    if stores.db is not None:
        from core.storage.postgres import mind_audit

        db, mind = stores.db, stores.mind
        checks.append(("base", database_check(db, MIGRATIONS_DIR)))
        audit = lambda: mind_audit(db, contract.instance_id, mind)  # noqa: E731
    else:
        audit = None
    checks += [("jobs", jobs_check(stores.jobs, contract.instance_id, stale_after=stale)), ("latido", heartbeat_check(heartbeat)),
               ("cortacircuito", breaker_check(breakers)),
               ("cifrado", mind_check(enabled=stores.mind.enabled, required=bool(h.get("encryption_required", False)) and stores.db is not None,
                                      key_id=stores.mind.codec.key_id, audit=audit))]
    checks += list(contract.extra_checks)
    doctor = Doctor(instance_id=contract.instance_id, checks=checks, health_log=stores.health_log, alerter=alerter)
    daily = DailyReport(instance_id=contract.instance_id, jobs=stores.jobs, doctor=doctor, breakers=breakers, alerter=alerter,
                        health_log=stores.health_log, at=_hhmm(h.get("daily_ok_at", "08:30")), timezone=contract.timezone)
    return Health(alerter=alerter, breakers=breakers, heartbeat=heartbeat, doctor=doctor, daily=daily)


def build_memory(contract: InstanceContract, stores: Stores, adapter: ModelAdapter | None) -> Memory:
    m = _section(contract.config, "memory")
    rc = dict(m.get("recall") or {})
    kn = _section(contract.config, "knowledge")
    knowledge = KnowledgeBase(instance_id=contract.instance_id, store=stores.knowledge,
                              min_similarity=float(kn.get("min_similarity", 0.35)), limit=int(kn.get("chunks_per_turn", 4)))
    recall = Recall(instance_id=contract.instance_id, episodes=stores.episodes, adapter=adapter, knowledge=knowledge,
                    config=RecallConfig(episodes=int(rc.get("episodes", 6)), candidates=int(rc.get("candidates", 24)),
                                        similarity_weight=float(rc.get("similarity_weight", 0.7)),
                                        recency_half_life=timedelta(days=float(rc.get("recency_half_life_days", 7)))))
    sessions = SessionTracker(instance_id=contract.instance_id, events=stores.events, jobs=stores.jobs, episodes=stores.episodes,
                              consolidator=contract.consolidator, gap=timedelta(minutes=float(m.get("session_gap_minutes", 45))))
    return Memory(episodic=EpisodicMemory(instance_id=contract.instance_id, episodes=stores.episodes, adapter=adapter), recall=recall,
                  knowledge=knowledge, sessions=sessions,
                  loader=KnowledgeLoader(instance_id=contract.instance_id, store=stores.knowledge, adapter=adapter,
                                         max_chars=int(kn.get("chunk_chars", 900))))


def assemble(contract: InstanceContract, stores: Stores, *, pinger: Pinger | None = None) -> Assembled:
    """Arma la instancia completa. No arranca nada: el proceso llama a `health.start()` y al scheduler."""
    health = build_health(contract, stores, pinger=pinger)
    adapter = (MeteredAdapter(contract.adapter, instance_id=contract.instance_id, events=stores.events, breakers=health.breakers)
               if contract.adapter is not None else None)
    memory = build_memory(contract, stores, adapter)
    turn_cfg = _section(contract.config, "turn")
    if contract.turn_policy is not None:
        policy: TurnPolicy = contract.turn_policy
    elif adapter is not None:
        policy = AnswerTurnPolicy(adapter=adapter, prompts=contract.prompts, recall=memory.recall,
                                  rules=AnswerRules(temperature=turn_cfg.get("temperature"), max_tokens=turn_cfg.get("max_tokens")))
    else:
        from core.engine.turn import EchoTurnPolicy

        policy = EchoTurnPolicy(prefix="eco: ")
    eng = _section(contract.config, "engine")
    resume = eng.get("resume_pulse_minutes")
    group = float(eng.get("group_window_seconds") or 0)
    engine = Engine(
        instance_id=contract.instance_id, events=stores.events, subjects=stores.subjects, channel=contract.channel, jobs=stores.jobs,
        turn_policy=policy, interaction=contract.interaction, recent_window=int(eng.get("recent_window", 50)),
        recent_messages=int(eng["recent_messages"]) if eng.get("recent_messages") else None,
        resume_pulse=timedelta(minutes=float(resume)) if resume else None,
        guard=health.breakers, after_intent=health.breakers.after_intent,
        group_window=timedelta(seconds=group) if group > 0 else None,
        after_turn=memory.hooks, typing_indicator=bool(eng.get("typing_indicator", False)),
    )
    scheduler = Scheduler(stores.jobs, lease=timedelta(minutes=int(eng.get("job_lease_minutes", 5))), instance_id=contract.instance_id)
    engine.attach(scheduler)
    health.attach(scheduler)
    memory.sessions.attach(scheduler)
    return Assembled(contract=contract, stores=stores, health=health, adapter=adapter, memory=memory, engine=engine, scheduler=scheduler)
