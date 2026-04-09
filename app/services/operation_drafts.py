import re
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import ValidationError

from app.ai_service import AIServiceError, extract_message_data
from app.core.formatting import money
from app.database import DatabaseClient


def _match_cliente_from_text(
    db: DatabaseClient,
    raw_name: str,
    *,
    normalize_text: Callable[[str], str],
) -> Optional[Dict[str, Any]]:
    query = str(raw_name or "").strip()
    if not query:
        return None

    normalized_query = normalize_text(query)
    candidates = db.search_clientes(query, limit=5)
    if not candidates:
        return None

    def candidate_score(cliente: Dict[str, Any]) -> Tuple[int, int, int]:
        nome = normalize_text(str(cliente.get("nome") or ""))
        apelido = normalize_text(str(cliente.get("apelido") or ""))
        exact = int(nome == normalized_query or apelido == normalized_query)
        starts = int(nome.startswith(normalized_query) or apelido.startswith(normalized_query))
        length_gap = -abs(len(nome) - len(normalized_query))
        return (exact, starts, length_gap)

    ordered = sorted(candidates, key=candidate_score, reverse=True)
    best = ordered[0]
    nome = normalize_text(str(best.get("nome") or ""))
    apelido = normalize_text(str(best.get("apelido") or ""))
    if normalized_query not in {nome, apelido} and not nome.startswith(normalized_query) and not apelido.startswith(normalized_query):
        return None
    return best


def _extract_operation_person(message: str, *, normalize_text: Callable[[str], str]) -> str:
    compact = re.sub(r"\s+", " ", message.strip())
    normalized_compact = normalize_text(compact)
    stop_tokens = {
        "ouro",
        "fundido",
        "fundida",
        "queimado",
        "queimada",
        "compra",
        "comprar",
        "comprei",
        "venda",
        "vender",
        "vendi",
        "grama",
        "gramas",
        "teor",
        "usd",
        "dolar",
        "dolares",
        "eur",
        "euro",
        "euros",
        "srd",
        "brl",
        "real",
        "reais",
        "pago",
        "paguei",
        "recebi",
        "recebido",
        "cliente",
    }
    patterns = [
        r"\b(?:do|da|de|para)\s+(?:cliente|sr\.?|sra\.?|senhor|senhora)?\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,60}?)(?=\s+(?:pago|paguei|recebi|recebido|com|em|teor|a\s+\d)|$)",
        r"\bcliente\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,60}?)(?=\s+(?:pago|paguei|recebi|recebido|com|em|teor|a\s+\d)|$)",
    ]
    matches: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
            candidate = re.sub(r"\s+", " ", match.group(1).strip(" ,.-"))
            if not candidate:
                continue
            normalized_candidate = normalize_text(candidate)
            candidate_tokens = [token for token in normalized_candidate.split() if token]
            if not candidate_tokens:
                continue
            if any(token in stop_tokens for token in candidate_tokens):
                continue
            if normalized_candidate in normalized_compact and normalized_candidate.startswith("ouro "):
                continue
            matches.append(candidate)
    if matches:
        return matches[-1]
    return ""


def _extract_operation_payments(
    message: str,
    recent_fx: Dict[str, str],
    *,
    normalize_text: Callable[[str], str],
    parse_decimal_from_text: Callable[[str, str], Decimal],
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> List[Dict[str, str]]:
    normalized = normalize_text(message)
    segment = ""
    for marker in [" pago ", " pagos ", " paguei ", " recebi ", " recebido "]:
        index = normalized.find(marker)
        if index >= 0:
            segment = normalized[index + len(marker):]
            break
    if not segment:
        return []

    currency_aliases = {
        "usd": "USD",
        "dolar": "USD",
        "dolares": "USD",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
        "srd": "SRD",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
    }
    payments: List[Dict[str, str]] = []
    for amount_raw, currency_raw in re.findall(r"(\d+(?:[\.,]\d+)?)\s*(usd|dolar|dolares|eur|euro|euros|srd|brl|real|reais)\b", segment, flags=re.IGNORECASE):
        moeda = currency_aliases.get(normalize_text(currency_raw), "")
        if not moeda:
            continue
        valor = parse_decimal_from_text(amount_raw, "payment_amount")
        if valor <= 0:
            continue
        payments.append(
            {
                "moeda": moeda,
                "valor": format_decimal_for_form(money(valor), 2),
                "cambio": "1" if moeda == "USD" else str(recent_fx.get(moeda) or ""),
                "forma": "dinheiro",
            }
        )
        if len(payments) == 4:
            break
    return payments


def _extract_gold_trade_profile_from_message(
    message: str,
    *,
    normalize_text: Callable[[str], str],
    parse_decimal_from_text: Callable[[str, str], Decimal],
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> Dict[str, str]:
    normalized = normalize_text(message)
    profile: Dict[str, str] = {"gold_type": "fundido", "quebra": ""}

    if any(token in normalized for token in {"queimado", "queimada"}):
        profile["gold_type"] = "queimado"
    elif any(token in normalized for token in {"fundido", "fundida"}):
        profile["gold_type"] = "fundido"

    quebra_patterns = [
        r"quebra\s*(?:de\s*)?(\d+(?:[\.,]\d+)?)\s*%?",
        r"(\d+(?:[\.,]\d+)?)\s*%\s*de\s*quebra",
        r"(\d+(?:[\.,]\d+)?)\s*%\s*quebra",
    ]
    for pattern in quebra_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        quebra = parse_decimal_from_text(match.group(1), "quebra_draft")
        if quebra > 0:
            profile["quebra"] = format_decimal_for_form(money(quebra), 2)
            if profile["gold_type"] != "queimado":
                profile["gold_type"] = "queimado"
            break

    return profile


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
        peso_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*g\b", normalized)
        if peso_match:
            peso = parse_decimal_from_text(peso_match.group(1), "peso_draft")

    teor = Decimal("90")
    teor_match = re.search(r"teor\s*(\d+(?:[\.,]\d+)?)", normalized)
    if teor_match:
        parsed_teor = parse_decimal_from_text(teor_match.group(1), "teor_draft")
        if parsed_teor > 0:
            teor = parsed_teor

    preco = Decimal(str(ai_data.valor_informado or "0")) if ai_data.valor_informado else Decimal("0")
    if preco <= 0:
        price_patterns = [
            r"(?:\ba\b|\bpor\b|preco(?:\s+usd)?|cotacao(?:\s+usd)?)\s*(?:de\s*)?(\d+(?:[\.,]\d+)?)\s*(?:usd|dolar|dolares)?\b",
            r"(\d+(?:[\.,]\d+)?)\s*(?:usd|dolar|dolares)\b(?:\s*/?g\b|\s+por\s+grama\b)?",
        ]
        for pattern in price_patterns:
            preco_match = re.search(pattern, normalized)
            if not preco_match:
                continue
            preco = parse_decimal_from_text(preco_match.group(1), "preco_draft")
            if preco > 0:
                break

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

    summary_parts: List[str] = []
    summary_parts.append(f"Tipo: {draft['tipo_operacao']}")
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

    return {
        "draft": draft,
        "missing_fields": missing_fields,
        "summary": " | ".join(summary_parts),
        "recent_fx": recent_fx,
    }