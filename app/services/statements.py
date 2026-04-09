from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from app.database import DatabaseClient


def _build_statement_summary(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_usd = Decimal("0")
    total_pago_usd = Decimal("0")
    total_diferenca_usd = Decimal("0")
    for item in transactions:
        total_usd += Decimal(str(item.get("total_usd") or "0"))
        total_pago_usd += Decimal(str(item.get("total_pago_usd") or "0"))
        total_diferenca_usd += Decimal(str(item.get("diferenca_usd") or "0"))
    return {
        "total_operacoes": len(transactions),
        "total_usd": str(total_usd),
        "total_pago_usd": str(total_pago_usd),
        "total_diferenca_usd": str(total_diferenca_usd),
    }


def _build_statement_summary_for_window(
    transactions: List[Dict[str, Any]],
    start_iso: str,
    end_iso: str,
) -> Dict[str, Any]:
    filtered: List[Dict[str, Any]] = []
    for item in transactions:
        created_at = str(item.get("criado_em") or "")
        if not created_at:
            continue
        if start_iso <= created_at < end_iso:
            filtered.append(item)
    return _build_statement_summary(filtered)


def _build_saas_statement_context(
    db: DatabaseClient,
    start_date: Optional[str],
    end_date: Optional[str],
    *,
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_cache_key: Callable[[str, str], str],
    get_cached_context: Callable[[str], Optional[Dict[str, Any]]],
    set_cached_context: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    build_extrato_response: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    if not start_date and not end_date:
        day = build_day_range(None)
        start_iso = day["start"]
        end_iso = day["end"]
        label = f"Hoje ({day['date']})"
        start_date = day["date"]
        end_date = day["date"]
    else:
        normalized_start = start_date or end_date
        normalized_end = end_date or start_date
        if not normalized_start or not normalized_end:
            raise HTTPException(status_code=400, detail="Informe um intervalo de datas válido.")
        start_day = build_day_range(normalized_start)
        end_day = build_day_range(normalized_end)
        if end_day["start"] < start_day["start"]:
            raise HTTPException(status_code=400, detail="A data final deve ser igual ou maior que a inicial.")
        start_iso = start_day["start"]
        end_iso = end_day["end"]
        label = f"{start_day['date']} a {end_day['date']}"
        start_date = start_day["date"]
        end_date = end_day["date"]

    cache_key = build_cache_key(start_iso, end_iso)
    cached_context = get_cached_context(cache_key)
    if cached_context is not None:
        return cached_context

    transactions = db.get_extrato_transactions(start_iso, end_iso)
    summary = _build_statement_summary(transactions)
    statement_text = build_extrato_response(db, start_iso, end_iso, label, transactions=transactions)
    context = {
        "start_iso": start_iso,
        "end_iso": end_iso,
        "start_date": start_date,
        "end_date": end_date,
        "label": label,
        "transactions": transactions,
        "summary": summary,
        "statement_text": str(statement_text.get("mensagem") or ""),
    }
    return set_cached_context(cache_key, context)