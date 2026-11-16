"""Eventos: idempotencia por clave, sin update ni delete, correcciones vinculadas."""

from __future__ import annotations

from datetime import datetime, timezone

from core.events.store import NewEvent
from core.storage.memory import InMemoryEventStore

INST = "inst-test"


async def test_same_key_appends_once():
    store = InMemoryEventStore()
    a = await store.append(NewEvent(INST, "k", "key-1", payload={"n": 1}))
    b = await store.append(NewEvent(INST, "k", "key-1", payload={"n": 2}))
    assert a.created and not b.created
    assert a.event.id == b.event.id
    assert b.event.payload == {"n": 1}, "la segunda escritura no pisa la primera"
    assert store.count() == 1


async def test_keys_are_scoped_by_instance():
    store = InMemoryEventStore()
    a = await store.append(NewEvent("inst-a", "k", "same"))
    b = await store.append(NewEvent("inst-b", "k", "same"))
    assert a.created and b.created and a.event.id != b.event.id


async def test_interface_has_no_mutation_methods():
    store = InMemoryEventStore()
    for forbidden in ("update", "delete", "remove", "edit", "set"):
        assert not hasattr(store, forbidden), f"el almacen de eventos no debe exponer '{forbidden}'"


async def test_correction_links_to_original_and_keeps_it():
    store = InMemoryEventStore()
    orig = await store.append(NewEvent(INST, "label", "lbl-1", payload={"state": "cumplido"}))
    fix = await store.append(
        NewEvent(INST, "label.correction", "lbl-1-fix", payload={"state": "parcial"}, corrects_event_id=orig.event.id)
    )
    assert fix.created and fix.event.corrects_event_id == orig.event.id
    still = await store.get(orig.event.id)
    assert still is not None and still.payload == {"state": "cumplido"}


async def test_list_filters_and_orders_chronologically():
    store = InMemoryEventStore()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await store.append(NewEvent(INST, "a", "1", subject_id="s1", occurred_at=t0.replace(hour=3)))
    await store.append(NewEvent(INST, "b", "2", subject_id="s1", occurred_at=t0.replace(hour=1)))
    await store.append(NewEvent(INST, "a", "3", subject_id="s2", occurred_at=t0.replace(hour=2)))
    got = await store.list(INST, subject_id="s1")
    assert [e.kind for e in got] == ["b", "a"]
    only_a = await store.list(INST, kinds=["a"])
    assert [e.idempotency_key for e in only_a] == ["3", "1"]
    last = await store.list(INST, subject_id="s1", limit=1)
    assert [e.idempotency_key for e in last] == ["1"]
