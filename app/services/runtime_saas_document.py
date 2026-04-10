from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from app.services.runtime_saas_document_fragments import build_runtime_saas_document_fragment_helpers


def build_runtime_saas_document_helpers() -> SimpleNamespace:
    fragment_helpers = build_runtime_saas_document_fragment_helpers()

    def render_saas_dashboard_document(
        *,
        db: Any,
        session_user: Dict[str, Any],
        current_page: str,
        values: Dict[str, str],
        user_name: str,
        user_phone: str,
        user_role: str,
        notice_html: str,
        day_date: str,
        inventory_available_grams: str,
        needs_statement: bool,
        needs_sidebar_inventory: bool,
        needs_market_rail: bool,
        needs_market_news: bool,
        statement: Dict[str, Any],
        statement_transactions: List[Dict[str, Any]],
        open_fechamentos_statement: List[Dict[str, Any]],
        client_view: Optional[Dict[str, Any]],
        supplier_view: Optional[Dict[str, Any]],
        chat_bootstrap: List[Dict[str, Any]],
        recent_fx: Dict[str, str],
        balances_html: str,
        money_balances_html: str,
        payment_rows_html: str,
        recent_html: str,
        gold_caixa_metrics: Dict[str, Any],
        market_snapshot: Dict[str, Any],
        lot_market_context: Dict[str, Any],
        market_trend: Dict[str, Any],
        operation_lot_market_context: Dict[str, Any],
        operation_open_lots: List[Dict[str, Any]],
        operation_lot_teor_html: str,
        risk_lots_html: str,
        chat_operator_field: str,
        layout_helpers: Any,
        page_helpers: Any,
        operation_page_helpers: Any,
        build_fechamento_status: Callable[[Any], str],
        render_market_panel_html: Callable[..., str],
        get_market_news: Callable[[], List[Dict[str, Any]]],
        render_market_news_panel_html: Callable[..., str],
        build_web_lot_monitor_view_model: Callable[..., Dict[str, Any]],
        render_lot_monitor_cards: Callable[..., str],
        normalize_text: Callable[[str], str],
        build_saas_clients_context: Callable[[Any], Dict[str, Any]],
        build_saas_suppliers_context: Callable[[Any], Dict[str, Any]],
        render_saas_clients_page: Callable[[Dict[str, Any], Dict[str, str]], str],
        render_saas_suppliers_page: Callable[[Dict[str, Any], Dict[str, str]], str],
        normalize_gold_type: Callable[[Any], str],
        format_caixa_movement: Callable[..., str],
        json_for_html_script: Callable[[Any], str],
        asset_url: Callable[[str], str],
    ) -> str:
        bootstrap_notice = ""
        if session_user.get("web_pin_bootstrap_required"):
            bootstrap_notice = "<div class='notice error'>PIN temporário em uso. Troque o PIN agora para remover o bootstrap de login.</div>"

        web_ai_banner_html = fragment_helpers.build_web_ai_banner_html(current_page)
        nav_html = layout_helpers.build_nav_html(current_page)

        statement_rows_html = (
            layout_helpers.build_statement_rows_html(
                statement_transactions,
                build_fechamento_status=build_fechamento_status,
            )
            if needs_statement
            else "<tr><td colspan='7'>Nenhuma operacao encontrada para o periodo.</td></tr>"
        )

        open_fechamentos_statement_html = (
            layout_helpers.build_open_fechamentos_statement_html(open_fechamentos_statement)
            if needs_statement
            else "<tr><td colspan='5'>Nenhum fechamento parcial em aberto nesse periodo.</td></tr>"
        )

        market_panel_html = render_market_panel_html(market_snapshot, heading="Mercado", rail=True) if needs_market_rail else ""
        market_news_items = get_market_news() if needs_market_news else []
        news_hub_html = render_market_news_panel_html(market_news_items, limit=12) if needs_market_news else ""
        market_rail_html = ""
        if needs_market_rail:
            market_rail_html = f"""
    <aside class='market-rail is-minimized' id='marketRail'>
        {market_panel_html}
    </aside>
    """

        shared_top_shell_html = layout_helpers.build_shared_top_shell_html(
            user_name=user_name,
            nav_html=nav_html,
            day_date=day_date,
            sidebar_inventory_grams=inventory_available_grams if needs_sidebar_inventory else "",
            market_rail_html=market_rail_html,
        )

        default_alert_phone = str(session_user.get("telefone") or "")
        web_lot_ai_alerts: List[Dict[str, Any]] = []
        web_lot_ai_summary = ""
        lot_monitor_entries: List[Dict[str, Any]] = []
        if current_page == "monitors":
            lot_monitor_model = build_web_lot_monitor_view_model(
                lot_market_context,
                market_trend,
                default_alert_phone=default_alert_phone,
                entry_limit=24,
                alert_limit=4,
            )
            web_lot_ai_alerts = list(lot_monitor_model.get("alerts") or [])
            web_lot_ai_summary = str(lot_monitor_model.get("summary") or "")
            lot_monitor_entries = list(lot_monitor_model.get("entries") or [])

        dashboard_bootstrap_json = json_for_html_script(
            {
                "chatHistory": chat_bootstrap,
                "lotAlerts": web_lot_ai_alerts,
                "lotMonitorEntries": lot_monitor_entries,
                "lotSummary": web_lot_ai_summary,
                "recentFx": recent_fx,
                "currentPage": current_page,
                "marketSnapshot": fragment_helpers.build_market_snapshot_client(market_snapshot),
            }
        )

        enabled_lot_monitor_entries = [item for item in lot_monitor_entries if item.get("enabled")] if current_page == "monitors" else []
        full_lot_monitor_html = ""
        monitor_alerts_html = "<tr><td colspan='4'>Nenhum gatilho ativo agora.</td></tr>"
        if current_page == "monitors":
            full_lot_monitor_html = render_lot_monitor_cards(
                lot_monitor_entries,
                "monitors",
                "Nenhum lote aberto para monitorar.",
                default_alert_phone,
            )
            monitor_alerts_html = layout_helpers.build_monitor_alerts_html(web_lot_ai_alerts)

        news_gold_count = sum(1 for item in market_news_items if normalize_text(str(item.get("topic") or "")) in {"ouro", "gold"}) if needs_market_news else 0
        news_fx_count = (len(market_news_items) - news_gold_count) if needs_market_news else 0
        pending_open_gold_total = gold_caixa_metrics["ouro_pendente"]

        if current_page == "dashboard":
            page_content_html = page_helpers.render_dashboard_page_content(balances_html)
        elif current_page == "monitors":
            page_content_html = page_helpers.render_monitors_page_content(
                lot_monitor_entries,
                enabled_lot_monitor_entries,
                web_lot_ai_alerts,
                full_lot_monitor_html,
                monitor_alerts_html,
            )
        elif current_page == "news_hub":
            page_content_html = page_helpers.render_news_page_content(
                market_news_items,
                news_gold_count,
                news_fx_count,
                news_hub_html,
                recent_html,
            )
        elif current_page == "operation":
            page_content_html = operation_page_helpers.render_operation_page_content(
                values=values,
                payment_rows_html=payment_rows_html,
                money_balances_html=money_balances_html,
                pending_open_gold_total=pending_open_gold_total,
                gold_caixa_metrics=gold_caixa_metrics,
                operation_lot_market_context=operation_lot_market_context,
                operation_open_lots=operation_open_lots,
                operation_lot_teor_html=operation_lot_teor_html,
                risk_lots_html=risk_lots_html,
                normalize_gold_type=normalize_gold_type,
                format_caixa_movement=format_caixa_movement,
            )
        elif current_page == "clients":
            page_content_html = render_saas_clients_page(client_view or build_saas_clients_context(db), values)
        elif current_page == "suppliers":
            page_content_html = render_saas_suppliers_page(supplier_view or build_saas_suppliers_context(db), values)
        elif current_page == "profile":
            page_content_html = page_helpers.render_profile_page_content(
                session_user,
                user_name,
                user_phone,
                user_role,
                balances_html,
            )
        else:
            page_content_html = page_helpers.render_statement_page_content(
                statement,
                statement_rows_html,
                open_fechamentos_statement_html,
            )

        floating_ai_html = fragment_helpers.build_floating_ai_html(
            current_page,
            chat_operator_field,
            values["console_mensagem"],
        )
        saas_css_url = asset_url("saas.css")
        saas_shared_runtime_js_url = asset_url("saas/shared-runtime.js")
        saas_fragments_js_url = asset_url("saas/fragments.js")
        saas_lot_alerts_js_url = asset_url("saas/lot-alerts.js")
        saas_market_core_js_url = asset_url("saas/market-core.js")
        saas_market_runtime_js_url = asset_url("saas/market-runtime.js")
        saas_chat_runtime_js_url = asset_url("saas/chat-runtime.js")
        saas_widget_runtime_js_url = asset_url("saas/widget-runtime.js")
        saas_operation_client_runtime_js_url = asset_url("saas/operation-form/client-runtime.js")
        saas_operation_calculator_runtime_js_url = asset_url("saas/operation-form/calculator-runtime.js")
        saas_operation_enhancements_runtime_js_url = asset_url("saas/operation-form/enhancements-runtime.js")
        saas_operation_submission_runtime_js_url = asset_url("saas/operation-form/submission-runtime.js")
        saas_operation_form_runtime_js_url = asset_url("saas/operation-form/runtime.js")
        saas_js_url = asset_url("saas-bootstrap.js")

        return f"""
    <html>
        <head>
            <title>Caixa SaaS Dashboard</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <link rel='preload' href='{saas_css_url}' as='style'>
            <link href='{saas_css_url}' rel='stylesheet'>
            <link rel='preload' href='{saas_js_url}' as='script'>
        </head>
        <body>
            {shared_top_shell_html}
            <div class='wrap app-content-wrap'>
                {bootstrap_notice}
                {notice_html}
                {web_ai_banner_html}
                {page_content_html}
            </div>
            {floating_ai_html}
            <script id='saasDashboardBootstrap' type='application/json'>{dashboard_bootstrap_json}</script>
            <script src='{saas_shared_runtime_js_url}' defer></script>
            <script src='{saas_fragments_js_url}' defer></script>
            <script src='{saas_lot_alerts_js_url}' defer></script>
            <script src='{saas_market_core_js_url}' defer></script>
            <script src='{saas_market_runtime_js_url}' defer></script>
            <script src='{saas_chat_runtime_js_url}' defer></script>
            <script src='{saas_widget_runtime_js_url}' defer></script>
            <script src='{saas_operation_client_runtime_js_url}' defer></script>
            <script src='{saas_operation_calculator_runtime_js_url}' defer></script>
            <script src='{saas_operation_enhancements_runtime_js_url}' defer></script>
            <script src='{saas_operation_submission_runtime_js_url}' defer></script>
            <script src='{saas_operation_form_runtime_js_url}' defer></script>
            <script src='{saas_js_url}' defer></script>
        </body>
    </html>
    """

    return SimpleNamespace(render_saas_dashboard_document=render_saas_dashboard_document)