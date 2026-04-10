from typing import Any, Dict, Type

from fastapi import FastAPI

from app.routes.saas_auth import register_saas_auth_routes
from app.routes.saas_bank_accounts import register_saas_bank_account_routes
from app.routes.saas_clients import register_saas_client_routes
from app.routes.saas_console import register_saas_console_routes
from app.routes.saas_dashboard import register_saas_dashboard_routes
from app.routes.saas_operations import register_saas_operation_routes
from app.routes.saas_receipts import register_saas_receipt_routes
from app.routes.saas_suppliers import register_saas_supplier_routes


def register_saas_routes_bundle(
    app: FastAPI,
    *,
    get_db: Any,
    auth_helpers: Any,
    ui_helpers: Any,
    runtime_support_helpers: Any,
    saas_helpers: Any,
    saas_dashboard_page_helpers: Any,
    runtime_saas_payment_helpers: Any,
    runtime_saas_date_helpers: Any,
    runtime_saas_form_helpers: Any,
    operation_rule_helpers: Any,
    operation_risk_helpers: Any,
    operation_runtime_helpers: Any,
    whatsapp_session_helpers: Any,
    whatsapp_runtime_binding_helpers: Any,
    friendly_errors: Dict[int, str],
    whatsapp_payload_cls: Type[Any],
    money: Any,
    guided_flow_states: Any,
    saas_session_cookie: str,
) -> None:
    register_saas_dashboard_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        normalize_saas_page=ui_helpers.normalize_saas_page,
        build_saas_statement_context=saas_helpers.build_saas_statement_context,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        build_saas_clients_context=saas_helpers.build_saas_clients_context,
        build_saas_suppliers_context=saas_helpers.build_saas_suppliers_context,
        build_cliente_lookup_meta=ui_helpers.build_cliente_lookup_meta,
    )

    register_saas_client_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        build_saas_clients_context=saas_helpers.build_saas_clients_context,
        build_cliente_lookup_meta=ui_helpers.build_cliente_lookup_meta,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        parse_cliente_opening_balances=runtime_saas_form_helpers.parse_cliente_opening_balances,
        normalize_saas_page=ui_helpers.normalize_saas_page,
        format_cliente_code=ui_helpers.format_cliente_code,
        friendly_errors=friendly_errors,
    )

    register_saas_supplier_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        build_saas_suppliers_context=saas_helpers.build_saas_suppliers_context,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        parse_cliente_opening_balances=runtime_saas_form_helpers.parse_cliente_opening_balances,
        parse_decimal_web_field=runtime_saas_payment_helpers.parse_decimal_web_field,
        normalize_saas_page=ui_helpers.normalize_saas_page,
        format_fornecedor_code=ui_helpers.format_fornecedor_code,
        friendly_errors=friendly_errors,
    )

    register_saas_bank_account_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        normalize_saas_page=ui_helpers.normalize_saas_page,
        build_saas_clients_context=saas_helpers.build_saas_clients_context,
        build_saas_suppliers_context=saas_helpers.build_saas_suppliers_context,
    )

    register_saas_auth_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        normalize_user_phone=runtime_support_helpers.normalize_user_phone,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        set_saas_authenticated_user_cached=auth_helpers.set_saas_authenticated_user_cached,
        set_saas_session=auth_helpers.set_saas_session,
        decode_saas_session=auth_helpers.decode_saas_session,
        saas_session_cookie=saas_session_cookie,
        invalidate_saas_authenticated_user_cache=auth_helpers.invalidate_saas_authenticated_user_cache,
        validate_web_pin_format=auth_helpers.validate_web_pin_format,
        normalize_saas_page=ui_helpers.normalize_saas_page,
    )

    register_saas_operation_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        normalize_saas_page=ui_helpers.normalize_saas_page,
        normalize_user_phone=runtime_support_helpers.normalize_user_phone,
        normalize_text=runtime_support_helpers.normalize_text,
        parse_gold_trade_profile=operation_rule_helpers.parse_gold_trade_profile,
        parse_decimal_web_field=runtime_saas_payment_helpers.parse_decimal_web_field,
        parse_web_payments_from_form=runtime_saas_payment_helpers.parse_web_payments_from_form,
        derive_forma_pagamento_summary=runtime_saas_payment_helpers.derive_forma_pagamento_summary,
        build_cliente_lookup_meta=ui_helpers.build_cliente_lookup_meta,
        attach_sale_profit_reference=operation_risk_helpers.attach_sale_profit_reference,
        project_caixa_balances=operation_risk_helpers.project_caixa_balances,
        find_negative_caixa_balances=operation_risk_helpers.find_negative_caixa_balances,
        format_negative_caixa_lines=operation_risk_helpers.format_negative_caixa_lines,
        persist_gold_operation_from_context=operation_runtime_helpers.persist_gold_operation_from_context,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        build_gold_receipt_context=saas_helpers.build_gold_receipt_context,
        render_saas_receipt_html=saas_helpers.render_saas_receipt_html,
        money=money,
        friendly_errors=friendly_errors,
    )

    register_saas_receipt_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        build_gold_receipt_context=saas_helpers.build_gold_receipt_context,
        render_saas_receipt_html=saas_helpers.render_saas_receipt_html,
        build_gold_receipt_pdf=saas_helpers.build_gold_receipt_pdf,
    )

    register_saas_console_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        render_saas_login_html=ui_helpers.render_saas_login_html,
        clear_saas_session=auth_helpers.clear_saas_session,
        normalize_saas_page=ui_helpers.normalize_saas_page,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        normalize_text=runtime_support_helpers.normalize_text,
        get_session=whatsapp_session_helpers.get_session,
        guided_flow_states=guided_flow_states,
        is_guided_session_stale=whatsapp_session_helpers.is_guided_session_stale,
        clear_session=whatsapp_session_helpers.clear_session,
        whatsapp_payload_cls=whatsapp_payload_cls,
        processar_webhook=whatsapp_runtime_binding_helpers.processar_webhook,
        friendly_errors=friendly_errors,
        build_operation_draft_from_message=saas_helpers.build_operation_draft_from_message,
    )