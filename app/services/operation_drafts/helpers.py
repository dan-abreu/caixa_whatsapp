import re
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        "ouro", "fundido", "fundida", "queimado", "queimada", "compra", "comprar", "comprei", "venda", "vender", "vendi",
        "grama", "gramas", "teor", "usd", "dolar", "dolares", "eur", "euro", "euros", "srd", "brl", "real", "reais",
        "pago", "paguei", "recebi", "recebido", "cliente",
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
            if not candidate_tokens or any(token in stop_tokens for token in candidate_tokens):
                continue
            if normalized_candidate in normalized_compact and normalized_candidate.startswith("ouro "):
                continue
            matches.append(candidate)
    return matches[-1] if matches else ""


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

    currency_aliases = {"usd": "USD", "dolar": "USD", "dolares": "USD", "eur": "EUR", "euro": "EUR", "euros": "EUR", "srd": "SRD", "brl": "BRL", "real": "BRL", "reais": "BRL"}
    payments: List[Dict[str, str]] = []
    for amount_raw, currency_raw in re.findall(r"(\d+(?:[\.,]\d+)?)\s*(usd|dolar|dolares|eur|euro|euros|srd|brl|real|reais)\b", segment, flags=re.IGNORECASE):
        moeda = currency_aliases.get(normalize_text(currency_raw), "")
        if not moeda:
            continue
        valor = parse_decimal_from_text(amount_raw, "payment_amount")
        if valor <= 0:
            continue
        payments.append({
            "moeda": moeda,
            "valor": format_decimal_for_form(money(valor), 2),
            "cambio": "1" if moeda == "USD" else str(recent_fx.get(moeda) or ""),
            "forma": "dinheiro",
        })
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