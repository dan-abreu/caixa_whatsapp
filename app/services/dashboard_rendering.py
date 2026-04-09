from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, cast


def _render_market_news_panel_html(news_items: List[Dict[str, str]], limit: int = 6) -> str:
    if not news_items:
        return "<div class='empty-state'>Sem noticias disponiveis agora.</div>"

    cards = []
    for item in news_items[:limit]:
        topic = str(item.get("topic") or "mercado").upper()
        cards.append(
            f"<article class='news-card'><span class='news-tag'>{escape(topic)}</span><h3>{escape(str(item.get('title') or '-'))}</h3><p>{escape(str(item.get('source') or 'Fonte externa'))}</p><a href='{escape(str(item.get('link') or '#'))}' target='_blank' rel='noreferrer'>Abrir noticia</a></article>"
        )
    return "<div class='news-grid'>" + "".join(cards) + "</div>"


def _render_recent_operations_rows(
    transactions: List[Dict[str, Any]],
    empty_message: str = "Nenhuma operação recente.",
) -> str:
    rows: List[str] = []
    for item in reversed(transactions):
        source = str(item.get("source") or "transacoes")
        tid = str(item.get("id") or "-")
        id_label = f"GT-{tid}" if source == "gold_transactions" else f"T-{tid}"
        rows.append(
            f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('tipo_operacao') or '-').upper())}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>USD {escape(str(item.get('total_usd') or '0'))}</td></tr>"
        )
    return "".join(rows) or f"<tr><td colspan='5'>{escape(empty_message)}</td></tr>"


def _render_open_fechamentos_rows(
    transactions: List[Dict[str, Any]],
    *,
    collect_open_fechamentos: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    limit: int = 8,
    empty_message: str = "Nenhum fechamento parcial em aberto nos movimentos recentes.",
) -> str:
    rows: List[str] = []
    for item in collect_open_fechamentos(transactions)[:limit]:
        source = str(item.get("source") or "gold_transactions")
        item_id = str(item.get("id") or "-")
        id_label = f"GT-{item_id}" if source == "gold_transactions" else f"T-{item_id}"
        status = cast(Dict[str, Any], item.get("fechamento_status") or {})
        rows.append(
            f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>{escape(str(status.get('fechado') or '0'))} g</td><td>{escape(str(status.get('aberto') or '0'))} g</td></tr>"
        )
    return "".join(rows) or f"<tr><td colspan='5'>{escape(empty_message)}</td></tr>"


def _render_dashboard_pending_closings_html(
    transactions: List[Dict[str, Any]],
    *,
    collect_open_fechamentos: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
) -> str:
    return f"""
    <p class='hint'>Quando uma operacao permanece com fechamento parcial, a diferenca entre o peso total e o peso liquidado permanece pendente para regularizacao futura.</p>
    <table>
        <thead><tr><th>ID</th><th>Pessoa</th><th>Peso</th><th>Fechado</th><th>Em aberto</th></tr></thead>
        <tbody>{_render_open_fechamentos_rows(transactions, collect_open_fechamentos=collect_open_fechamentos)}</tbody>
    </table>
    """


def _render_dashboard_recent_operations_html(transactions: List[Dict[str, Any]]) -> str:
    return f"""
    <table>
        <thead><tr><th>ID</th><th>Tipo</th><th>Pessoa</th><th>Peso</th><th>Total</th></tr></thead>
        <tbody>{_render_recent_operations_rows(transactions)}</tbody>
    </table>
    """


def _render_dashboard_inventory_html(inventory: Dict[str, Any], lot_market_context: Dict[str, Any]) -> str:
    lot_rows: List[str] = []
    for item in cast(List[Dict[str, Any]], lot_market_context.get("lots") or [])[:10]:
        pnl_value = Decimal(str(item.get("unrealized_pnl_usd") or "0"))
        pnl_class = "positive" if pnl_value >= 0 else "negative"
        lot_rows.append(
            f"<tr><td>GT-{escape(str(item.get('source_transaction_id', item.get('source_id', ''))))}</td><td>{escape(str(item.get('teor', '-')))}%</td><td>{escape(str(item.get('remaining_grams', '0')))} g</td><td>{escape(str(item.get('fine_grams', '0')))} g fino</td><td>USD {escape(str(item.get('lot_cost_usd', '0')))}</td><td>USD {escape(str(item.get('market_value_usd', '0')))}</td><td class='{pnl_class}'>USD {escape(str(item.get('unrealized_pnl_usd', '0')))}</td></tr>"
        )
    lots_html = "".join(lot_rows) or "<tr><td colspan='7'>Sem lotes abertos.</td></tr>"

    lot_teor_rows: List[str] = []
    for item in cast(List[Dict[str, Any]], lot_market_context.get("by_teor") or []):
        pnl_value = Decimal(str(item.get("unrealized_pnl_usd") or "0"))
        pnl_class = "positive" if pnl_value >= 0 else "negative"
        lot_teor_rows.append(
            f"<tr><td>{escape(str(item.get('teor') or '-'))}%</td><td>{escape(str(item.get('lots') or 0))}</td><td>{escape(str(item.get('grams') or '0'))} g</td><td>{escape(str(item.get('fine_grams') or '0'))} g fino</td><td>USD {escape(str(item.get('cost_usd') or '0'))}</td><td>USD {escape(str(item.get('market_value_usd') or '0'))}</td><td class='{pnl_class}'>USD {escape(str(item.get('unrealized_pnl_usd') or '0'))}</td></tr>"
        )
    lot_teor_html = "".join(lot_teor_rows) or "<tr><td colspan='7'>Sem agrupamento por teor.</td></tr>"

    pnl_class = "positive" if Decimal(str(lot_market_context.get("unrealized_pnl_usd", "0"))) >= 0 else "negative"
    return f"""
    <div class='cards'>
        <div class='card'><small>Quantidade Disponivel</small><strong>{escape(str(inventory.get('available_grams', '0')))} g</strong></div>
        <div class='card'><small>Custo em Aberto</small><strong>USD {escape(str(inventory.get('inventory_cost_usd', '0.00')))}</strong></div>
        <div class='card'><small>Custo Medio</small><strong>USD {escape(str(inventory.get('avg_cost_usd_per_gram', '0.00')))}</strong></div>
    </div>
    <div class='cards' style='margin-top:14px;'>
        <div class='card'><small>Ouro fino aberto</small><strong>{escape(str(lot_market_context.get('available_fine_grams', '0')))} g</strong></div>
        <div class='card'><small>Valor de mercado</small><strong>USD {escape(str(lot_market_context.get('market_value_usd', '0')))}</strong></div>
        <div class='card'><small>P/L em aberto</small><strong class='{pnl_class}'>USD {escape(str(lot_market_context.get('unrealized_pnl_usd', '0')))}</strong></div>
    </div>
    <p class='hint'>Cada lote segue separado por compra e por teor. A marcação a mercado converte a onça spot em grama fina para mostrar quando vale segurar e quando a posição está carregando prejuízo.</p>
    <table>
        <thead><tr><th>Lote</th><th>Teor</th><th>Saldo bruto</th><th>Saldo fino</th><th>Custo</th><th>Mercado</th><th>P/L</th></tr></thead>
        <tbody>{lots_html}</tbody>
    </table>
    <table style='margin-top:14px;'>
        <thead><tr><th>Teor</th><th>Lotes</th><th>Gramas</th><th>Fino</th><th>Custo</th><th>Mercado</th><th>P/L</th></tr></thead>
        <tbody>{lot_teor_html}</tbody>
    </table>
    """


def _render_dashboard_summary_html(
    summary: Dict[str, Any],
    gross_grams_today: Decimal,
    ouro_proprio: Decimal,
    *,
    format_caixa_movement: Callable[[str, Decimal], str],
) -> str:
    return f"""
    <div class='section-head'>
        <div>
            <h2>Resumo Executivo</h2>
            <p class='hint'>Visao de mesa para o caixa de compra: entrada e giro do ouro, estoque FIFO aberto, monitores selecionados e contexto externo suficiente para decisao rapida.</p>
        </div>
    </div>
    <div class='cards'>
        <div class='card'><small>Operacoes do Dia</small><strong>{escape(str(summary.get('total_operacoes', 0)))}</strong></div>
        <div class='card'><small>Ouro Movimentado Hoje</small><strong>{escape(f'{gross_grams_today:.3f}')} g</strong></div>
        <div class='card'><small>Posicao Propria em Ouro</small><strong>{escape(format_caixa_movement('XAU', ouro_proprio))}</strong></div>
    </div>
    """