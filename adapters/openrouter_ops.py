"""Operacion contra OpenRouter: saldo, tope de la key y chequeo del doctor.

No es parte del adaptador de modelo (que solo infiere): son las llamadas de
cuenta que el operador y el doctor necesitan para saber si hay con que pagar
el proximo turno.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from core.health.doctor import Check


@dataclass(frozen=True)
class Credits:
    total_usd: float
    used_usd: float
    key_limit_usd: float | None
    key_usage_usd: float
    key_expires_at: str | None

    @property
    def remaining_usd(self) -> float:
        remaining = self.total_usd - self.used_usd
        if self.key_limit_usd is not None:
            remaining = min(remaining, self.key_limit_usd - self.key_usage_usd)
        return remaining

    def render(self) -> str:
        key = f"tope de la key US${self.key_limit_usd:.2f}" if self.key_limit_usd is not None else "key sin tope"
        return (
            f"saldo US${self.total_usd:.2f} cargados, US${self.used_usd:.4f} usados → disponible US${self.remaining_usd:.4f}; "
            f"{key}, usados por la key US${self.key_usage_usd:.4f}"
            + (f", vence {self.key_expires_at[:10]}" if self.key_expires_at else "")
        )


async def fetch_credits(api_key: str, *, base_url: str = "https://openrouter.ai/api/v1", timeout_seconds: float = 20.0) -> Credits:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        credits = await client.get(f"{base_url.rstrip('/')}/credits", headers=headers)
        key = await client.get(f"{base_url.rstrip('/')}/auth/key", headers=headers)
    if credits.status_code != 200:
        raise RuntimeError(f"credits {credits.status_code}: {credits.text[:200]}")
    if key.status_code != 200:
        raise RuntimeError(f"auth/key {key.status_code}: {key.text[:200]}")
    c = credits.json().get("data") or {}
    k = key.json().get("data") or {}
    return Credits(
        total_usd=float(c.get("total_credits") or 0.0),
        used_usd=float(c.get("total_usage") or 0.0),
        key_limit_usd=float(k["limit"]) if k.get("limit") is not None else None,
        key_usage_usd=float(k.get("usage") or 0.0),
        key_expires_at=k.get("expires_at"),
    )


def openrouter_check(api_key: str | None, *, min_remaining_usd: float, base_url: str = "https://openrouter.ai/api/v1"):
    """Chequeo del doctor: la key responde y queda saldo por encima del minimo."""

    async def run() -> Check:
        if not api_key:
            return Check("proveedor", False, "sin API key configurada")
        c = await fetch_credits(api_key, base_url=base_url)
        if c.remaining_usd <= 0:
            return Check("proveedor", False, f"sin saldo: {c.render()}")
        if c.remaining_usd < min_remaining_usd:
            return Check("proveedor", False, f"saldo bajo (< US${min_remaining_usd:.2f}): {c.render()}", severity="warning")
        return Check("proveedor", True, f"disponible US${c.remaining_usd:.2f}")

    return run
