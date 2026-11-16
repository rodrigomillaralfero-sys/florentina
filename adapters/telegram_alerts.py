"""Alertas al operador por un bot de Telegram aparte (solo salida).

Es otro bot, con otro token, que escribe al chat del operador. Nunca lee
mensajes ni habla con el sujeto: no tiene handlers, no hace polling. Por eso
puede convivir con el bot del sujeto y con cualquier otro proceso.

Tambien trae el chequeo de doctor para un token de Telegram (`getMe`).
"""

from __future__ import annotations

import logging

import httpx

from core.health.doctor import Check

log = logging.getLogger(__name__)

API = "https://api.telegram.org"


class TelegramAlertSink:
    def __init__(self, *, token: str, chat_id: str, timeout_seconds: float = 15.0) -> None:
        self._token = token
        self._chat_id = str(chat_id)
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "telegram-ops"

    async def emit(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{API}/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text[:4000], "disable_web_page_preview": True},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"telegram sendMessage {resp.status_code}: {resp.text[:200]}")


def telegram_bot_check(name: str, token: str | None, *, timeout_seconds: float = 15.0):
    """Chequeo del doctor: el token responde a getMe."""

    async def run() -> Check:
        if not token:
            return Check(name, False, "sin token configurado", severity="warning")
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(f"{API}/bot{token}/getMe")
        if resp.status_code != 200:
            return Check(name, False, f"getMe {resp.status_code}: token invalido o revocado")
        username = (resp.json().get("result") or {}).get("username", "?")
        return Check(name, True, f"@{username}")

    return run
