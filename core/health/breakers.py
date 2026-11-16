"""Cortacircuitos.

Topes por instancia que detienen al motor antes de que un bug gaste plata
o escriba basura: llamadas al modelo por hora, pulsos por dia, costo por
dia. Al superarse uno, el motor deja de consultar al modelo y de enviar,
sigue registrando lo que entra, mantiene los controles mecanicos y avisa.
Queda asi hasta que el operador lo libera (`release`), nunca solo.

El estado no es una fila mutable: es el pliegue de `breaker.tripped` /
`breaker.released` en la episodica, como pausa y salida. Los contadores se
calculan desde los registros de intencion y de pulso que ya existen; no hay
un segundo contador que pueda desincronizarse. Tras una liberacion, los
contadores arrancan desde ese momento (si no, el mismo tope volveria a
saltar al primer turno).

Los fallos seguidos del modelo no cortan: avisan. El pulso de reintento de
la politica de turno ya acota el ritmo, y el tope de llamadas por hora
acota el dano.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from core.events import kinds
from core.events.store import Event, EventStore, NewEvent, utcnow
from core.health.alerts import Alert, Alerter

log = logging.getLogger(__name__)

# Codigos de tope. Son el `code` del evento tripped y de la alerta.
CALLS_PER_HOUR = "model_calls_per_hour"
PULSES_PER_DAY = "pulses_per_day"
COST_PER_DAY = "cost_usd_per_day"

QUOTA_MARKER = "ModelQuotaExceeded"  # nombre de la excepcion que el adaptador levanta sin saldo (402)


@dataclass(frozen=True)
class BreakerLimits:
    model_calls_per_hour: int | None = None
    pulses_per_day: int | None = None
    cost_usd_per_day: float | None = None
    model_failures_in_a_row: int | None = None  # alerta, no corte
    cost_scope: str = "turn"  # "turn" = turno y cierre de sesion; "all" = toda llamada al modelo (model.spend)

    @classmethod
    def from_mapping(cls, m: dict | None) -> "BreakerLimits":
        m = m or {}

        def _int(k: str) -> int | None:
            v = m.get(k)
            return int(v) if v not in (None, "") else None

        cost = m.get("cost_usd_per_day")
        return cls(
            model_calls_per_hour=_int("model_calls_per_hour"),
            pulses_per_day=_int("pulses_per_day"),
            cost_usd_per_day=float(cost) if cost not in (None, "") else None,
            model_failures_in_a_row=_int("model_failures_in_a_row"),
            cost_scope=str(m.get("cost_scope") or "turn").strip().lower(),
        )


@dataclass(frozen=True)
class BreakerState:
    tripped: bool = False
    code: str | None = None
    detail: str | None = None
    since: datetime | None = None
    released_at: datetime | None = None  # ultima liberacion; los contadores arrancan aqui


def fold_breaker(events: Sequence[Event]) -> BreakerState:
    state = BreakerState()
    for ev in events:
        if ev.kind == kinds.BREAKER_TRIPPED:
            state = BreakerState(
                tripped=True,
                code=ev.payload.get("code"),
                detail=ev.payload.get("detail"),
                since=ev.occurred_at,
                released_at=state.released_at,
            )
        elif ev.kind == kinds.BREAKER_RELEASED:
            state = BreakerState(tripped=False, released_at=ev.occurred_at)
    return state


@dataclass(frozen=True)
class Counters:
    model_calls_last_hour: int = 0
    pulses_last_day: int = 0
    cost_usd_last_day: float = 0.0
    failures_in_a_row: int = 0
    turns_last_day: int = 0

    def render(self) -> str:
        return (
            f"llamadas 1h: {self.model_calls_last_hour} · pulsos 24h: {self.pulses_last_day} · "
            f"costo 24h: US${self.cost_usd_last_day:.4f} · turnos 24h: {self.turns_last_day} · "
            f"fallos seguidos: {self.failures_in_a_row}"
        )


def _side_usage(ev: Event) -> Mapping[str, Any]:
    if ev.kind == kinds.MEMORY_SESSION_CLOSED:
        return ((ev.payload.get("consolidation") or {}).get("usage")) or {}
    return ev.payload.get("usage") or {}


def _is_model_intent(ev: Event) -> bool:
    return ev.kind == kinds.TURN_INTENT and ev.payload.get("policy") == "model"


class Breakers:
    """Guardia del turno: `before_turn` decide si el motor puede consultar al modelo;
    `after_intent` mira lo que paso y avisa (fallos seguidos, proveedor sin saldo)."""

    def __init__(
        self,
        *,
        instance_id: str,
        events: EventStore,
        limits: BreakerLimits,
        alerter: Alerter | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._events = events
        self.limits = limits
        self._alerter = alerter
        self._clock = clock

    # ------------------------------------------------------------------ estado y contadores

    async def state(self) -> BreakerState:
        events = await self._events.list(self.instance_id, kinds=list(kinds.BREAKER_KINDS))
        return fold_breaker(events)

    async def counters(self, now: datetime | None = None, *, floor: datetime | None = None) -> Counters:
        now = now or self._clock()
        day_since = now - timedelta(days=1)
        hour_since = now - timedelta(hours=1)
        if floor is not None:
            day_since = max(day_since, floor)
            hour_since = max(hour_since, floor)
        intents = [e for e in await self._events.list(self.instance_id, kinds=[kinds.TURN_INTENT], since=day_since) if _is_model_intent(e)]
        pulses = await self._events.list(self.instance_id, kinds=[kinds.PULSE_FIRED], since=day_since)
        calls = sum(int(e.payload.get("attempts") or 1) for e in intents if e.occurred_at >= hour_since)
        if self.limits.cost_scope == "all":
            # toda llamada al modelo, de cualquier pieza: el registro `model.spend`
            spend = await self._events.list(self.instance_id, kinds=[kinds.MODEL_SPEND], since=day_since)
            cost_micro = sum(int(e.payload.get("cost_microusd") or 0) for e in spend)
        else:
            cost_micro = sum(int((e.payload.get("usage") or {}).get("cost_microusd") or 0) for e in intents)
            # El costo diario incluye lo que llama al modelo fuera del turno (el cierre de sesion). No cuenta en
            # llamadas/hora: esta acotado por construccion (uno por sesion), pero el tope de plata es de plata.
            side = await self._events.list(self.instance_id, kinds=[kinds.MEMORY_SESSION_CLOSED], since=day_since)
            cost_micro += sum(int(_side_usage(e).get("cost_microusd") or 0) for e in side)
        failures = 0
        for e in reversed(intents):
            if e.payload.get("failed"):
                failures += 1
            else:
                break
        return Counters(
            model_calls_last_hour=calls,
            pulses_last_day=len(pulses),
            cost_usd_last_day=cost_micro / 1_000_000,
            failures_in_a_row=failures,
            turns_last_day=len(intents),
        )

    # ------------------------------------------------------------------ guardia del turno

    async def before_turn(self, *, trigger: str, now: datetime | None = None) -> str | None:
        """None = adelante. Un texto = razon para no consultar al modelo en este turno."""
        now = now or self._clock()
        state = await self.state()
        if state.tripped:
            return f"cortacircuito activo ({state.code}): {state.detail}"
        c = await self.counters(now, floor=state.released_at)
        lim = self.limits
        if lim.model_calls_per_hour is not None and c.model_calls_last_hour >= lim.model_calls_per_hour:
            return await self._trip(CALLS_PER_HOUR, f"{c.model_calls_last_hour} llamadas en la ultima hora (tope {lim.model_calls_per_hour})", now)
        if lim.cost_usd_per_day is not None and c.cost_usd_last_day >= lim.cost_usd_per_day:
            return await self._trip(COST_PER_DAY, f"US${c.cost_usd_last_day:.4f} en 24 h (tope US${lim.cost_usd_per_day:.2f})", now)
        if trigger == "pulse" and lim.pulses_per_day is not None and c.pulses_last_day > lim.pulses_per_day:
            # El pulso ya se registro como disparado; con este se supera el tope.
            return await self._trip(PULSES_PER_DAY, f"{c.pulses_last_day} pulsos en 24 h (tope {lim.pulses_per_day})", now)
        return None

    async def blocked(self) -> str | None:
        """Si el cortacircuito esta activo: su codigo (ninguna pieza llama al modelo hasta `release`)."""
        st = await self.state()
        return st.code if st.tripped else None

    async def check_cost(self, now: datetime | None = None) -> str | None:
        """Mira el tope de costo diario fuera del turno (lo llama el medidor despues de cada llamada): una pieza que
        gasta de noche, sin turnos, tambien lo dispara. None = bajo el tope."""
        now = now or self._clock()
        st = await self.state()
        if st.tripped:
            return st.code
        lim = self.limits
        if lim.cost_usd_per_day is None:
            return None
        c = await self.counters(now, floor=st.released_at)
        if c.cost_usd_last_day >= lim.cost_usd_per_day:
            await self._trip(COST_PER_DAY, f"US${c.cost_usd_last_day:.4f} en 24 h (tope US${lim.cost_usd_per_day:.2f})", now)
            return COST_PER_DAY
        return None

    async def after_intent(self, intent: Event) -> None:
        """Mira el registro de intencion recien escrito: fallos seguidos y proveedor sin saldo avisan."""
        if not _is_model_intent(intent) or not intent.payload.get("failed"):
            return
        problems = [str(p) for p in intent.payload.get("problems") or []]
        if any(QUOTA_MARKER in p for p in problems):
            await self._alert(
                Alert(
                    code="provider.quota",
                    severity="critical",
                    title="proveedor sin saldo o key rechazada",
                    detail="la llamada al modelo volvio con 402/403; el motor deja un pulso de reintento. Cargar saldo o rotar la key.",
                    dedupe_key="provider.quota",
                )
            )
            return
        limit = self.limits.model_failures_in_a_row
        if limit is None:
            return
        c = await self.counters(intent.occurred_at)
        if c.failures_in_a_row >= limit:
            types = sorted({p.split(":", 1)[0] for p in problems})
            await self._alert(
                Alert(
                    code="model.failures",
                    severity="warning",
                    title=f"modelo fallando: {c.failures_in_a_row} turnos seguidos sin decision valida",
                    detail=f"ultimos problemas: {', '.join(types) or 'sin detalle'}. Queda pulso de reintento.",
                    dedupe_key="model.failures",
                )
            )

    # ------------------------------------------------------------------ cortar y liberar

    async def _trip(self, code: str, detail: str, now: datetime) -> str:
        res = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                kind=kinds.BREAKER_TRIPPED,
                idempotency_key=kinds.key_breaker_trip(code, now.isoformat()),
                occurred_at=now,
                payload={"code": code, "detail": detail},
            )
        )
        if res.created:
            log.error("CORTACIRCUITO %s: %s", code, detail)
            await self._alert(
                Alert(
                    code="breaker.tripped",
                    severity="critical",
                    title=f"cortacircuito: {code}",
                    detail=f"{detail}. El motor registra pero no consulta al modelo ni envia hasta `ops release`.",
                    dedupe_key=f"breaker.tripped:{code}",
                )
            )
        return f"cortacircuito activo ({code}): {detail}"

    async def release(self, *, actor: str = "operator", note: str = "", now: datetime | None = None) -> bool:
        now = now or self._clock()
        state = await self.state()
        if not state.tripped:
            return False
        res = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                kind=kinds.BREAKER_RELEASED,
                idempotency_key=kinds.key_breaker_release(now.isoformat()),
                occurred_at=now,
                payload={"actor": actor, "note": note, "code": state.code},
            )
        )
        if res.created:
            await self._alert(
                Alert(
                    code="breaker.released",
                    severity="info",
                    title=f"cortacircuito liberado ({state.code})",
                    detail=note or "los contadores arrancan de cero desde ahora",
                )
            )
        return res.created

    async def _alert(self, alert: Alert) -> None:
        if self._alerter is not None:
            await self._alerter.emit(alert)
