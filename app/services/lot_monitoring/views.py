from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, cast

from app.core.formatting import money


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
    return {"alerts": limited_alerts, "entries": entries, "summary": build_alert_summary(limited_alerts)}


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