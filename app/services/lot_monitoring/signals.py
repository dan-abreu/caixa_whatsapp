from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, cast

from app.core.formatting import money


def _extract_lot_monitor_config(lot: Dict[str, Any]) -> Dict[str, Any]:
    metadata = cast(Dict[str, Any], lot.get("metadata") or {})
    return cast(Dict[str, Any], metadata.get("monitor") or {})


def _build_lot_sell_signal(lot: Dict[str, Any], market_trend: Dict[str, Any]) -> Dict[str, Any]:
    monitor = _extract_lot_monitor_config(lot)
    try:
        current_unit = Decimal(str(lot.get("market_unit_usd") or "0"))
        cost_unit = Decimal(str(lot.get("unit_cost_usd") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        current_unit = Decimal("0")
        cost_unit = Decimal("0")

    target_price = Decimal(str(monitor.get("target_price_usd") or "0")) if str(monitor.get("target_price_usd") or "").strip() else Decimal("0")
    min_profit_pct = Decimal(str(monitor.get("min_profit_pct") or "4")) if str(monitor.get("min_profit_pct") or "").strip() else Decimal("4")
    profit_pct = ((current_unit - cost_unit) / cost_unit * Decimal("100")) if cost_unit > 0 else Decimal("0")
    min_profit_gap_pct = money(profit_pct - min_profit_pct)
    target_hit = target_price > 0 and current_unit >= target_price
    signal_name = str(market_trend.get("signal") or "neutral")
    ai_window = profit_pct >= min_profit_pct and signal_name in {"bullish", "constructive"}
    protect_profit = signal_name == "bearish" and profit_pct > 0 and (profit_pct >= (min_profit_pct * Decimal("0.75")) or target_hit)

    status = "aguardar"
    status_class = "neutral"
    reason = "Mercado ainda sem vantagem estatistica para agir."
    if target_hit:
        status = "limite_atingido"
        status_class = "positive"
        reason = "Preco alvo atingido; execucao de venda ja pode ser considerada."
    elif ai_window:
        status = "janela_favoravel"
        status_class = "positive"
        reason = "Lucro acima do minimo com tendencia construtiva; janela assistida de saida aberta."
    elif protect_profit:
        status = "proteger_lucro"
        status_class = "negative"
        reason = "O lote ainda tem ganho, mas a leitura de curto prazo perdeu forca; convem proteger lucro."

    return {
        "enabled": bool(monitor.get("enabled")),
        "notify_phone": str(monitor.get("notify_phone") or ""),
        "target_price_usd": str(target_price if target_price > 0 else ""),
        "min_profit_pct": str(min_profit_pct),
        "min_profit_gap_pct": str(min_profit_gap_pct),
        "status": status,
        "status_class": status_class,
        "reason": reason,
        "profit_pct": str(money(profit_pct)),
        "should_alert": bool(monitor.get("enabled")) and (target_hit or ai_window or protect_profit),
        "alert_signature": f"{status}|{str(current_unit)}|{str(target_price)}|{str(money(profit_pct))}",
    }


def _format_lot_signal_status(status: str) -> str:
    labels = {
        "aguardar": "Aguardar",
        "limite_atingido": "Limite atingido",
        "janela_favoravel": "Janela favoravel",
        "proteger_lucro": "Proteger lucro",
    }
    return labels.get(str(status or "aguardar"), str(status or "aguardar").replace("_", " ").title())


def _build_web_lot_ai_alert_summary(alerts: List[Dict[str, Any]]) -> str:
    if not alerts:
        return "Nenhum monitor de lote com gatilho ativo agora."
    lead = alerts[0]
    if len(alerts) == 1:
        return (
            f"GT-{lead.get('source_transaction_id', '-')}: {lead.get('status_label', 'Alerta')} com "
            f"USD {lead.get('market_unit_usd', '0')}/g e lucro aberto de {lead.get('profit_pct', '0')}%."
        )
    return (
        f"{len(alerts)} monitores ativos. Destaque: GT-{lead.get('source_transaction_id', '-')} em "
        f"{str(lead.get('status_label') or 'alerta').lower()}."
    )


def _build_web_lot_ai_alerts(
    lot_market_context: Dict[str, Any],
    market_trend: Dict[str, Any],
    *,
    build_lot_sell_signal: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    format_lot_signal_status: Callable[[str], str],
    limit: int = 4,
) -> List[Dict[str, Any]]:
    priority = {"limite_atingido": 0, "janela_favoravel": 1, "proteger_lucro": 2}
    alerts: List[Dict[str, Any]] = []
    for lot in cast(List[Dict[str, Any]], lot_market_context.get("lots") or []):
        signal = build_lot_sell_signal(lot, market_trend)
        if not signal.get("enabled"):
            continue
        status = str(signal.get("status") or "aguardar")
        if status == "aguardar":
            continue
        lot_id = int(lot.get("id") or 0)
        source_transaction_id = int(lot.get("source_transaction_id") or lot.get("source_id") or 0)
        signature = f"lot:{lot_id}|{signal.get('alert_signature') or status}"
        alerts.append(
            {
                "lot_id": lot_id,
                "source_transaction_id": source_transaction_id,
                "status": status,
                "status_label": format_lot_signal_status(status),
                "status_class": str(signal.get("status_class") or "neutral"),
                "reason": str(signal.get("reason") or "-"),
                "profit_pct": str(signal.get("profit_pct") or "0"),
                "remaining_grams": str(lot.get("remaining_grams") or "0"),
                "market_unit_usd": str(lot.get("market_unit_usd") or "0"),
                "unrealized_pnl_usd": str(lot.get("unrealized_pnl_usd") or "0"),
                "teor": str(lot.get("teor") or "-"),
                "signature": signature,
                "message": (
                    f"IA web: GT-{source_transaction_id} em {format_lot_signal_status(status).lower()}. "
                    f"Mercado USD {lot.get('market_unit_usd', '0')}/g, P/L USD {lot.get('unrealized_pnl_usd', '0')} "
                    f"({signal.get('profit_pct', '0')}%), folga sobre minimo {signal.get('min_profit_gap_pct', '0')} p.p. {signal.get('reason', '-')}"
                ),
            }
        )

    alerts.sort(
        key=lambda item: (
            priority.get(str(item.get("status") or "aguardar"), 9),
            -Decimal(str(item.get("unrealized_pnl_usd") or "0")),
            int(item.get("lot_id") or 0),
        )
    )
    return alerts[:limit]