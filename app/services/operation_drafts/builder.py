import re
from decimal import Decimal
from typing import Any, Callable, Dict, List

from fastapi import HTTPException
from pydantic import ValidationError

from app.ai_service import AIServiceError, extract_message_data
from app.core.formatting import money
from app.database import DatabaseClient

from .helpers import (
    _extract_gold_trade_profile_from_message,
    _extract_operation_payments,
    _extract_operation_person,
    _match_cliente_from_text,
)


_WEIGHT_PATTERN = re.compile(r"(\d+(?:[\.,]\d+)?)\s*g\b")
_TEOR_PATTERN = re.compile(r"teor\s*(\d+(?:[\.,]\d+)?)")
_PRICE_PATTERNS = (
    re.compile(r"(?:\ba\b|\bpor\b|preco(?:\s+usd)?|cotacao(?:\s+usd)?)\s*(?:de\s*)?(\d+(?:[\.,]\d+)?)\s*(?:usd|dolar|dolares)?\b"),
    re.compile(r"(\d+(?:[\.,]\d+)?)\s*(?:usd|dolar|dolares)\b(?:\s*/?g\b|\s+por\s+grama\b)?"),
)


def _extract_weight_from_message(normalized: str, parse_decimal_from_text: Callable[[str, str], Decimal]) -> Decimal:
    peso_match = _WEIGHT_PATTERN.search(normalized)
    if not peso_match:
        return Decimal("0")
    return parse_decimal_from_text(peso_match.group(1), "peso_draft")


def _extract_teor_from_message(normalized: str, parse_decimal_from_text: Callable[[str, str], Decimal]) -> Decimal:
    teor_match = _TEOR_PATTERN.search(normalized)
    if not teor_match:
        return Decimal("90")

    parsed_teor = parse_decimal_from_text(teor_match.group(1), "teor_draft")
    return parsed_teor if parsed_teor > 0 else Decimal("90")


def _extract_price_from_message(normalized: str, parse_decimal_from_text: Callable[[str, str], Decimal]) -> Decimal:
    for pattern in _PRICE_PATTERNS:
        preco_match = pattern.search(normalized)
        if not preco_match:
            continue
        preco = parse_decimal_from_text(preco_match.group(1), "preco_draft")
        if preco > 0:
            return preco
    return Decimal("0")


def _build_operation_draft_from_message(
    db: DatabaseClient,
    session_user: Dict[str, Any],
    message: str,
    *,
    normalize_text: Callable[[str], str],
    build_recent_fx_map: Callable[[DatabaseClient], Dict[str, str]],
    ai_extracted_data_cls: Any,
    dashboard_default_form_values: Callable[[Dict[str, Any]], Dict[str, str]],
    infer_tipo_operacao: Callable[[str], str],
    parse_decimal_from_text: Callable[[str, str], Decimal],
    format_decimal_for_form: Callable[[Decimal, int], str],
    payment_input_to_usd: Callable[[str, Decimal, Decimal], Decimal],
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
) -> Dict[str, Any]:
    text = str(message or "").strip()
    normalized = normalize_text(text)
    if not text:
        raise HTTPException(status_code=400, detail="Descreva a ordem para montar o rascunho.")

    looks_like_operation = any(token in normalized for token in {"compra", "comprei", "comprar", "venda", "vendi", "vender"})
    recent_fx = build_recent_fx_map(db)

    try:
        raw_ai_data = extract_message_data(text)
        ai_data = ai_extracted_data_cls.model_validate(raw_ai_data)
    except (AIServiceError, ValidationError, HTTPException, ValueError):
        ai_data = ai_extracted_data_cls(intencao="registrar_operacao" if looks_like_operation else "conversar")

    if ai_data.intencao != "registrar_operacao" and not looks_like_operation:
        raise HTTPException(status_code=400, detail="Nao identifiquei uma operacao nessa frase. Descreva compra ou venda com peso, preco ou pagamento.")

    draft = dict(dashboard_default_form_values(session_user))
    draft["tipo_operacao"] = infer_tipo_operacao(text)
    draft["origem"] = "fora" if "fora" in normalized else "balcao"
    trade_profile = _extract_gold_trade_profile_from_message(
        text,
        normalize_text=normalize_text,
        parse_decimal_from_text=parse_decimal_from_text,
        format_decimal_for_form=format_decimal_for_form,
    )
    draft["gold_type"] = trade_profile["gold_type"]
    draft["quebra"] = trade_profile["quebra"]

    peso = Decimal(str(ai_data.quantidade or "0")) if ai_data.quantidade else Decimal("0")
    if peso <= 0:
        peso = _extract_weight_from_message(normalized, parse_decimal_from_text)

    teor = _extract_teor_from_message(normalized, parse_decimal_from_text)

    preco = Decimal(str(ai_data.valor_informado or "0")) if ai_data.valor_informado else Decimal("0")
    if preco <= 0:
        preco = _extract_price_from_message(normalized, parse_decimal_from_text)

    pessoa = _extract_operation_person(text, normalize_text=normalize_text)
    cliente_match = _match_cliente_from_text(db, pessoa, normalize_text=normalize_text) if pessoa else None
    payments = _extract_operation_payments(
        text,
        recent_fx,
        normalize_text=normalize_text,
        parse_decimal_from_text=parse_decimal_from_text,
        format_decimal_for_form=format_decimal_for_form,
    )

    missing_fields: List[str] = []
    if peso > 0:
        draft["peso"] = format_decimal_for_form(peso, 3)
        draft["fechamento_gramas"] = format_decimal_for_form(peso, 3)
        draft["fechamento_tipo"] = "total"
    else:
        missing_fields.append("peso")

    draft["teor"] = format_decimal_for_form(teor, 2)

    if preco > 0:
        draft["preco_usd"] = format_decimal_for_form(money(preco), 2)
    else:
        missing_fields.append("preco_usd")

    if pessoa:
        draft["pessoa"] = pessoa
    else:
        missing_fields.append("cliente")

    if cliente_match:
        draft["cliente_id"] = str(cliente_match.get("id") or "")
        draft["cliente_lookup_meta"] = build_cliente_lookup_meta(cliente_match)
        draft["pessoa"] = str(cliente_match.get("nome") or pessoa)

    for index, payment in enumerate(payments, start=1):
        draft[f"payment_{index}_moeda"] = payment["moeda"]
        draft[f"payment_{index}_valor"] = payment["valor"]
        draft[f"payment_{index}_cambio"] = payment["cambio"]
        draft[f"payment_{index}_forma"] = payment["forma"]

    if payments:
        total_pago = Decimal("0")
        for payment in payments:
            valor = Decimal(str(payment["valor"]))
            cambio = Decimal(str(payment["cambio"] or "0")) if payment["moeda"] != "USD" else Decimal("1")
            total_pago += payment_input_to_usd(payment["moeda"], valor, cambio)
        draft["total_pago_usd"] = format_decimal_for_form(money(total_pago), 2)

    summary_parts: List[str] = [f"Tipo: {draft['tipo_operacao']}"]
    if draft.get("peso"):
        summary_parts.append(f"Peso: {draft['peso']} g")
    if draft.get("preco_usd"):
        summary_parts.append(f"Preco: USD {draft['preco_usd']}/g")
    if draft.get("pessoa"):
        summary_parts.append(f"Cliente: {draft['pessoa']}")
    if draft.get("cliente_lookup_meta"):
        summary_parts.append(f"Conta: {draft['cliente_lookup_meta']}")
    if draft.get("gold_type"):
        material_label = str(draft["gold_type"])
        if draft.get("quebra"):
            summary_parts.append(f"Material: {material_label} ({draft['quebra']}% quebra)")
        else:
            summary_parts.append(f"Material: {material_label}")
    if payments:
        summary_parts.append("Pagamentos: " + ", ".join(f"{item['moeda']} {item['valor']}" for item in payments))

    return {"draft": draft, "missing_fields": missing_fields, "summary": " | ".join(summary_parts), "recent_fx": recent_fx}