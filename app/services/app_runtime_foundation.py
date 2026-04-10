from types import SimpleNamespace
from typing import Any, Dict

from app.services import ai_conf as ai_conf_service
from app.services import dashboard_fragments as dashboard_fragments_service
from app.services import guided_flow_fx as guided_flow_fx_service
from app.services import inventory_metrics as inventory_metrics_service
from app.services import operation_rules as operation_rules_service
from app.services import runtime_saas_auth as runtime_saas_auth_service
from app.services import runtime_saas_dates as runtime_saas_dates_service
from app.services import runtime_saas_forms as runtime_saas_forms_service
from app.services import runtime_saas_payments as runtime_saas_payments_service
from app.services import runtime_saas_ui as runtime_saas_ui_service
from app.services import runtime_support as runtime_support_service
from app.services import runtime_view_state as runtime_view_state_service
from app.services import view_caches as view_caches_service
from app.services import whatsapp_input_parsers as whatsapp_input_parsers_service
from app.shared_cache import get_shared_cache


def build_app_runtime_foundation(
    *,
    http_exception_cls: Any,
    money: Any,
    fx_rate: Any,
    asset_url: Any,
    saas_session_ttl_seconds: int,
    saas_session_cookie: str,
    saas_cookie_secure: bool,
    saas_auth_user_cache: Dict[str, Dict[str, Any]],
    saas_auth_user_cache_ttl_seconds: int,
    dashboard_fragment_cache: Dict[str, Dict[str, Any]],
    dashboard_fragment_cache_ttl_seconds: int,
    dashboard_fragment_cache_key_prefix: str,
    dashboard_fragment_monitors_name: str,
    dashboard_fragment_inventory_name: str,
    dashboard_fragment_trend_name: str,
    dashboard_fragment_summary_name: str,
    dashboard_fragment_pending_closings_name: str,
    dashboard_fragment_recent_operations_name: str,
    saas_statement_context_cache_key_prefix: str,
    saas_statement_context_cache: Dict[str, Dict[str, Any]],
    saas_statement_context_cache_ttl_seconds: int,
    saas_recent_fx_cache: Dict[str, Any],
    saas_recent_fx_cache_ttl_seconds: int,
    saas_receipt_context_cache_key_prefix: str,
    saas_receipt_context_cache: Dict[str, Dict[str, Any]],
    saas_receipt_context_cache_ttl_seconds: int,
    saas_lot_monitor_snapshot_cache_key_prefix: str,
    saas_lot_monitor_snapshot_cache: Dict[str, Dict[str, Any]],
    saas_lot_monitor_snapshot_cache_ttl_seconds: float,
    admin_dashboard_cache_key_prefix: str,
    inventory_status_cache: Dict[str, Any],
    inventory_status_cache_ttl_seconds: int,
    admin_dashboard_cache: Dict[str, Dict[str, Any]],
    admin_dashboard_cache_ttl_seconds: int,
) -> SimpleNamespace:
    runtime_support_helpers = runtime_support_service.build_runtime_support_helpers(
        http_exception_cls=http_exception_cls,
    )
    parse_decimal = runtime_support_helpers.parse_decimal

    runtime_view_helpers = runtime_view_state_service.build_runtime_view_helpers(
        dashboard_fragments_service=dashboard_fragments_service,
        view_caches_service=view_caches_service,
        get_shared_cache_backend=get_shared_cache,
        dashboard_fragment_cache=dashboard_fragment_cache,
        dashboard_fragment_cache_ttl_seconds=dashboard_fragment_cache_ttl_seconds,
        dashboard_fragment_cache_key_prefix=dashboard_fragment_cache_key_prefix,
        dashboard_fragment_monitors_name=dashboard_fragment_monitors_name,
        dashboard_fragment_inventory_name=dashboard_fragment_inventory_name,
        dashboard_fragment_trend_name=dashboard_fragment_trend_name,
        dashboard_fragment_summary_name=dashboard_fragment_summary_name,
        dashboard_fragment_pending_closings_name=dashboard_fragment_pending_closings_name,
        dashboard_fragment_recent_operations_name=dashboard_fragment_recent_operations_name,
        saas_statement_context_cache_key_prefix=saas_statement_context_cache_key_prefix,
        saas_statement_context_cache=saas_statement_context_cache,
        saas_statement_context_cache_ttl_seconds=saas_statement_context_cache_ttl_seconds,
        saas_recent_fx_cache=saas_recent_fx_cache,
        saas_recent_fx_cache_ttl_seconds=saas_recent_fx_cache_ttl_seconds,
        saas_receipt_context_cache_key_prefix=saas_receipt_context_cache_key_prefix,
        saas_receipt_context_cache=saas_receipt_context_cache,
        saas_receipt_context_cache_ttl_seconds=saas_receipt_context_cache_ttl_seconds,
        saas_lot_monitor_snapshot_cache_key_prefix=saas_lot_monitor_snapshot_cache_key_prefix,
        saas_lot_monitor_snapshot_cache=saas_lot_monitor_snapshot_cache,
        saas_lot_monitor_snapshot_cache_ttl_seconds=saas_lot_monitor_snapshot_cache_ttl_seconds,
        admin_dashboard_cache_key_prefix=admin_dashboard_cache_key_prefix,
        inventory_status_cache=inventory_status_cache,
        inventory_status_cache_ttl_seconds=inventory_status_cache_ttl_seconds,
        admin_dashboard_cache=admin_dashboard_cache,
        admin_dashboard_cache_ttl_seconds=admin_dashboard_cache_ttl_seconds,
        normalize_user_phone=runtime_support_helpers.normalize_user_phone,
    )

    ai_conf_helpers = ai_conf_service.build_ai_conf_helpers()
    runtime_saas_ui_helpers = runtime_saas_ui_service.build_runtime_saas_ui_helpers(
        asset_url=asset_url,
        normalize_text=runtime_support_helpers.normalize_text,
    )
    whatsapp_input_parser_helpers = whatsapp_input_parsers_service.build_whatsapp_input_parser_helpers(
        normalize_text=runtime_support_helpers.normalize_text,
    )
    inventory_metric_helpers = inventory_metrics_service.build_inventory_metric_helpers(
        money=money,
    )
    runtime_saas_auth_helpers = runtime_saas_auth_service.build_runtime_saas_auth_helpers(
        session_ttl_seconds=saas_session_ttl_seconds,
        session_cookie=saas_session_cookie,
        cookie_secure=saas_cookie_secure,
        auth_user_cache=saas_auth_user_cache,
        auth_user_cache_ttl_seconds=saas_auth_user_cache_ttl_seconds,
    )

    runtime_saas_form_helpers = None
    guided_flow_fx_helpers = guided_flow_fx_service.build_guided_flow_fx_helpers(
        fx_rate=fx_rate,
        money=money,
        format_decimal_for_form=lambda value, places=2: runtime_saas_form_helpers.format_decimal_for_form(value, places),
    )
    runtime_saas_payment_helpers = runtime_saas_payments_service.build_runtime_saas_payment_helpers(
        normalize_text=runtime_support_helpers.normalize_text,
        payment_fx_prompt_label=guided_flow_fx_helpers.payment_fx_prompt_label,
        parse_decimal=parse_decimal,
        normalize_cambio_para_usd=guided_flow_fx_helpers.normalize_cambio_para_usd,
        money=money,
        fx_rate=fx_rate,
    )
    runtime_saas_date_helpers = runtime_saas_dates_service.build_runtime_saas_date_helpers()
    operation_rule_helpers = operation_rules_service.build_operation_rule_helpers(
        normalize_text=runtime_support_helpers.normalize_text,
        parse_decimal_web_field=lambda raw, field_name: runtime_saas_payment_helpers.parse_decimal_web_field(raw, field_name),
        money=money,
    )
    runtime_saas_form_helpers = runtime_saas_forms_service.build_runtime_saas_form_helpers(
        get_saas_recent_fx_cached=runtime_view_helpers.get_saas_recent_fx_cached,
        set_saas_recent_fx_cached=runtime_view_helpers.set_saas_recent_fx_cached,
        display_cambio_for_web_input=guided_flow_fx_helpers.display_cambio_for_web_input,
        parse_decimal_web_field=runtime_saas_payment_helpers.parse_decimal_web_field,
    )

    return SimpleNamespace(
        ai_conf_helpers=ai_conf_helpers,
        runtime_support_helpers=runtime_support_helpers,
        runtime_view_helpers=runtime_view_helpers,
        runtime_saas_ui_helpers=runtime_saas_ui_helpers,
        whatsapp_input_parser_helpers=whatsapp_input_parser_helpers,
        inventory_metric_helpers=inventory_metric_helpers,
        runtime_saas_auth_helpers=runtime_saas_auth_helpers,
        guided_flow_fx_helpers=guided_flow_fx_helpers,
        runtime_saas_payment_helpers=runtime_saas_payment_helpers,
        runtime_saas_date_helpers=runtime_saas_date_helpers,
        operation_rule_helpers=operation_rule_helpers,
        runtime_saas_form_helpers=runtime_saas_form_helpers,
        parse_decimal=parse_decimal,
        normalize_ativo_nome=operation_rule_helpers.normalize_ativo_nome,
        infer_tipo_operacao=operation_rule_helpers.infer_tipo_operacao,
    )