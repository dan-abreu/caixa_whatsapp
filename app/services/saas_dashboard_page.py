from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, cast


def build_saas_dashboard_page_helpers(
    *,
    normalize_saas_page: Callable[[Optional[str]], str],
    dashboard_default_form_values: Callable[[Dict[str, Any]], Dict[str, str]],
    runtime_saas_context_helpers: Any,
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_week_range: Callable[[], Dict[str, str]],
    build_saas_statement_context: Callable[[Any, Optional[str], Optional[str]], Dict[str, Any]],
    build_saas_clients_context: Callable[..., Dict[str, Any]],
    build_saas_suppliers_context: Callable[..., Dict[str, Any]],
    collect_open_fechamentos: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    build_gold_caixa_metrics_from_pending_grams: Callable[..., Dict[str, Any]],
    get_market_snapshot: Callable[[], Dict[str, Any]],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    build_operation_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    build_market_trend_context: Callable[[], Dict[str, Any]],
    render_recent_operations_rows: Callable[[List[Dict[str, Any]]], str],
    build_saas_chat_welcome: Callable[[str], Dict[str, str]],
    build_saas_recent_fx_map: Callable[[Any], Dict[str, str]],
    build_web_payment_rows_html: Callable[..., str],
    format_caixa_movement: Callable[[str, Any], str],
    runtime_saas_document_helpers: Any,
    runtime_saas_layout_helpers: Any,
    runtime_saas_page_helpers: Any,
    runtime_saas_operation_page_helpers: Any,
    build_fechamento_status: Callable[..., Dict[str, Any]],
    render_market_panel_html: Callable[..., str],
    get_market_news: Callable[[], List[Dict[str, str]]],
    render_market_news_panel_html: Callable[..., str],
    build_web_lot_monitor_view_model: Callable[..., Dict[str, Any]],
    render_lot_monitor_cards: Callable[..., str],
    normalize_text: Callable[[str], str],
    render_saas_clients_page: Callable[[Dict[str, Any], Dict[str, str]], str],
    render_saas_suppliers_page: Callable[[Dict[str, Any], Dict[str, str]], str],
    render_bank_account_section: Callable[..., str],
    normalize_gold_type: Callable[[Any], str],
    json_for_html_script: Callable[[Any], str],
    asset_url: Callable[[str], str],
) -> SimpleNamespace:
    def render_saas_dashboard_html(
        db: Any,
        session_user: Dict[str, Any],
        notice: Optional[str] = None,
        notice_kind: str = "info",
        assistant_result: Optional[Dict[str, Any]] = None,
        form_values: Optional[Dict[str, str]] = None,
        current_page: str = "dashboard",
        statement_context: Optional[Dict[str, Any]] = None,
        clients_context: Optional[Dict[str, Any]] = None,
        suppliers_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        current_page_local = normalize_saas_page(current_page)
        values = dict(dashboard_default_form_values(session_user))
        if form_values:
            values.update({key: str(value) for key, value in form_values.items()})

        dashboard_context = runtime_saas_context_helpers.build_dashboard_context(
            db=db,
            session_user=session_user,
            current_page=current_page_local,
            statement_context=statement_context,
            clients_context=clients_context,
            suppliers_context=suppliers_context,
            build_day_range=build_day_range,
            build_week_range=build_week_range,
            build_saas_statement_context=build_saas_statement_context,
            build_saas_clients_context=build_saas_clients_context,
            build_saas_suppliers_context=build_saas_suppliers_context,
            collect_open_fechamentos=collect_open_fechamentos,
            build_gold_caixa_metrics_from_pending_grams=build_gold_caixa_metrics_from_pending_grams,
            get_market_snapshot=get_market_snapshot,
            build_open_lot_market_context=build_open_lot_market_context,
            build_operation_lot_market_context=build_operation_lot_market_context,
            build_market_trend_context=build_market_trend_context,
            render_recent_operations_rows=render_recent_operations_rows,
            build_saas_chat_welcome=build_saas_chat_welcome,
            build_saas_recent_fx_map=build_saas_recent_fx_map,
            build_web_payment_rows_html=build_web_payment_rows_html,
            format_caixa_movement=format_caixa_movement,
        )

        day = cast(Dict[str, Any], dashboard_context["day"])
        statement = cast(Dict[str, Any], dashboard_context["statement"])
        client_view = cast(Optional[Dict[str, Any]], dashboard_context["client_view"])
        supplier_view = cast(Optional[Dict[str, Any]], dashboard_context["supplier_view"])
        statement_transactions = cast(List[Dict[str, Any]], dashboard_context["statement_transactions"])
        open_fechamentos_statement = cast(List[Dict[str, Any]], dashboard_context["open_fechamentos_statement"])
        gold_caixa_metrics = cast(Dict[str, Any], dashboard_context["gold_caixa_metrics"])
        market_snapshot = cast(Dict[str, Any], dashboard_context["market_snapshot"])
        lot_market_context = cast(Dict[str, Any], dashboard_context["lot_market_context"])
        market_trend = cast(Dict[str, Any], dashboard_context["market_trend"])
        operation_lot_market_context = cast(Dict[str, Any], dashboard_context["operation_lot_market_context"])
        operation_open_lots = cast(List[Dict[str, Any]], dashboard_context["operation_open_lots"])
        chat_bootstrap = cast(List[Dict[str, Any]], dashboard_context["chat_bootstrap"])
        recent_fx = cast(Dict[str, str], dashboard_context["recent_fx"])

        notice_html = ""
        if notice:
            notice_html = f"<div class='notice {escape(notice_kind)}'>{escape(notice)}</div>"

        if assistant_result and str(assistant_result.get("mensagem") or "").strip():
            chat_bootstrap.append({"role": "assistant", "content": str(assistant_result.get("mensagem") or "")})

        chat_remetente = escape(values["console_remetente"])
        if str(session_user.get("tipo_usuario") or "").lower() == "admin":
            chat_operator_field = f"""
        <label class='chat-meta-field'>Operador / remetente
            <input id='aiChatRemetente' name='console_remetente' value='{chat_remetente}' required />
        </label>
        """
        else:
            chat_operator_field = f"""
        <div class='chat-identity'>Conversando como <strong>{dashboard_context['user_phone']}</strong></div>
        <input id='aiChatRemetente' name='console_remetente' value='{chat_remetente}' type='hidden' />
        """

        selected_client_id = int(values.get("cliente_id") or 0) if str(values.get("cliente_id") or "").isdigit() else 0
        list_cliente_bank_accounts = getattr(db, "list_cliente_bank_accounts", None)
        list_company_bank_accounts = getattr(db, "list_company_bank_accounts", None)
        client_bank_accounts = cast(
            List[Dict[str, Any]],
            list_cliente_bank_accounts(selected_client_id)
            if current_page_local == "operation" and selected_client_id > 0 and callable(list_cliente_bank_accounts)
            else [],
        )
        company_bank_accounts = cast(
            List[Dict[str, Any]],
            list_company_bank_accounts()
            if current_page_local in {"operation", "profile"} and callable(list_company_bank_accounts)
            else [],
        )
        payment_rows_html = build_web_payment_rows_html(values, client_bank_accounts=client_bank_accounts, company_bank_accounts=company_bank_accounts)
        company_bank_accounts_html = ""
        is_admin = str(session_user.get("tipo_usuario") or "").lower() == "admin"
        if current_page_local == "profile":
            company_bank_accounts_html = render_bank_account_section(
                title="Contas Bancarias da Empresa",
                hint="Somente administradores adicionam contas corporativas. Operadores usam essas contas salvas nas transferencias do painel.",
                action="/saas/profile/company-bank-accounts",
                page="profile",
                accounts=company_bank_accounts,
                empty_message="Nenhuma conta corporativa cadastrada.",
                submit_label="Salvar conta corporativa",
                allow_management=is_admin,
            )
        profile_session_user = dict(session_user)
        profile_session_user["company_bank_accounts_html"] = company_bank_accounts_html

        return runtime_saas_document_helpers.render_saas_dashboard_document(
            db=db,
            session_user=profile_session_user,
            current_page=current_page_local,
            values=values,
            user_name=str(dashboard_context["user_name"]),
            user_phone=str(dashboard_context["user_phone"]),
            user_role=str(dashboard_context["user_role"]),
            notice_html=notice_html,
            day_date=str(day["date"]),
            inventory_available_grams=str(dashboard_context["inventory_available_grams"]),
            needs_statement=bool(dashboard_context["needs_statement"]),
            needs_sidebar_inventory=bool(dashboard_context["needs_sidebar_inventory"]),
            needs_market_rail=bool(dashboard_context["needs_market_rail"]),
            needs_market_news=bool(dashboard_context["needs_market_news"]),
            statement=statement,
            statement_transactions=statement_transactions,
            open_fechamentos_statement=open_fechamentos_statement,
            client_view=client_view,
            supplier_view=supplier_view,
            chat_bootstrap=chat_bootstrap,
            recent_fx=recent_fx,
            balances_html=str(dashboard_context["balances_html"]),
            money_balances_html=str(dashboard_context["money_balances_html"]),
            payment_rows_html=payment_rows_html,
            recent_html=str(dashboard_context["recent_html"]),
            gold_caixa_metrics=gold_caixa_metrics,
            market_snapshot=market_snapshot,
            lot_market_context=lot_market_context,
            market_trend=market_trend,
            operation_lot_market_context=operation_lot_market_context,
            operation_open_lots=operation_open_lots,
            operation_lot_teor_html=str(dashboard_context["operation_lot_teor_html"]),
            risk_lots_html=str(dashboard_context["risk_lots_html"]),
            chat_operator_field=chat_operator_field,
            layout_helpers=runtime_saas_layout_helpers,
            page_helpers=runtime_saas_page_helpers,
            operation_page_helpers=runtime_saas_operation_page_helpers,
            build_fechamento_status=build_fechamento_status,
            render_market_panel_html=render_market_panel_html,
            get_market_news=get_market_news,
            render_market_news_panel_html=render_market_news_panel_html,
            build_web_lot_monitor_view_model=build_web_lot_monitor_view_model,
            render_lot_monitor_cards=render_lot_monitor_cards,
            normalize_text=normalize_text,
            build_saas_clients_context=build_saas_clients_context,
            render_saas_clients_page=render_saas_clients_page,
            build_saas_suppliers_context=build_saas_suppliers_context,
            render_saas_suppliers_page=render_saas_suppliers_page,
            normalize_gold_type=normalize_gold_type,
            format_caixa_movement=format_caixa_movement,
            json_for_html_script=json_for_html_script,
            asset_url=asset_url,
        )

    return SimpleNamespace(render_saas_dashboard_html=render_saas_dashboard_html)
