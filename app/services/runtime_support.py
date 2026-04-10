import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any


logger = logging.getLogger("caixa_whatsapp")


def build_runtime_support_helpers(*, http_exception_cls: Any) -> SimpleNamespace:
    def normalize_text(value: str) -> str:
        lowered = value.strip().lower()
        normalized = unicodedata.normalize("NFD", lowered)
        return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

    def parse_decimal(value: Any, field_name: str) -> Decimal:
        try:
            parsed = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise http_exception_cls(status_code=400, detail=f"Valor invalido para {field_name}")
        if not parsed.is_finite():
            raise http_exception_cls(status_code=400, detail=f"Valor invalido para {field_name}")
        return parsed

    def parse_decimal_from_text(value: str, field_name: str) -> Decimal:
        cleaned = value.strip().replace(" ", "")
        cleaned = cleaned.replace(",", ".")
        cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
        if cleaned in {"", "-", ".", "-.", ".-"}:
            return Decimal("-1")
        try:
            return parse_decimal(cleaned, field_name)
        except http_exception_cls:
            return Decimal("-1")

    def navigation_hint() -> str:
        return "\n\nDigite voltar para retornar ou cancelar para encerrar."

    def format_caixa_movement(currency: str, movement: Decimal) -> str:
        signal = "+" if movement >= 0 else "-"
        magnitude = abs(movement)
        if currency == "XAU":
            return f"{signal}{magnitude:,.3f} g"
        if currency == "USD":
            return f"{signal}$ {magnitude:,.2f}"
        if currency == "EUR":
            return f"{signal}EUR {magnitude:,.2f}"
        if currency == "SRD":
            return f"{signal}SRD {magnitude:,.2f}"
        if currency == "BRL":
            return f"{signal}R$ {magnitude:,.2f}"
        return f"{signal}{currency} {magnitude:,.2f}"

    def normalize_user_phone(raw: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return ""
        return f"+{digits}"

    def format_datetime_pt_br(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "-"
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
            dt_local = dt + timedelta(hours=tz_offset_hours)
            return dt_local.strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError) as exc:
            logger.warning("Falha ao formatar datetime '%s': %s", raw, exc)
            return raw

    return SimpleNamespace(
        normalize_text=normalize_text,
        parse_decimal=parse_decimal,
        parse_decimal_from_text=parse_decimal_from_text,
        navigation_hint=navigation_hint,
        format_caixa_movement=format_caixa_movement,
        normalize_user_phone=normalize_user_phone,
        format_datetime_pt_br=format_datetime_pt_br,
    )