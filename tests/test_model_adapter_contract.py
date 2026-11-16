"""Criterio F0.1: dos adaptadores de modelo simulados pasan el mismo contrato."""

from __future__ import annotations

import json

import pytest

from adapters.fake_model import EchoModelAdapter, ScriptedModelAdapter
from core.interfaces.model_adapter import ModelMessage, ModelRequest, ModelRole
from core.testing.model_adapter_contract import ModelAdapterContract


def _scripted() -> ScriptedModelAdapter:
    def script(req: ModelRequest) -> str:
        if req.schema:
            return json.dumps({"text": "clasificado", "ask": True, "level": 2})
        return f"guion para {req.role.value}"

    return ScriptedModelAdapter(script, model="scripted-v2", dim=12)


ADAPTERS = {
    "echo": EchoModelAdapter,
    "scripted": _scripted,
}

CONTRACT_CHECKS = [name for name, _ in ModelAdapterContract(EchoModelAdapter()).checks()]


@pytest.mark.parametrize("adapter_name", list(ADAPTERS))
@pytest.mark.parametrize("check_name", CONTRACT_CHECKS)
async def test_adapter_passes_each_contract_check(adapter_name: str, check_name: str):
    adapter = ADAPTERS[adapter_name]()
    contract = ModelAdapterContract(adapter)
    check = dict(contract.checks())[check_name]
    await check()  # type: ignore[misc]


@pytest.mark.parametrize("adapter_name", list(ADAPTERS))
async def test_adapter_passes_whole_contract(adapter_name: str):
    passed = await ModelAdapterContract(ADAPTERS[adapter_name]()).run_all()
    assert passed == CONTRACT_CHECKS


async def test_two_adapters_are_interchangeable_from_the_callers_point_of_view():
    """El mismo codigo cliente corre con ambos y obtiene la misma forma de salida."""
    req = ModelRequest(role=ModelRole.CONVERSATION, messages=[ModelMessage("user", "hola")])
    identities = set()
    for make in ADAPTERS.values():
        adapter = make()
        res = await adapter.complete(req)
        assert isinstance(res.text, str)
        identities.add((res.identity.provider, res.identity.model))
        emb = await adapter.embed(["a"])
        assert emb.dim == len(emb.vectors[0])
    assert len(identities) == 2, "los dos adaptadores deben identificarse distinto"
