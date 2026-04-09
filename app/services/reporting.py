from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, Optional, cast

from app.database import DatabaseClient


def _build_inventory_status_report_payload(
    db: DatabaseClient,
    *,
    get_cached_payload: Callable[[], Optional[Dict[str, Any]]],
    set_cached_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    get_market_snapshot: Callable[[], Dict[str, str]],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, str]], Dict[str, Any]],
    compute_inventory_metrics: Callable[[List[Dict[str, Any]]], Dict[str, Decimal]],
    build_fifo_inventory_lots: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
) -> Dict[str, Any]:
    cached_payload = get_cached_payload()
    if cached_payload is not None:
        return cached_payload

    inventory = db.get_gold_inventory_status(open_only=True)
    if not inventory.get("has_any_lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status(open_only=True)
    market_snapshot = get_market_snapshot()
    lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)

    if inventory.get("has_any_lots"):
        return set_cached_payload({
            "available_grams": str(inventory.get("available_grams", "0")),
            "inventory_cost_usd": str(inventory.get("inventory_cost_usd", "0.00")),
            "avg_cost_usd_per_gram": str(inventory.get("avg_cost_usd_per_gram", "0.00")),
            "available_fine_grams": str(lot_market_context.get("available_fine_grams", "0")),
            "market_value_usd": str(lot_market_context.get("market_value_usd", "0")),
            "unrealized_pnl_usd": str(lot_market_context.get("unrealized_pnl_usd", "0")),
            "by_teor": lot_market_context.get("by_teor", []),
            "open_lots": len(cast(List[Dict[str, Any]], inventory.get("open_lots") or [])),
            "ledger_mode": "persisted",
            "lots": lot_market_context.get("lots", []),
        })

    transactions = db.get_gold_inventory_transactions()
    metrics = compute_inventory_metrics(transactions)
    fallback_lots = build_fifo_inventory_lots(transactions)
    fallback_market_context = build_open_lot_market_context(fallback_lots, market_snapshot)
    return set_cached_payload({
        "available_grams": str(metrics["available_grams"]),
        "inventory_cost_usd": str(metrics["inventory_cost_usd"]),
        "avg_cost_usd_per_gram": str(metrics["avg_cost_usd_per_gram"]),
        "available_fine_grams": str(fallback_market_context.get("available_fine_grams", "0")),
        "market_value_usd": str(fallback_market_context.get("market_value_usd", "0")),
        "unrealized_pnl_usd": str(fallback_market_context.get("unrealized_pnl_usd", "0")),
        "by_teor": fallback_market_context.get("by_teor", []),
        "open_lots": len(fallback_lots),
        "ledger_mode": "reconstructed",
        "lots": fallback_market_context.get("lots", []),
    })


def _build_admin_dashboard_html(
    db: DatabaseClient,
    *,
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_cache_key: Callable[[str], str],
    get_cached_html: Callable[[str], Optional[str]],
    set_cached_html: Callable[[str, str], str],
    compute_inventory_metrics: Callable[[List[Dict[str, Any]]], Dict[str, Decimal]],
    build_fifo_inventory_lots: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    format_caixa_movement: Callable[[str, Decimal], str],
) -> str:
    day = build_day_range(None)
    cache_key = build_cache_key(day["date"])
    cached_html = get_cached_html(cache_key)
    if cached_html is not None:
        return cached_html

    summary = db.get_daily_gold_summary(day["start"], day["end"])
    alerts = db.get_risk_alerts(day["start"], day["end"])
    divergences = db.get_top_divergences(day["start"], day["end"], limit=5)
    saldo = db.get_saldo_caixa()
    recent_runs = db.get_recent_multi_agent_runs(limit=5)
    inventory = db.get_gold_inventory_status(open_only=True)
    if not inventory.get("has_any_lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status(open_only=True)

    if not inventory.get("has_any_lots"):
        fallback_transactions = db.get_gold_inventory_transactions()
        fallback_metrics = compute_inventory_metrics(fallback_transactions)
        inventory = {
            "available_grams": str(fallback_metrics["available_grams"]),
            "inventory_cost_usd": str(fallback_metrics["inventory_cost_usd"]),
            "avg_cost_usd_per_gram": str(fallback_metrics["avg_cost_usd_per_gram"]),
            "open_lots": build_fifo_inventory_lots(fallback_transactions),
            "has_any_lots": False,
        }

    saldo_items = "".join(
        f"<li><strong>{moeda}</strong>: {escape(format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</li>"
        for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]
    )
    alert_items = "".join(
        f"<li>{escape(str(item.get('tipo_alerta', 'alerta')))} - {escape(str(item.get('descricao', item)))}</li>"
        for item in alerts[:10]
    ) or "<li>Sem alertas no dia.</li>"
    divergence_items = "".join(
        f"<li>ID {item.get('id')}: {escape(str(item.get('tipo_operacao', 'op')))} | diff USD {escape(str(item.get('diferenca_usd', '0')))} | operador {escape(str(item.get('operador_id', '')))}</li>"
        for item in divergences
    ) or "<li>Sem divergencias no dia.</li>"
    run_items = "".join(
        f"<li>{escape(str(item.get('criado_em', '')))} - {escape(str(item.get('objective', 'multi-agent')))}</li>"
        for item in recent_runs
    ) or "<li>Sem execucoes multiagente recentes.</li>"
    lot_items = "".join(
        f"<li>Lote tx {escape(str(item.get('source_transaction_id', '')))}: {escape(str(item.get('remaining_grams', '0')))} g a USD {escape(str(item.get('unit_cost_usd', '0')))}</li>"
        for item in cast(List[Dict[str, Any]], inventory.get("open_lots") or [])[:8]
    ) or "<li>Sem lotes abertos.</li>"

    html = f"""
    <html>
        <head>
            <title>Caixa Admin Dashboard</title>
            <style>
                body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #111; }}
                h1, h2 {{ margin-bottom: 8px; }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }}
                .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 16px; background: #fafafa; }}
                ul {{ padding-left: 18px; }}
                .kpi {{ font-size: 20px; font-weight: 700; }}
            </style>
        </head>
        <body>
            <h1>Caixa Admin Dashboard</h1>
            <p>Data: {escape(day['date'])}</p>
            <div class="grid">
                <div class="card">
                    <h2>Resumo Diario</h2>
                    <div class="kpi">Operacoes: {escape(str(summary.get('total_operacoes', 0)))}</div>
                    <p>Total USD: {escape(str(summary.get('total_usd', '0')))}</p>
                    <p>Total pago USD: {escape(str(summary.get('total_pago_usd', '0')))}</p>
                    <p>Diferenca USD: {escape(str(summary.get('total_diferenca_usd', '0')))}</p>
                </div>
                <div class="card">
                    <h2>Estoque Ouro</h2>
                    <p>Disponivel: {escape(str(inventory['available_grams']))} g</p>
                    <p>Custo FIFO aberto: USD {escape(str(inventory['inventory_cost_usd']))}</p>
                    <p>Custo medio aberto: USD {escape(str(inventory['avg_cost_usd_per_gram']))}/g</p>
                    <ul>{lot_items}</ul>
                </div>
                <div class="card">
                    <h2>Posicao dos 5 Caixas</h2>
                    <ul>{saldo_items}</ul>
                </div>
                <div class="card">
                    <h2>Alertas de Risco</h2>
                    <ul>{alert_items}</ul>
                </div>
                <div class="card">
                    <h2>Top Divergencias</h2>
                    <ul>{divergence_items}</ul>
                </div>
                <div class="card">
                    <h2>Runs Multiagente</h2>
                    <ul>{run_items}</ul>
                </div>
            </div>
        </body>
    </html>
    """
    return set_cached_html(cache_key, html)