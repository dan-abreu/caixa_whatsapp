from decimal import Decimal
from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, cast


def build_runtime_saas_context_helpers() -> SimpleNamespace:
    def build_dashboard_context(
        *,
        db: Any,
        session_user: Dict[str, Any],
        current_page: str,
        statement_context: Optional[Dict[str, Any]],
        clients_context: Optional[Dict[str, Any]],
        suppliers_context: Optional[Dict[str, Any]],
        build_day_range: Callable[[Any], Dict[str, Any]],
        build_week_range: Callable[[], Dict[str, Any]],
        build_saas_statement_context: Callable[[Any, Any, Any], Dict[str, Any]],
        build_saas_clients_context: Callable[[Any], Dict[str, Any]],
        build_saas_suppliers_context: Callable[[Any], Dict[str, Any]],
        collect_open_fechamentos: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
        build_gold_caixa_metrics_from_pending_grams: Callable[[Decimal, Any], Dict[str, Any]],
        get_market_snapshot: Callable[[], Dict[str, Any]],
        build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
        build_operation_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
        build_market_trend_context: Callable[[], Dict[str, Any]],
        render_recent_operations_rows: Callable[[List[Dict[str, Any]]], str],
        build_saas_chat_welcome: Callable[[str], Dict[str, Any]],
        build_saas_recent_fx_map: Callable[[Any], Dict[str, str]],
        build_web_payment_rows_html: Callable[[Dict[str, str]], str],
        format_caixa_movement: Callable[[str, Decimal], str],
    ) -> Dict[str, Any]:
        day = build_day_range(None)
        week = build_week_range()
        needs_statement = current_page == "statement" or statement_context is not None
        needs_clients = current_page == "clients" or clients_context is not None
        needs_suppliers = current_page == "suppliers" or suppliers_context is not None
        needs_week_activity = current_page == "news_hub"
        needs_inventory_details = current_page in {"operation", "monitors"}
        needs_gold_caixa_metrics = current_page == "operation"
        needs_market_news = current_page == "news_hub"
        needs_lot_monitors = current_page == "monitors"
        needs_recent_fx = current_page == "operation"
        needs_market_rail = current_page in {"dashboard", "operation", "monitors", "news_hub"}
        needs_market_snapshot = needs_inventory_details or needs_market_rail
        needs_sidebar_inventory = current_page in {"dashboard", "operation", "monitors", "news_hub"}
        needs_balance_cards = current_page in {"dashboard", "profile"}
        needs_money_balances = current_page == "operation"
        needs_operation_inventory_tables = current_page == "operation"
        needs_news_recent_ops = current_page == "news_hub"
        needs_full_lot_market_context = needs_lot_monitors

        saldo: Dict[str, Any] = {"XAU": "0", "USD": "0", "EUR": "0", "SRD": "0", "BRL": "0"}
        if needs_balance_cards or needs_money_balances:
            saldo = db.get_saldo_caixa()

        if needs_inventory_details:
            inventory = db.get_gold_inventory_status(open_only=True)
            if not inventory.get("has_any_lots"):
                db.sync_gold_inventory_ledger()
                inventory = db.get_gold_inventory_status(open_only=True)
        elif needs_sidebar_inventory:
            inventory = db.get_gold_inventory_overview()
        else:
            inventory = {"available_grams": "0"}

        week_transactions: List[Dict[str, Any]] = []
        if needs_week_activity:
            week_transactions = db.get_extrato_transactions(week["start"], week["end"])
        recent_ops = week_transactions[-12:] if needs_week_activity else []

        statement = statement_context or {}
        if needs_statement and not statement:
            statement = build_saas_statement_context(db, None, None)
        client_view = clients_context or (build_saas_clients_context(db) if needs_clients else None)
        supplier_view = suppliers_context or (build_saas_suppliers_context(db) if needs_suppliers else None)
        statement_transactions = cast(List[Dict[str, Any]], statement.get("transactions") or [])
        open_fechamentos_statement = collect_open_fechamentos(statement_transactions) if needs_statement else []

        gold_caixa_metrics = build_gold_caixa_metrics_from_pending_grams(
            Decimal(str(saldo.get("XAU", "0"))),
            db.get_gold_pending_closure_grams(),
        ) if needs_gold_caixa_metrics else {
            "ouro_pendente": Decimal("0"),
            "ouro_em_caixa": Decimal(str(saldo.get("XAU", "0"))),
            "ouro_proprio": Decimal(str(saldo.get("XAU", "0"))),
        }

        market_snapshot = get_market_snapshot() if needs_market_snapshot else {
            "xau_usd_raw": "",
            "grama_ref_raw": "",
            "usd_brl_raw": "",
            "eur_usd_raw": "",
            "eur_brl_raw": "",
            "xau_source": "",
            "xau_source_label": "",
            "status": "Indisponivel",
            "updated_at_label": "",
        }

        open_lots = cast(List[Dict[str, Any]], inventory.get("open_lots") or [])
        lot_market_context = build_open_lot_market_context(open_lots, market_snapshot) if needs_full_lot_market_context else {
            "lots": [],
            "by_teor": [],
            "available_fine_grams": "0",
            "market_value_usd": "0",
            "unrealized_pnl_usd": "0",
        }
        operation_lot_market_context = build_operation_lot_market_context(open_lots, market_snapshot) if needs_operation_inventory_tables else {
            "by_teor": [],
            "risk_lots": [],
            "available_fine_grams": "0",
            "market_value_usd": "0",
            "unrealized_pnl_usd": "0",
        }
        market_trend = build_market_trend_context() if needs_lot_monitors else {"trend_label": "Lateral"}

        balances_html = ""
        if needs_balance_cards:
            balances_html = "".join(
                f"<div class='balance'><span>{escape(moeda)}</span><strong>{escape(format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</strong></div>"
                for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]
            )

        money_balances_html = ""
        if needs_money_balances:
            money_balances_html = "".join(
                f"<div class='balance'><span>{escape(moeda)}</span><strong>{escape(format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</strong></div>"
                for moeda in ["USD", "EUR", "SRD", "BRL"]
            )

        operation_lot_teor_html = "<tr><td colspan='3'>Sem teor aberto.</td></tr>"
        risk_lots_html = "<tr><td colspan='4'>Sem lotes em aberto.</td></tr>"
        if needs_operation_inventory_tables:
            operation_lot_teor_rows: List[str] = []
            for item in cast(List[Dict[str, Any]], operation_lot_market_context.get("by_teor") or [])[:4]:
                pnl_value = Decimal(str(item.get("unrealized_pnl_usd") or "0"))
                pnl_class = "positive" if pnl_value >= 0 else "negative"
                operation_lot_teor_rows.append(
                    f"<tr><td>{escape(str(item.get('teor') or '-'))}%</td><td>{escape(str(item.get('grams') or '0'))} g</td><td class='{pnl_class}'>USD {escape(str(item.get('unrealized_pnl_usd') or '0'))}</td></tr>"
                )
            operation_lot_teor_html = "".join(operation_lot_teor_rows) or operation_lot_teor_html

            risk_lot_rows: List[str] = []
            ranked_risk_lots = cast(List[Dict[str, Any]], operation_lot_market_context.get("risk_lots") or [])
            for item in ranked_risk_lots:
                pnl_value = Decimal(str(item.get("unrealized_pnl_usd") or "0"))
                pnl_class = "positive" if pnl_value >= 0 else "negative"
                risk_lot_rows.append(
                    f"<tr><td>GT-{escape(str(item.get('source_transaction_id', item.get('source_id', ''))))}</td><td>{escape(str(item.get('teor', '-')))}%</td><td>{escape(str(item.get('remaining_grams', '0')))} g</td><td class='{pnl_class}'>USD {escape(str(item.get('unrealized_pnl_usd', '0')))}</td></tr>"
                )
            risk_lots_html = "".join(risk_lot_rows) or risk_lots_html

        user_name = escape(str(session_user.get("nome") or session_user.get("telefone") or "Operador"))
        user_phone = escape(str(session_user.get("telefone") or "-"))
        user_role = escape(str(session_user.get("tipo_usuario") or "operador"))
        chat_bootstrap = [build_saas_chat_welcome(str(session_user.get("nome") or "operador"))]
        recent_fx = build_saas_recent_fx_map(db) if needs_recent_fx else {"USD": "1"}
        payment_rows_html = build_web_payment_rows_html

        return {
            "day": day,
            "needs_statement": needs_statement,
            "needs_market_news": needs_market_news,
            "needs_market_rail": needs_market_rail,
            "needs_sidebar_inventory": needs_sidebar_inventory,
            "statement": statement,
            "client_view": client_view,
            "supplier_view": supplier_view,
            "statement_transactions": statement_transactions,
            "open_fechamentos_statement": open_fechamentos_statement,
            "gold_caixa_metrics": gold_caixa_metrics,
            "market_snapshot": market_snapshot,
            "lot_market_context": lot_market_context,
            "market_trend": market_trend,
            "operation_lot_market_context": operation_lot_market_context,
            "balances_html": balances_html,
            "money_balances_html": money_balances_html,
            "operation_lot_teor_html": operation_lot_teor_html,
            "risk_lots_html": risk_lots_html,
            "recent_html": render_recent_operations_rows(recent_ops) if needs_news_recent_ops else "",
            "user_name": user_name,
            "user_phone": user_phone,
            "user_role": user_role,
            "chat_bootstrap": chat_bootstrap,
            "recent_fx": recent_fx,
            "inventory_available_grams": str(inventory.get("available_grams", "0")),
            "operation_open_lots": open_lots if needs_operation_inventory_tables else [],
        }

    return SimpleNamespace(build_dashboard_context=build_dashboard_context)