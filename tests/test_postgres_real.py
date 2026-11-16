"""La instancia minima sobre Postgres real, con cifrado en reposo. Corre solo si hay `TEST_DATABASE_URL`
(una base con las migraciones aplicadas); si no, se salta. Reiniciar el proceso no pierde ni duplica nada."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adapters.fake_model import ScriptedModelAdapter
from core.assembly import assemble, build_mind, postgres_stores
from core.events import kinds
from core.interfaces.channel import InboundEvent
from core.mind.cipher import new_key
from core.storage.postgres import mind_audit
from instances import minima
from tests.conftest import FakeChannel

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="sin TEST_DATABASE_URL")
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
INST = f"minima-{uuid.uuid4().hex[:8]}"  # los eventos son append-only: cada corrida usa su propia instancia


def _msg(who, text, n):
    return InboundEvent(instance_id=INST, subject_ref=who, external_id=f"pg-{who}-{n}", text=text, received_at=T0)


async def _app(key):
    mind = build_mind(key_b64=key, required=True, sealed_events={"message.received": ["text"], "message.sent": ["text"]})
    stores = await postgres_stores(instance_id=INST, database_url=URL, schema="motor", mind=mind)
    channel = FakeChannel()
    adapter = ScriptedModelAdapter(lambda r: "ok " + r.last_user_text, dim=64, semantic=True)
    contract = minima.contract(channel=channel, adapter=adapter, config={"knowledge": {"min_similarity": 0.1}})
    contract.instance_id = INST
    app = assemble(contract, stores)
    return app, channel, adapter


async def test_conversa_cifrado_sobre_postgres_y_sobrevive_un_reinicio(tmp_path: Path):
    key = new_key()
    app, channel, adapter = await _app(key)
    (tmp_path / "prenda.md").write_text("La prenda se alza con la carta de la financiera.", encoding="utf-8")
    await app.memory.loader.load_dir(tmp_path)
    await app.engine.receive(_msg("ana", "como se alza la prenda", 1))
    assert channel.sent[-1].text.startswith("ok ") and "[prenda.md]" in adapter.calls[-1].messages[0].content
    audit = {r["field"]: r for r in await mind_audit(app.stores.db, INST, app.stores.mind)}
    assert audit["episodes.text"]["sealed"] >= 1 and audit["episodes.text"]["plain"] == 0 and audit["episodes.text"]["probe"] == "abre"
    async with app.stores.db.cursor() as cur:
        await cur.execute("select payload from events where instance_id=%s and kind='message.received'", (INST,))
        raw = [r["payload"] for r in await cur.fetchall()]
    assert raw and all("como se alza" not in str(p) for p in raw), "en la base el texto va cifrado"
    report = await app.health.doctor.run(alert=False)
    by = {c.name: c for c in report.checks}
    assert by["base"].ok and by["cifrado"].ok and by["cortacircuito"].ok, [(c.name, c.detail) for c in report.failed]
    # reinicio: proceso nuevo sobre la misma base; la reentrega del mismo mensaje no duplica
    app2, channel2, adapter2 = await _app(key)
    await app2.engine.receive(_msg("ana", "como se alza la prenda", 1))
    assert channel2.sent == [] and adapter2.calls == []
    ana = await app2.stores.subjects.resolve(INST, "ana")
    eps = await app2.stores.episodes.list(INST, ana.subject_id)
    assert len(eps) == 1 and "como se alza la prenda" in eps[0].text, "el episodio se abre con la clave"
    sent = await app2.stores.events.list(INST, subject_id=ana.subject_id, kinds=[kinds.MESSAGE_SENT])
    assert len(sent) == 1
