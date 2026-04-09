import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any, Callable, Dict, List, cast

import requests

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


def _build_web_lot_monitor_view_model(
    lot_market_context: Dict[str, Any],
    market_trend: Dict[str, Any],
    *,
    build_lot_sell_signal: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    format_lot_signal_status: Callable[[str], str],
    build_alert_summary: Callable[[List[Dict[str, Any]]], str],
    default_alert_phone: str = "",
    entry_limit: int = 8,
    alert_limit: int = 4,
) -> Dict[str, Any]:
    priority = {"limite_atingido": 0, "janela_favoravel": 1, "proteger_lucro": 2}
    alerts: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []

    for lot in cast(List[Dict[str, Any]], lot_market_context.get("lots") or []):
        signal = build_lot_sell_signal(lot, market_trend)
        lot_id = int(lot.get("id") or 0)
        source_transaction_id = int(lot.get("source_transaction_id") or lot.get("source_id") or 0)

        if entry_limit > 0 and len(entries) < entry_limit:
            current_unit = Decimal(str(lot.get("market_unit_usd") or "0"))
            entry_unit = Decimal(str(lot.get("unit_cost_usd") or "0"))
            target_unit = Decimal(str(signal.get("target_price_usd") or "0")) if str(signal.get("target_price_usd") or "").strip() else Decimal("0")
            target_gap = money(target_unit - current_unit) if target_unit > 0 else Decimal("0")
            progress_pct = Decimal("0")
            if target_unit > 0 and target_unit > entry_unit:
                progress_pct = ((current_unit - entry_unit) / (target_unit - entry_unit)) * Decimal("100")
            elif target_unit > 0 and current_unit >= target_unit:
                progress_pct = Decimal("100")
            progress_pct = max(Decimal("0"), min(Decimal("100"), progress_pct))
            trend_signal = str(market_trend.get("signal") or "neutral")
            trend_label = {
                "bullish": "Alta curta",
                "constructive": "Alta moderada",
                "bearish": "Perdendo forca",
                "neutral": "Lateral",
            }.get(trend_signal, trend_signal.replace("_", " ").title())
            action_label = {
                "limite_atingido": "Executar venda",
                "janela_favoravel": "Preparar saida",
                "proteger_lucro": "Reduzir risco",
                "aguardar": "Aguardar",
            }.get(str(signal.get("status") or "aguardar"), "Aguardar")
            entries.append(
                {
                    "id": lot_id,
                    "source_transaction_id": source_transaction_id,
                    "remaining_grams": str(lot.get("remaining_grams") or "0"),
                    "teor": str(lot.get("teor") or "-"),
                    "hold_days": str(lot.get("hold_days") or "0"),
                    "entry_unit_usd": str(money(entry_unit)),
                    "market_unit_usd": str(lot.get("market_unit_usd") or "0"),
                    "target_unit_usd": str(target_unit if target_unit > 0 else ""),
                    "target_gap_usd": str(target_gap),
                    "target_progress_pct": str(money(progress_pct)),
                    "unrealized_pnl_usd": str(lot.get("unrealized_pnl_usd") or "0"),
                    "profit_pct": str(signal.get("profit_pct") or "0"),
                    "min_profit_gap_pct": str(signal.get("min_profit_gap_pct") or "0"),
                    "reason": str(signal.get("reason") or "-"),
                    "status": str(signal.get("status") or "aguardar"),
                    "status_label": format_lot_signal_status(str(signal.get("status") or "aguardar")),
                    "action_label": action_label,
                    "status_class": str(signal.get("status_class") or "neutral"),
                    "trend_signal": trend_signal,
                    "trend_label": trend_label,
                    "enabled": bool(signal.get("enabled")),
                    "notify_phone": str(signal.get("notify_phone") or default_alert_phone),
                    "target_price_usd": str(signal.get("target_price_usd") or ""),
                    "min_profit_pct": str(signal.get("min_profit_pct") or "4"),
                }
            )

        if not signal.get("enabled"):
            continue
        status = str(signal.get("status") or "aguardar")
        if status == "aguardar":
            continue
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
    limited_alerts = alerts[:alert_limit] if alert_limit > 0 else []
    return {
        "alerts": limited_alerts,
        "entries": entries,
        "summary": build_alert_summary(limited_alerts),
    }


def _build_web_lot_monitor_entries(
    lot_market_context: Dict[str, Any],
    market_trend: Dict[str, Any],
    *,
    build_lot_sell_signal: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    format_lot_signal_status: Callable[[str], str],
    default_alert_phone: str = "",
    limit: int = 8,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for lot in cast(List[Dict[str, Any]], lot_market_context.get("lots") or [])[:limit]:
        signal = build_lot_sell_signal(lot, market_trend)
        current_unit = Decimal(str(lot.get("market_unit_usd") or "0"))
        entry_unit = Decimal(str(lot.get("unit_cost_usd") or "0"))
        target_unit = Decimal(str(signal.get("target_price_usd") or "0")) if str(signal.get("target_price_usd") or "").strip() else Decimal("0")
        target_gap = money(target_unit - current_unit) if target_unit > 0 else Decimal("0")
        progress_pct = Decimal("0")
        if target_unit > 0 and target_unit > entry_unit:
            progress_pct = ((current_unit - entry_unit) / (target_unit - entry_unit)) * Decimal("100")
        elif target_unit > 0 and current_unit >= target_unit:
            progress_pct = Decimal("100")
        progress_pct = max(Decimal("0"), min(Decimal("100"), progress_pct))
        trend_signal = str(market_trend.get("signal") or "neutral")
        trend_label = {
            "bullish": "Alta curta",
            "constructive": "Alta moderada",
            "bearish": "Perdendo forca",
            "neutral": "Lateral",
        }.get(trend_signal, trend_signal.replace("_", " ").title())
        action_label = {
            "limite_atingido": "Executar venda",
            "janela_favoravel": "Preparar saida",
            "proteger_lucro": "Reduzir risco",
            "aguardar": "Aguardar",
        }.get(str(signal.get("status") or "aguardar"), "Aguardar")
        entries.append(
            {
                "id": int(lot.get("id") or 0),
                "source_transaction_id": int(lot.get("source_transaction_id") or lot.get("source_id") or 0),
                "remaining_grams": str(lot.get("remaining_grams") or "0"),
                "teor": str(lot.get("teor") or "-"),
                "hold_days": str(lot.get("hold_days") or "0"),
                "entry_unit_usd": str(money(entry_unit)),
                "market_unit_usd": str(lot.get("market_unit_usd") or "0"),
                "target_unit_usd": str(target_unit if target_unit > 0 else ""),
                "target_gap_usd": str(target_gap),
                "target_progress_pct": str(money(progress_pct)),
                "unrealized_pnl_usd": str(lot.get("unrealized_pnl_usd") or "0"),
                "profit_pct": str(signal.get("profit_pct") or "0"),
                "min_profit_gap_pct": str(signal.get("min_profit_gap_pct") or "0"),
                "reason": str(signal.get("reason") or "-"),
                "status": str(signal.get("status") or "aguardar"),
                "status_label": format_lot_signal_status(str(signal.get("status") or "aguardar")),
                "action_label": action_label,
                "status_class": str(signal.get("status_class") or "neutral"),
                "trend_signal": trend_signal,
                "trend_label": trend_label,
                "enabled": bool(signal.get("enabled")),
                "notify_phone": str(signal.get("notify_phone") or default_alert_phone),
                "target_price_usd": str(signal.get("target_price_usd") or ""),
                "min_profit_pct": str(signal.get("min_profit_pct") or "4"),
            }
        )
    return entries


def _render_lot_monitor_cards(
    entries: List[Dict[str, Any]],
    page_name: str,
    empty_message: str,
    default_alert_phone: str,
) -> str:
    lot_monitor_cards: List[str] = []
    for item in entries:
        notify_phone = escape(str(item.get("notify_phone") or default_alert_phone))
        target_price = escape(str(item.get("target_price_usd") or ""))
        min_profit_pct = escape(str(item.get("min_profit_pct") or "4"))
        enabled_checked = "checked" if item.get("enabled") else ""
        signal_status = escape(str(item.get("status_label") or "Aguardar"))
        signal_reason = escape(str(item.get("reason") or "-"))
        signal_class = escape(str(item.get("status_class") or "neutral"))
        lot_monitor_cards.append(
            f"""
            <article class='lot-monitor-card {signal_class}' data-lot-monitor-card data-lot-id='{escape(str(item.get('id') or '0'))}'>
                <div class='lot-monitor-head'>
                    <div class='lot-monitor-title'>
                        <strong data-lot-source-id>GT-{escape(str(item.get('source_transaction_id', '')))}</strong>
                        <p><span data-lot-remaining-grams>{escape(str(item.get('remaining_grams', '0')))}</span> g | teor <span data-lot-teor>{escape(str(item.get('teor', '-')))}</span>% | <span data-lot-trend-label>{escape(str(item.get('trend_label') or 'Lateral'))}</span></p>
                    </div>
                    <div class='lot-monitor-status-stack'>
                        <span class='lot-monitor-pill {signal_class}' data-lot-status-pill>{escape(str(item.get('action_label') or signal_status))}</span>
                        <small data-lot-monitor-mode>{'Monitor 24h ativo' if item.get('enabled') else 'Monitor desligado'}</small>
                    </div>
                </div>
                <div class='lot-monitor-progress'>
                    <div class='lot-monitor-progress-head'>
                        <span>Progresso ate a meta</span>
                        <strong data-lot-target-progress>{escape(str(item.get('target_progress_pct') or '0'))}%</strong>
                    </div>
                    <div class='lot-monitor-progress-bar'><span data-lot-target-progress-bar style='width:{escape(str(item.get('target_progress_pct') or '0'))}%'></span></div>
                </div>
                <div class='lot-monitor-metrics lot-monitor-metrics-4'>
                    <div><small>Entrada</small><strong data-lot-entry-unit>USD {escape(str(item.get('entry_unit_usd') or '0'))}</strong></div>
                    <div><small>Mercado</small><strong data-lot-market-unit>USD {escape(str(item.get('market_unit_usd', '0')))}</strong></div>
                    <div><small>Meta</small><strong data-lot-target-unit>USD {escape(str(item.get('target_unit_usd') or '-'))}</strong></div>
                    <div><small>Gap meta</small><strong data-lot-target-gap class='{signal_class}'>USD {escape(str(item.get('target_gap_usd') or '0'))}</strong></div>
                </div>
                <div class='lot-monitor-metrics lot-monitor-metrics-4'>
                    <div><small>P/L USD</small><strong class='{signal_class}' data-lot-unrealized-pnl>USD {escape(str(item.get('unrealized_pnl_usd', '0')))}</strong></div>
                    <div><small>P/L %</small><strong class='{signal_class}' data-lot-profit-pct>{escape(str(item.get('profit_pct') or '0'))}%</strong></div>
                    <div><small>Folga minimo</small><strong class='{signal_class}' data-lot-min-profit-gap>{escape(str(item.get('min_profit_gap_pct') or '0'))} p.p.</strong></div>
                    <div><small>Dias em carteira</small><strong data-lot-hold-days>{escape(str(item.get('hold_days') or '0'))}</strong></div>
                    <div><small>Leitura IA</small><strong data-lot-trend-bias>{escape(str(item.get('trend_label') or 'Lateral'))}</strong></div>
                </div>
                <p class='hint lot-monitor-callout' data-lot-reason>{signal_reason}</p>
                <form method='post' action='/saas/lots/{escape(str(item.get('id') or '0'))}/monitor' class='lot-monitor-form'>
                    <input type='hidden' name='page' value='{page_name}' />
                    <div class='fields-3'>
                        <label>Meta USD/g
                            <input name='target_price_usd' value='{target_price}' inputmode='decimal' placeholder='Opcional' data-lot-target-price />
                        </label>
                        <label>Lucro minimo %
                            <input name='min_profit_pct' value='{min_profit_pct}' inputmode='decimal' data-lot-min-profit />
                        </label>
                        <label>WhatsApp alerta
                            <input name='notify_phone' value='{notify_phone}' placeholder='+597...' data-lot-notify-phone />
                        </label>
                    </div>
                    <div class='quick-actions'>
                        <label class='monitor-check'><input type='checkbox' name='enabled' value='1' style='width:auto;margin-right:8px;' {enabled_checked} data-lot-enabled/> Ativar monitor 24h</label>
                        <button type='submit'>Salvar monitor</button>
                    </div>
                </form>
            </article>
            """
        )
    return "".join(lot_monitor_cards) or f"<div class='empty-state'>{escape(empty_message)}</div>"


def _normalize_whatsapp_to(
    raw_phone: str,
    *,
    normalize_user_phone: Callable[[str], str],
) -> str:
    normalized = normalize_user_phone(raw_phone)
    return f"whatsapp:{normalized}" if normalized else ""


def _send_outbound_whatsapp_alert(
    phone: str,
    message: str,
    *,
    normalize_whatsapp_to: Callable[[str], str],
    logger: logging.Logger,
) -> bool:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = str(os.getenv("TWILIO_WHATSAPP_FROM") or "").strip()
    to_number = normalize_whatsapp_to(phone)
    if not account_sid or not auth_token or not from_number or not to_number:
        logger.warning("Monitor de lotes sem credenciais completas do Twilio; alerta nao enviado.")
        return False
    if not from_number.startswith("whatsapp:"):
        from_number = f"whatsapp:{from_number}"

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "From": from_number,
                "To": to_number,
                "Body": message,
            },
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Falha ao enviar alerta outbound do lote via Twilio: %s", exc)
        return False


def _build_lot_alert_message(lot: Dict[str, Any], signal: Dict[str, Any], market_trend: Dict[str, Any]) -> str:
    return (
        "ALERTA DE LOTE DE OURO\n"
        f"Lote: GT-{lot.get('source_transaction_id', lot.get('source_id', '-'))}\n"
        f"Teor: {lot.get('teor', '-')}%\n"
        f"Saldo: {lot.get('remaining_grams', '0')} g\n"
        f"Mercado: USD {lot.get('market_unit_usd', '0')}/g\n"
        f"Lucro aberto: USD {lot.get('unrealized_pnl_usd', '0')} ({signal.get('profit_pct', '0')}%)\n"
        f"Sinal: {str(signal.get('status') or 'aguardar').replace('_', ' ').title()}\n"
        f"Leitura: {market_trend.get('summary', '-')}.\n"
        f"Motivo: {signal.get('reason', '-')}."
    )


def _run_lot_monitor_cycle(
    *,
    get_db: Callable[[], Any],
    get_market_snapshot: Callable[[], Dict[str, str]],
    build_market_trend_context: Callable[[], Dict[str, Any]],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, str]], Dict[str, Any]],
    build_lot_sell_signal: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    extract_lot_monitor_config: Callable[[Dict[str, Any]], Dict[str, Any]],
    build_lot_alert_message: Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], str],
    send_outbound_whatsapp_alert: Callable[[str, str], bool],
    logger: logging.Logger,
) -> None:
    db = get_db()
    inventory = db.get_gold_inventory_status(open_only=True)
    if not inventory.get("has_any_lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status(open_only=True)
    market_snapshot = get_market_snapshot()
    market_trend = build_market_trend_context()
    lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)
    for lot in cast(List[Dict[str, Any]], lot_market_context.get("lots") or []):
        signal = build_lot_sell_signal(lot, market_trend)
        if not signal.get("should_alert"):
            continue
        monitor_cfg = extract_lot_monitor_config(lot)
        notify_phone = str(signal.get("notify_phone") or "")
        if not notify_phone:
            continue
        if str(monitor_cfg.get("last_alert_signature") or "") == str(signal.get("alert_signature") or ""):
            continue
        alert_message = build_lot_alert_message(lot, signal, market_trend)
        if not send_outbound_whatsapp_alert(notify_phone, alert_message):
            continue
        updated_monitor = {
            **monitor_cfg,
            "enabled": bool(signal.get("enabled")),
            "notify_phone": notify_phone,
            "target_price_usd": signal.get("target_price_usd"),
            "min_profit_pct": signal.get("min_profit_pct"),
            "last_alert_signature": signal.get("alert_signature"),
            "last_alert_status": signal.get("status"),
            "last_alert_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            db.update_gold_inventory_lot_monitor(int(lot.get("id") or 0), updated_monitor)
        except Exception as exc:
            logger.warning("Falha ao persistir estado do monitor do lote %s: %s", lot.get("id"), exc)


def _lot_monitor_worker(
    *,
    stop_event: threading.Event,
    interval_seconds: int,
    run_lot_monitor_cycle: Callable[[], None],
    logger: logging.Logger,
) -> None:
    while not stop_event.is_set():
        try:
            run_lot_monitor_cycle()
        except Exception as exc:
            logger.warning("Falha no ciclo do monitor de lotes: %s", exc)
        stop_event.wait(interval_seconds)


def _build_lot_monitor_snapshot_payload(
    db: Any,
    session_user: Dict[str, Any],
    *,
    build_snapshot_cache_key: Callable[[str], str],
    get_snapshot_cached: Callable[[str], Dict[str, Any] | None],
    set_snapshot_cached: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    get_market_snapshot: Callable[[], Dict[str, str]],
    build_market_trend_context: Callable[[], Dict[str, Any]],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, str]], Dict[str, Any]],
    build_web_lot_monitor_view_model: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    cache_key = build_snapshot_cache_key(str(session_user.get("telefone") or ""))
    cached_payload = get_snapshot_cached(cache_key)
    if cached_payload is not None:
        return cached_payload

    inventory = db.get_gold_inventory_status(open_only=True)
    if not inventory.get("has_any_lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status(open_only=True)
    market_snapshot = get_market_snapshot()
    market_trend = build_market_trend_context()
    lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)
    lot_monitor_model = build_web_lot_monitor_view_model(
        lot_market_context,
        market_trend,
        default_alert_phone=str(session_user.get("telefone") or ""),
        entry_limit=24,
        alert_limit=4,
    )
    payload = {
        "ok": True,
        "summary": str(lot_monitor_model.get("summary") or ""),
        "alerts": cast(List[Dict[str, Any]], lot_monitor_model.get("alerts") or []),
        "lots": cast(List[Dict[str, Any]], lot_monitor_model.get("entries") or []),
        "updated_at_label": str(market_snapshot.get("updated_at_label") or ""),
    }
    return set_snapshot_cached(cache_key, payload)


async def _lot_monitor_stream_events(
    request: Any,
    session_user: Dict[str, Any],
    db: Any,
    *,
    build_lot_monitor_snapshot_payload: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
    build_sse_message: Callable[[Dict[str, Any]], str],
    stream_interval_seconds: float,
):
    while True:
        if await request.is_disconnected():
            break
        payload = build_lot_monitor_snapshot_payload(db, session_user)
        yield build_sse_message(payload)
        await asyncio.sleep(stream_interval_seconds)


def _build_open_lot_market_context(
    open_lots: List[Dict[str, Any]],
    market_snapshot: Dict[str, str],
    *,
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> Dict[str, Any]:
    try:
        xau_usd_spot = Decimal(str(market_snapshot.get("xau_usd_raw") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        xau_usd_spot = Decimal("0")

    pure_gram_spot = money(xau_usd_spot / Decimal("31.1035")) if xau_usd_spot > 0 else Decimal("0")
    now_local = datetime.now(timezone.utc)
    enriched_lots: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Decimal | str | int]] = {}
    total_fine_grams = Decimal("0")
    total_market_value = Decimal("0")
    total_unrealized = Decimal("0")

    for lot in open_lots:
        try:
            remaining_grams = Decimal(str(lot.get("remaining_grams") or "0"))
            initial_grams = Decimal(str(lot.get("initial_grams") or remaining_grams))
            unit_cost_usd = Decimal(str(lot.get("unit_cost_usd") or "0"))
            teor_pct = Decimal(str(lot.get("teor") or "100"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if remaining_grams <= 0:
            continue
        if teor_pct <= 0:
            teor_pct = Decimal("100")

        fine_grams = money(remaining_grams * (teor_pct / Decimal("100")))
        lot_cost_usd = money(remaining_grams * unit_cost_usd)
        market_unit_usd = money(pure_gram_spot * (teor_pct / Decimal("100"))) if pure_gram_spot > 0 else Decimal("0")
        market_value_usd = money(remaining_grams * market_unit_usd) if market_unit_usd > 0 else Decimal("0")
        unrealized_pnl_usd = money(market_value_usd - lot_cost_usd) if market_unit_usd > 0 else Decimal("0")

        created_at = str(lot.get("created_at_tx") or lot.get("criado_em") or "")
        hold_days = 0
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            hold_days = max(0, int((now_local - created_dt).total_seconds() // 86400))
        except Exception:
            hold_days = 0

        teor_label = format_decimal_for_form(teor_pct, 2)
        grouping = grouped.setdefault(
            teor_label,
            {
                "teor": teor_label,
                "lots": 0,
                "grams": Decimal("0"),
                "fine_grams": Decimal("0"),
                "cost_usd": Decimal("0"),
                "market_value_usd": Decimal("0"),
                "unrealized_pnl_usd": Decimal("0"),
            },
        )
        grouping["lots"] = int(grouping["lots"] or 0) + 1
        grouping["grams"] = Decimal(str(grouping["grams"] or "0")) + remaining_grams
        grouping["fine_grams"] = Decimal(str(grouping["fine_grams"] or "0")) + fine_grams
        grouping["cost_usd"] = Decimal(str(grouping["cost_usd"] or "0")) + lot_cost_usd
        grouping["market_value_usd"] = Decimal(str(grouping["market_value_usd"] or "0")) + market_value_usd
        grouping["unrealized_pnl_usd"] = Decimal(str(grouping["unrealized_pnl_usd"] or "0")) + unrealized_pnl_usd

        total_fine_grams += fine_grams
        total_market_value += market_value_usd
        total_unrealized += unrealized_pnl_usd
        enriched_lots.append(
            {
                **lot,
                "initial_grams": str(initial_grams),
                "remaining_grams": str(remaining_grams),
                "teor": teor_label,
                "fine_grams": str(fine_grams),
                "lot_cost_usd": str(lot_cost_usd),
                "market_unit_usd": str(market_unit_usd),
                "market_value_usd": str(market_value_usd),
                "unrealized_pnl_usd": str(unrealized_pnl_usd),
                "hold_days": hold_days,
            }
        )

    by_teor = [
        {
            "teor": str(item["teor"]),
            "lots": int(item["lots"]),
            "grams": str(money(Decimal(str(item["grams"])))),
            "fine_grams": str(money(Decimal(str(item["fine_grams"])))),
            "cost_usd": str(money(Decimal(str(item["cost_usd"])))),
            "market_value_usd": str(money(Decimal(str(item["market_value_usd"])))),
            "unrealized_pnl_usd": str(money(Decimal(str(item["unrealized_pnl_usd"])))),
        }
        for item in sorted(grouped.values(), key=lambda row: Decimal(str(row.get("teor") or "0")), reverse=True)
    ]

    return {
        "pure_gram_spot_usd": str(pure_gram_spot),
        "available_fine_grams": str(money(total_fine_grams)),
        "market_value_usd": str(money(total_market_value)),
        "unrealized_pnl_usd": str(money(total_unrealized)),
        "lots": enriched_lots,
        "by_teor": by_teor,
    }


def _build_operation_lot_market_context(
    open_lots: List[Dict[str, Any]],
    market_snapshot: Dict[str, str],
    *,
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> Dict[str, Any]:
    try:
        xau_usd_spot = Decimal(str(market_snapshot.get("xau_usd_raw") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        xau_usd_spot = Decimal("0")

    pure_gram_spot = money(xau_usd_spot / Decimal("31.1035")) if xau_usd_spot > 0 else Decimal("0")
    grouped: Dict[str, Dict[str, Decimal | str | int]] = {}
    risk_lots: List[Dict[str, Any]] = []
    total_fine_grams = Decimal("0")
    total_market_value = Decimal("0")
    total_unrealized = Decimal("0")

    for lot in open_lots:
        try:
            remaining_grams = Decimal(str(lot.get("remaining_grams") or "0"))
            unit_cost_usd = Decimal(str(lot.get("unit_cost_usd") or "0"))
            teor_pct = Decimal(str(lot.get("teor") or "100"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if remaining_grams <= 0:
            continue
        if teor_pct <= 0:
            teor_pct = Decimal("100")

        fine_grams = money(remaining_grams * (teor_pct / Decimal("100")))
        lot_cost_usd = money(remaining_grams * unit_cost_usd)
        market_unit_usd = money(pure_gram_spot * (teor_pct / Decimal("100"))) if pure_gram_spot > 0 else Decimal("0")
        market_value_usd = money(remaining_grams * market_unit_usd) if market_unit_usd > 0 else Decimal("0")
        unrealized_pnl_usd = money(market_value_usd - lot_cost_usd) if market_unit_usd > 0 else Decimal("0")

        teor_label = format_decimal_for_form(teor_pct, 2)
        grouping = grouped.setdefault(
            teor_label,
            {
                "teor": teor_label,
                "lots": 0,
                "grams": Decimal("0"),
                "fine_grams": Decimal("0"),
                "cost_usd": Decimal("0"),
                "market_value_usd": Decimal("0"),
                "unrealized_pnl_usd": Decimal("0"),
            },
        )
        grouping["lots"] = int(grouping["lots"] or 0) + 1
        grouping["grams"] = Decimal(str(grouping["grams"] or "0")) + remaining_grams
        grouping["fine_grams"] = Decimal(str(grouping["fine_grams"] or "0")) + fine_grams
        grouping["cost_usd"] = Decimal(str(grouping["cost_usd"] or "0")) + lot_cost_usd
        grouping["market_value_usd"] = Decimal(str(grouping["market_value_usd"] or "0")) + market_value_usd
        grouping["unrealized_pnl_usd"] = Decimal(str(grouping["unrealized_pnl_usd"] or "0")) + unrealized_pnl_usd

        total_fine_grams += fine_grams
        total_market_value += market_value_usd
        total_unrealized += unrealized_pnl_usd

        risk_lots.append(
            {
                "source_transaction_id": lot.get("source_transaction_id", lot.get("source_id")),
                "source_id": lot.get("source_id"),
                "teor": teor_label,
                "remaining_grams": str(remaining_grams),
                "unrealized_pnl_usd": str(unrealized_pnl_usd),
            }
        )
        risk_lots.sort(key=lambda item: Decimal(str(item.get("unrealized_pnl_usd") or "0")))
        if len(risk_lots) > 4:
            risk_lots.pop()

    by_teor = [
        {
            "teor": str(item["teor"]),
            "lots": int(item["lots"]),
            "grams": str(money(Decimal(str(item["grams"])))),
            "fine_grams": str(money(Decimal(str(item["fine_grams"])))),
            "cost_usd": str(money(Decimal(str(item["cost_usd"])))),
            "market_value_usd": str(money(Decimal(str(item["market_value_usd"])))),
            "unrealized_pnl_usd": str(money(Decimal(str(item["unrealized_pnl_usd"])))),
        }
        for item in sorted(grouped.values(), key=lambda row: Decimal(str(row.get("teor") or "0")), reverse=True)
    ]

    return {
        "pure_gram_spot_usd": str(pure_gram_spot),
        "available_fine_grams": str(money(total_fine_grams)),
        "market_value_usd": str(money(total_market_value)),
        "unrealized_pnl_usd": str(money(total_unrealized)),
        "by_teor": by_teor,
        "risk_lots": risk_lots,
    }