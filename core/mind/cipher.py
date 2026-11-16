"""Cifrado en reposo de lo que el motor guarda de cada persona.

Los textos sensibles (episodios de conversacion y los campos de eventos que la instancia marque) se
guardan cifrados con una clave que vive en el entorno de ejecucion. La base guarda un sobre
`enc:v1:<kid>:<base64(nonce|cifrado)>`; solo el motor, con la clave, lo abre.

- **AES-256-GCM.** Autenticado: una fila alterada (un byte cambiado en el sobre) no se abre: falla
  con `MindTampered`.
- **Datos asociados (AAD)** = instancia + campo (`episodes.text`, `events:<tipo>:<ruta>`...):
  un sobre copiado a otro campo u otra instancia tampoco abre.
- **kid** = 8 hex del sha256 de la clave. Si la clave cambio, el error lo dice (`MindKeyMismatch`)
  en vez de parecer una alteracion.
- **Legado en claro.** Un valor sin sobre se devuelve tal cual. La auditoria lo cuenta sin mostrarlo.
- **Sin clave** (`PlainCodec`): escribe en claro y, si encuentra un sobre, falla con `MindKeyMissing`
  en vez de devolver basura. Una instancia que exige cifrado no arranca sin clave (lo decide el cableado).

Toda lectura de contenido fuera del motor pasa por el registro de accesos (`core.mind.access`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

PREFIX = "enc:v1:"
JSON_KEY = "enc"  # una columna jsonb sellada guarda {"enc": "<sobre>"}


class MindError(RuntimeError):
    """Base de los errores del cifrado. Nunca llevan contenido."""


class MindTampered(MindError):
    """El sobre no abre con la clave vigente: fue alterado (o copiado a otro campo)."""


class MindKeyMismatch(MindError):
    """El sobre se cifro con otra clave (kid distinto)."""


class MindKeyMissing(MindError):
    """Hay contenido cifrado y este proceso no tiene clave."""


class MindCodec(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def key_id(self) -> str | None: ...

    def seal_text(self, value: str | None, *, aad: str) -> str | None: ...

    def open_text(self, value: str | None, *, aad: str) -> str | None: ...

    def seal_json(self, value: Any, *, aad: str) -> Any: ...

    def open_json(self, value: Any, *, aad: str) -> Any: ...


def is_sealed(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith(PREFIX)
    if isinstance(value, Mapping):
        return set(value.keys()) == {JSON_KEY} and isinstance(value.get(JSON_KEY), str) and value[JSON_KEY].startswith(PREFIX)
    return False


def new_key() -> str:
    """Una clave nueva (32 bytes) en base64 url-safe. Para `ops mind keygen`: se escribe al .env, nunca a pantalla."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def parse_key(raw: str) -> bytes:
    raw = (raw or "").strip()
    if not raw:
        raise MindKeyMissing("clave vacia")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as exc:
        raise MindError("la clave no es base64 valido") from exc
    if len(key) != 32:
        raise MindError(f"la clave debe tener 32 bytes (tiene {len(key)})")
    return key


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


class PlainCodec:
    """Sin clave: escribe en claro; un sobre al leer es un error, nunca basura."""

    enabled = False
    key_id = None

    def seal_text(self, value: str | None, *, aad: str) -> str | None:
        return value

    def open_text(self, value: str | None, *, aad: str) -> str | None:
        if isinstance(value, str) and value.startswith(PREFIX):
            raise MindKeyMissing(f"{aad}: contenido cifrado y este proceso no tiene MIND_KEY")
        return value

    def seal_json(self, value: Any, *, aad: str) -> Any:
        return value

    def open_json(self, value: Any, *, aad: str) -> Any:
        if is_sealed(value):
            raise MindKeyMissing(f"{aad}: contenido cifrado y este proceso no tiene MIND_KEY")
        return value


class AesGcmCodec:
    def __init__(self, key: bytes, *, accept_plaintext: bool = True) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # import diferido: solo quien cifra lo necesita

        if len(key) != 32:
            raise MindError("AES-256-GCM exige una clave de 32 bytes")
        self._aead = AESGCM(key)
        self._kid = key_id(key)
        # r2: con `accept_plaintext=False`, un campo cifrado que aparece en claro es una alteracion (se escribio
        # por fuera del motor), no legado.
        self.accept_plaintext = accept_plaintext

    @classmethod
    def from_b64(cls, raw: str, *, accept_plaintext: bool = True) -> "AesGcmCodec":
        return cls(parse_key(raw), accept_plaintext=accept_plaintext)

    @property
    def enabled(self) -> bool:
        return True

    @property
    def key_id(self) -> str:
        return self._kid

    def seal_text(self, value: str | None, *, aad: str) -> str | None:
        if value is None or (isinstance(value, str) and value.startswith(PREFIX)):
            return value  # nada que cifrar, o ya sellado (idempotente)
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
        return f"{PREFIX}{self._kid}:{base64.b64encode(nonce + ct).decode('ascii')}"

    def open_text(self, value: str | None, *, aad: str) -> str | None:
        if not isinstance(value, str) or not value.startswith(PREFIX):
            if value and not self.accept_plaintext:
                raise MindTampered(f"{aad}: texto en claro en un campo cifrado (la instancia ya no acepta legado)")
            return value  # legado en claro (o nulo)
        from cryptography.exceptions import InvalidTag

        body = value[len(PREFIX):]
        kid, sep, b64 = body.partition(":")
        if not sep:
            raise MindTampered(f"{aad}: sobre mal formado")
        if kid != self._kid:
            raise MindKeyMismatch(f"{aad}: cifrado con la clave {kid}, la vigente es {self._kid}")
        try:
            blob = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise MindTampered(f"{aad}: sobre mal formado") from exc
        if len(blob) < 12 + 16:
            raise MindTampered(f"{aad}: sobre truncado")
        try:
            return self._aead.decrypt(blob[:12], blob[12:], aad.encode("utf-8")).decode("utf-8")
        except InvalidTag as exc:
            raise MindTampered(f"{aad}: el contenido no autentica (alterado o fuera de su campo)") from exc

    def seal_json(self, value: Any, *, aad: str) -> Any:
        if value is None or is_sealed(value):
            return value
        return {JSON_KEY: self.seal_text(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str), aad=aad)}

    def open_json(self, value: Any, *, aad: str) -> Any:
        if not is_sealed(value):
            if value is not None and not self.accept_plaintext:
                raise MindTampered(f"{aad}: valor en claro en un campo cifrado (la instancia ya no acepta legado)")
            return value
        return json.loads(self.open_text(value[JSON_KEY], aad=aad) or "null")


PLAIN = PlainCodec()


# ---------------------------------------------------------------------- campos sellados en eventos


@dataclass(frozen=True)
class Mind:
    """La politica de cifrado de una instancia: el codec y que campos de que eventos van sellados.

    `event_fields` mapea tipo de evento -> rutas con punto (`message.received` -> `text`). El almacen de eventos
    sella esas rutas al escribir y las abre al leer: quien lee un evento no se entera."""

    codec: MindCodec = PLAIN
    event_fields: Mapping[str, Sequence[str]] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.codec.enabled

    def seal_event(self, instance_id: str, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        paths = self.event_fields.get(kind)
        out = dict(payload)
        if not paths or not self.codec.enabled:
            return out
        for path in paths:
            _apply(out, path.split("."), lambda v, p=path: self.codec.seal_json(v, aad=_event_aad(instance_id, kind, p)))
        return out

    def open_event(self, instance_id: str, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(payload or {})
        for path in self.event_fields.get(kind, ()):
            _apply(out, path.split("."), lambda v, p=path: self.codec.open_json(v, aad=_event_aad(instance_id, kind, p)))
        return out

    def text_aad(self, instance_id: str, column: str) -> str:
        return f"{instance_id}|{column}"


def _event_aad(instance_id: str, kind: str, path: str) -> str:
    return f"{instance_id}|events:{kind}:{path}"


def _apply(obj: dict[str, Any], parts: list[str], fn) -> None:
    """Aplica `fn` al valor en la ruta (si existe y no es nulo), copiando los dicts intermedios."""
    head, rest = parts[0], parts[1:]
    if head not in obj or obj[head] is None:
        return
    if not rest:
        obj[head] = fn(obj[head])
        return
    child = obj[head]
    if not isinstance(child, Mapping):
        return
    child = dict(child)
    obj[head] = child
    _apply(child, rest, fn)


def codec_from_env(env: Mapping[str, str], var: str = "MIND_KEY") -> MindCodec:
    raw = (env.get(var) or "").strip()
    return AesGcmCodec.from_b64(raw) if raw else PLAIN
