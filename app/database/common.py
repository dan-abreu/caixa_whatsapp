import hashlib
import hmac
import importlib
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import sqrt
from time import monotonic
from typing import Any, Dict, List, Optional, cast

from app.shared_cache import get_shared_cache


class DatabaseError(Exception):
    pass


logger = logging.getLogger("caixa_whatsapp")
_WEB_PIN_HASH_PREFIX = "pbkdf2_sha256"
_WEB_PIN_HASH_ITERATIONS = 260000
_CLIENT_BALANCE_CURRENCIES = ("XAU", "USD", "EUR", "SRD", "BRL")


def _safe_decimal(value: Any, default: str = "0", *, context: str = "valor") -> Decimal:
    try:
        return Decimal(str(default if value is None else value))
    except (ArithmeticError, TypeError, ValueError) as exc:
        logger.warning("Falha ao converter decimal em %s: %s", context, exc)
        return Decimal(str(default))


def _safe_decimal_from_row(row: Dict[str, Any], key: str, default: str = "0") -> Decimal:
    return _safe_decimal(row.get(key), default, context=f"row[{key}]")


def _safe_int(value: Any, default: int = 0, *, context: str = "valor") -> int:
    try:
        return int(str(default if value is None else value))
    except (TypeError, ValueError) as exc:
        logger.warning("Falha ao converter inteiro em %s: %s", context, exc)
        return default


def _hash_web_pin(pin: str, salt: Optional[str] = None) -> str:
    normalized_pin = str(pin or "").strip()
    if not normalized_pin:
        raise ValueError("PIN vazio")
    salt_value = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized_pin.encode("utf-8"),
        salt_value.encode("utf-8"),
        _WEB_PIN_HASH_ITERATIONS,
    ).hex()
    return f"{_WEB_PIN_HASH_PREFIX}${_WEB_PIN_HASH_ITERATIONS}${salt_value}${digest}"


def _verify_web_pin(pin: str, stored_hash: Optional[str]) -> bool:
    normalized_pin = str(pin or "").strip()
    if not normalized_pin or not stored_hash:
        return False
    try:
        algorithm, iterations_raw, salt_value, expected_digest = str(stored_hash).split("$", 3)
        if algorithm != _WEB_PIN_HASH_PREFIX:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            normalized_pin.encode("utf-8"),
            salt_value.encode("utf-8"),
            int(iterations_raw),
        ).hex()
        return hmac.compare_digest(digest, expected_digest)
    except (TypeError, ValueError):
        return False


def _empty_cliente_balance_snapshot() -> Dict[str, Decimal]:
    return {currency: Decimal("0") for currency in _CLIENT_BALANCE_CURRENCIES}


def _aggregate_cliente_movements(movements: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    balances = _empty_cliente_balance_snapshot()
    for row in movements:
        moeda = str(row.get("moeda") or "").upper()
        if moeda not in balances:
            continue
        balances[moeda] += _safe_decimal(row.get("valor") or "0", context="cliente_movimentacoes.valor")
    return balances


def _aggregate_cliente_movements_by_client(movements: List[Dict[str, Any]]) -> Dict[int, Dict[str, Decimal]]:
    balances_by_client: Dict[int, Dict[str, Decimal]] = {}
    for row in movements:
        cliente_id = _safe_int(row.get("cliente_id"), context="cliente_movimentacoes.cliente_id")
        if cliente_id <= 0:
            continue
        balances = balances_by_client.setdefault(cliente_id, _empty_cliente_balance_snapshot())
        moeda = str(row.get("moeda") or "").upper()
        if moeda not in balances:
            continue
        balances[moeda] += _safe_decimal(row.get("valor") or "0", context="cliente_movimentacoes.valor")
    return balances_by_client