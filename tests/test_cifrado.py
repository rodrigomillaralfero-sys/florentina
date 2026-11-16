"""F0.8 — mente cifrada: el codec, los campos sellados de eventos y la politica de la instancia."""

from __future__ import annotations

import base64

import pytest

from core.mind.cipher import (
    PLAIN,
    PREFIX,
    AesGcmCodec,
    Mind,
    MindKeyMismatch,
    MindKeyMissing,
    MindTampered,
    is_sealed,
    new_key,
    parse_key,
)
from core.assembly import build_mind

KEY = new_key()


def codec(key: str = KEY) -> AesGcmCodec:
    return AesGcmCodec.from_b64(key)


def test_roundtrip_text_and_json_with_aad():
    c = codec()
    sealed = c.seal_text("salir a caminar antes del viernes", aad="i|goals.objective")
    assert sealed.startswith(PREFIX) and "caminar" not in sealed
    assert c.open_text(sealed, aad="i|goals.objective") == "salir a caminar antes del viernes"
    ladder = {"rungs": [{"level": 2, "meaning": "x", "day": 1, "cost": {"tiempo": 1}}]}
    sj = c.seal_json(ladder, aad="i|goals.ladder")
    assert is_sealed(sj) and c.open_json(sj, aad="i|goals.ladder") == ladder
    # cifrado aleatorio: dos sellos del mismo texto no se parecen (nada que comparar en la base)
    assert c.seal_text("igual", aad="a") != c.seal_text("igual", aad="a")


def test_a_tampered_envelope_does_not_open():
    c = codec()
    sealed = c.seal_text("objetivo", aad="i|goals.objective")
    kid, b64 = sealed[len(PREFIX):].split(":", 1)
    blob = bytearray(base64.b64decode(b64))
    blob[-1] ^= 0x01  # un bit del tag
    with pytest.raises(MindTampered):
        c.open_text(f"{PREFIX}{kid}:{base64.b64encode(bytes(blob)).decode()}", aad="i|goals.objective")


def test_an_envelope_moved_to_another_field_or_instance_does_not_open():
    c = codec()
    sealed = c.seal_text("objetivo", aad="i|goals.objective")
    with pytest.raises(MindTampered):
        c.open_text(sealed, aad="i|strategist_memos.content")
    with pytest.raises(MindTampered):
        c.open_text(sealed, aad="otra|goals.objective")


def test_wrong_key_says_so_instead_of_looking_like_tampering():
    sealed = codec().seal_text("objetivo", aad="a")
    with pytest.raises(MindKeyMismatch):
        codec(new_key()).open_text(sealed, aad="a")


def test_legacy_plaintext_passes_and_a_process_without_key_refuses_sealed_content():
    c = codec()
    assert c.open_text("texto de antes de F0.8", aad="a") == "texto de antes de F0.8"
    assert c.open_json({"rungs": []}, aad="a") == {"rungs": []}
    sealed = c.seal_text("objetivo", aad="a")
    with pytest.raises(MindKeyMissing):
        PLAIN.open_text(sealed, aad="a")
    assert PLAIN.seal_text("x", aad="a") == "x"  # sin clave escribe en claro (la instancia decide si eso se permite)


def test_sealing_is_idempotent():
    c = codec()
    once = c.seal_text("x", aad="a")
    assert c.seal_text(once, aad="a") == once
    sj = c.seal_json({"a": 1}, aad="a")
    assert c.seal_json(sj, aad="a") == sj


def test_bad_keys_are_rejected():
    with pytest.raises(Exception):
        parse_key("corta")
    with pytest.raises(MindKeyMissing):
        parse_key("")


def test_event_fields_are_sealed_by_path_and_reopened_transparently():
    mind = Mind(codec=codec(), event_fields={"turn.intent": ("reason", "request.criterion"), "strategist.proposed": ("raw",)})
    payload = {"text": "hola", "reason": "busco que salga a caminar", "request": {"present": True, "criterion": "camina 20 min", "level": 2}}
    stored = mind.seal_event("i", "turn.intent", payload)
    assert stored["text"] == "hola", "el texto enviado al sujeto no se sella: el sujeto lo ve igual"
    assert is_sealed(stored["reason"]) and is_sealed(stored["request"]["criterion"]) and stored["request"]["level"] == 2
    assert payload["request"]["criterion"] == "camina 20 min", "sellar no muta el payload original"
    assert mind.open_event("i", "turn.intent", stored) == payload
    # otros tipos pasan intactos; rutas ausentes o nulas no rompen
    assert mind.seal_event("i", "message.sent", {"text": "x"}) == {"text": "x"}
    assert mind.seal_event("i", "turn.intent", {"text": None, "reason": None}) == {"text": None, "reason": None}
    raw = {"raw": {"caso": "x", "items": [{"id": "e1"}]}}
    assert mind.open_event("i", "case.noted", mind.seal_event("i", "case.noted", raw)) == raw
    # un evento sellado de otra instancia no abre en esta
    with pytest.raises(MindTampered):
        mind.open_event("otra", "turn.intent", stored)


def test_instance_requires_a_key_against_the_database():
    with pytest.raises(RuntimeError, match="sin clave"):
        build_mind(key_b64=None, required=True)  # base persistente: lo de las personas no se escribe en claro
    assert build_mind(key_b64=None, required=True, persistent=False).enabled is False  # en memoria no hay base que mirar
    m = build_mind(key_b64=KEY, required=True, sealed_events={"message.received": ["text"]})
    assert m.enabled and m.event_fields["message.received"] == ("text",)
