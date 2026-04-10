from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any, Dict, List, Optional


def _format_market_decimal(value: Optional[Decimal], prefix: str = "", suffix: str = "") -> str:
    if value is None:
        return "Indisponivel"
    return f"{prefix}{value}{suffix}"


def _format_live_market_value(raw_value: str, prefix: str = "", suffix: str = "", decimals: int = 2) -> str:
    try:
        value = Decimal(str(raw_value or ""))
    except (InvalidOperation, TypeError, ValueError):
        return "Indisponivel"
    quantizer = Decimal("1") if decimals <= 0 else Decimal("1").scaleb(-decimals)
    normalized = value.quantize(quantizer, rounding=ROUND_HALF_UP)
    text = f"{normalized:,.{decimals}f}"
    text = text.replace(",", "#").replace(".", ",").replace("#", ".")
    return f"{prefix}{text}{suffix}"


def _render_market_panel_html(
    market_snapshot: Dict[str, str],
    *,
    market_monitor_cards: List[Dict[str, Any]],
    market_alert_threshold_pct: Decimal,
    format_live_market_value: Any,
    heading: str = "Painel de Mercado",
    compact: bool = False,
    rail: bool = False,
) -> str:
    cards_html = []
    for card in market_monitor_cards:
        field = str(card.get("field") or "")
        label = str(card.get("label") or field)
        prefix = str(card.get("prefix") or "")
        suffix = str(card.get("suffix") or "")
        decimals = int(card.get("decimals") or 2)
        priority = str(card.get("priority") or "secondary")
        alert_enabled = "1" if bool(card.get("alert_enabled")) else "0"
        cards_html.append(
            f"""
            <div class='card market-card market-card-{escape(priority)}' data-market-field='{escape(field)}' data-alert-enabled='{alert_enabled}' data-prefix='{escape(prefix)}' data-suffix='{escape(suffix)}' data-decimals='{decimals}'>
                <div class='market-card-head'>
                    <small>{escape(label)}</small>
                    <span class='market-card-chip'>{'Monitoravel' if alert_enabled == '1' else 'Referencia'}</span>
                </div>
                <strong class='market-value'>{escape(format_live_market_value(str(market_snapshot.get(field) or ''), prefix=prefix, suffix=suffix, decimals=decimals))}</strong>
                <div class='market-card-meta'>
                    <span class='market-window-label' data-market-window>Janela 20s</span>
                    <span class='market-freshness' data-market-freshness>Ao vivo</span>
                </div>
                <div class='market-change neutral'><span class='market-arrow'>•</span><span class='market-delta'>Coletando janela</span></div>
                <svg class='market-sparkline' viewBox='0 0 120 36' preserveAspectRatio='none' aria-hidden='true'><polyline class='market-sparkline-line' points=''></polyline></svg>
            </div>
            """
        )
    panel_class = "panel section market-panel-live"
    if compact:
        panel_class += " compact-market-panel"
    if rail:
        panel_class += " market-rail-panel"
    description_html = (
        ""
        if compact or rail
        else "<p class='hint'>Monitor profissional com foco em variacao por janela, frescor do feed e prioridade operacional. O valor por grama considera onca troy ÷ 31.1035 com desconto tecnico de 10%.</p>"
    )
    status_html = "" if compact or rail else f"<p class='hint market-status'>{escape(market_snapshot['status'])}</p>"
    sources_html = (
        ""
        if compact or rail
        else "<p class='market-sources'>Fontes: <a href='https://api.gold-api.com' target='_blank' rel='noreferrer'>Gold-API</a> para XAU/USD spot, <a href='https://docs.awesomeapi.com.br/api-de-moedas' target='_blank' rel='noreferrer'>AwesomeAPI XAU/USD</a> como contingencia do ouro, e <a href='https://www.frankfurter.app' target='_blank' rel='noreferrer'>Frankfurter</a> para cambio.</p>"
    )
    return f"""
    <section class='{panel_class}' data-market-endpoint='/saas/market-snapshot' data-market-stream-endpoint='/saas/market-stream' data-market-alert-threshold='{str(market_alert_threshold_pct)}'>
        <div class='section-head'>
            <div>
                <h2>{escape(heading)}</h2>
                {description_html}
            </div>
            <div class='market-live-meta'>
                <span class='market-live-badge'>Tempo real</span>
                <span class='market-live-updated' data-market-updated>{escape(str(market_snapshot.get('updated_at_label') or 'agora'))}</span>
                <label class='market-threshold-control'>
                    <span>Alerta</span>
                    <select data-market-threshold-select>
                        <option value='0.25'>0,25%</option>
                        <option value='0.50' selected>0,50%</option>
                        <option value='1.00'>1,00%</option>
                        <option value='2.00'>2,00%</option>
                    </select>
                </label>
            </div>
        </div>
        <div class='market-grid'>
            {''.join(cards_html)}
        </div>
        <div class='market-alert-banner is-hidden' data-market-alert-banner>
            <strong>Alerta de mercado</strong>
            <span data-market-alert-text>Sem alertas relevantes.</span>
        </div>
        {status_html}
        {sources_html}
    </section>
    """