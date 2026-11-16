"""Adaptador nativo de Gemini y enrutado por rol."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from adapters.google_native import GoogleNativeAdapter, GoogleRoleConfig, RoutedModelAdapter
from core.interfaces.model_adapter import (
    ModelBadOutput, ModelIdentity, ModelMessage, ModelQuotaExceeded, ModelRequest, ModelResponse, ModelRole,
)

RC = GoogleRoleConfig(model="gemini-4-hydrogen", price_in=1.5, price_out=7.5, generation={"thinkingConfig": {"thinkingLevel": "low"}})


def _ok(text="hola", prompt=1000, out=100, think=50):
    return {"candidates": [{"content": {"parts": [{"text": "pienso", "thought": True}, {"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": out, "thoughtsTokenCount": think},
            "modelVersion": "gemini-4-hydrogen-001", "responseId": "r1"}


def _adapter(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleNativeAdapter(api_key="k", roles={ModelRole.CONVERSATION: RC}, client=client, **kw)


def _req(*msgs, **kw):
    return ModelRequest(role=ModelRole.CONVERSATION, messages=[ModelMessage(r, c) for r, c in msgs], **kw)


def test_body_has_filters_off_system_instruction_and_alternating_turns():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok())

    ad = _adapter(handler)
    res = asyncio.run(ad.complete(_req(("system", "eres una asistente"), ("user", "hola"), ("user", "¿estás?"),
                                       ("assistant", "sí"), ("system", "nota del sistema"), temperature=0.8, max_tokens=3000)))
    body = seen["body"]
    assert seen["url"].endswith("/models/gemini-4-hydrogen:generateContent") and seen["key"] == "k"
    assert "safetySettings" not in body, "los filtros quedan en el valor por defecto del proveedor"
    assert body["systemInstruction"]["parts"][0]["text"] == "eres una asistente"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert len(body["contents"][0]["parts"]) == 2  # dos mensajes seguidos del sujeto, juntos
    assert body["contents"][2]["parts"][0]["text"].startswith("[sistema]")
    gen = body["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"} and gen["temperature"] == 0.8 and gen["maxOutputTokens"] == 3000
    assert res.text == "hola"  # el pensamiento no viaja en el texto
    assert res.identity.provider == "google" and res.identity.version == "gemini-4-hydrogen-001"


def test_cost_counts_thinking_as_output_in_microusd():
    ad = _adapter(lambda r: httpx.Response(200, json=_ok(prompt=10_000, out=400, think=600)))
    res = asyncio.run(ad.complete(_req(("system", "s"), ("user", "u"))))
    assert res.usage["cost_microusd"] == round(10_000 * 1.5 + 1000 * 7.5)  # US$0.0225
    assert res.usage["prompt_tokens"] == 10_000 and res.usage["completion_tokens"] == 1000


def test_empty_output_raises_bad_output_but_keeps_the_cost():
    data = {"candidates": [{"finishReason": "SAFETY"}], "usageMetadata": {"promptTokenCount": 100}}
    ad = _adapter(lambda r: httpx.Response(200, json=data))
    with pytest.raises(ModelBadOutput) as exc:
        asyncio.run(ad.complete(_req(("system", "s"), ("user", "u"))))
    assert exc.value.usage["cost_microusd"] == 150


def test_retries_on_503_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="overloaded") if calls["n"] == 1 else httpx.Response(200, json=_ok())

    async def no_sleep(_):
        return None

    monkeypatch.setattr("adapters.google_native.asyncio.sleep", no_sleep)
    res = asyncio.run(_adapter(handler).complete(_req(("system", "s"), ("user", "u"))))
    assert res.text == "hola" and calls["n"] == 2


def test_bad_key_is_quota_error_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, text="denied")

    with pytest.raises(ModelQuotaExceeded):
        asyncio.run(_adapter(handler).complete(_req(("system", "s"), ("user", "u"))))
    assert calls["n"] == 1


def test_structured_uses_json_mode_and_checks_required_keys():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok(text='{"a": 1}'))

    schema = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}, "required": ["a"]}
    res = asyncio.run(_adapter(handler).complete_structured(_req(("system", "s"), ("user", "u"), schema=schema)))
    assert res.structured == {"a": 1} and seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    schema["required"] = ["a", "b"]
    with pytest.raises(ModelBadOutput):
        asyncio.run(_adapter(handler).complete_structured(_req(("system", "s"), ("user", "u"), schema=schema)))


class _Base:
    def __init__(self):
        self.seen = []

    def identity(self, role):
        return ModelIdentity(provider="openrouter", model="kimi", version="api", mode="fast")

    async def complete(self, request):
        self.seen.append(request.role)
        return ModelResponse(text="base", identity=self.identity(request.role))

    async def complete_structured(self, request):
        return await self.complete(request)

    async def embed(self, texts):
        self.seen.append("embed")


def test_router_sends_only_google_roles_to_google():
    base = _Base()
    google = _adapter(lambda r: httpx.Response(200, json=_ok()))
    ad = RoutedModelAdapter(base, google)
    assert asyncio.run(ad.complete(_req(("system", "s"), ("user", "u")))).text == "hola"
    other = ModelRequest(role=ModelRole.CLASSIFIER, messages=[ModelMessage("user", "u")])
    assert asyncio.run(ad.complete(other)).text == "base"
    asyncio.run(ad.embed(["x"]))
    assert base.seen == [ModelRole.CLASSIFIER, "embed"]
    assert ad.identity(ModelRole.CONVERSATION).provider == "google" and ad.identity(ModelRole.CLASSIFIER).provider == "openrouter"


def test_thought_parts_come_back_as_reasoning():
    res = asyncio.run(_adapter(lambda r: httpx.Response(200, json=_ok())).complete(_req(("system", "s"), ("user", "u"))))
    assert res.text == "hola" and res.reasoning == "pienso"
