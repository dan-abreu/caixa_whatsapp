from typing import Any, Dict, Type
from urllib.parse import parse_qs

from fastapi import FastAPI

from app.routes.ai_health import register_ai_health_routes
from app.routes.analytics_api import register_analytics_routes
from app.routes.dashboard_runtime import register_dashboard_runtime_routes
from app.routes.operations_management import register_operation_management_routes
from app.routes.system_routes import register_system_routes
from app.routes.webhooks import register_webhook_routes


def register_runtime_routes_bundle(
    app: FastAPI,
    *,
    get_db: Any,
    auth_helpers: Any,
    runtime_saas_helpers: Any,
    market_runtime_helpers: Any,
    runtime_web_helpers: Any,
    runtime_view_helpers: Any,
    runtime_saas_date_helpers: Any,
    runtime_saas_payment_helpers: Any,
    runtime_support_helpers: Any,
    reporting_runtime_helpers: Any,
    saas_dashboard_page_helpers: Any,
    runtime_saas_ui_helpers: Any,
    inventory_metric_helpers: Any,
    ai_conf_helpers: Any,
    validate_webhook_token: Any,
    whatsapp_payload_cls: Type[Any],
    whatsapp_runtime_binding_helpers: Any,
    idempotency_cache: Dict[str, Dict[str, Any]],
    market_cache_ttl_seconds: float,
    dashboard_fragment_news_name: str,
    dashboard_fragment_monitors_name: str,
    dashboard_fragment_inventory_name: str,
    dashboard_fragment_trend_name: str,
    dashboard_fragment_summary_name: str,
    dashboard_fragment_pending_closings_name: str,
    dashboard_fragment_recent_operations_name: str,
    friendly_errors: Dict[int, str],
    logger: Any,
) -> None:
    register_dashboard_runtime_routes(
        app,
        get_db=get_db,
        get_saas_authenticated_user=auth_helpers.get_saas_authenticated_user,
        build_inventory_status_report_payload=reporting_runtime_helpers.build_inventory_status_report_payload,
        get_market_snapshot=market_runtime_helpers.get_market_snapshot,
        market_cache_ttl_seconds=market_cache_ttl_seconds,
        market_stream_events=market_runtime_helpers.market_stream_events,
        get_market_news=market_runtime_helpers.get_market_news,
        build_dashboard_fragment_cache_key=runtime_view_helpers.build_dashboard_fragment_cache_key,
        dashboard_fragment_news_name=dashboard_fragment_news_name,
        dashboard_fragment_monitors_name=dashboard_fragment_monitors_name,
        dashboard_fragment_inventory_name=dashboard_fragment_inventory_name,
        dashboard_fragment_trend_name=dashboard_fragment_trend_name,
        dashboard_fragment_summary_name=dashboard_fragment_summary_name,
        dashboard_fragment_pending_closings_name=dashboard_fragment_pending_closings_name,
        dashboard_fragment_recent_operations_name=dashboard_fragment_recent_operations_name,
        render_cached_dashboard_fragment=runtime_view_helpers.render_cached_dashboard_fragment,
        render_market_news_panel_html=runtime_web_helpers.render_market_news_panel_html,
        normalize_user_phone=runtime_support_helpers.normalize_user_phone,
        build_open_lot_market_context=market_runtime_helpers.build_open_lot_market_context,
        build_market_trend_context=market_runtime_helpers.build_market_trend_context,
        build_web_lot_monitor_view_model=runtime_web_helpers.build_web_lot_monitor_view_model,
        render_lot_monitor_cards=runtime_web_helpers.render_lot_monitor_cards,
        render_dashboard_inventory_html=runtime_web_helpers.render_dashboard_inventory_html,
        render_dashboard_trend_html=runtime_web_helpers.render_dashboard_trend_html,
        build_day_range=runtime_saas_date_helpers.build_day_range,
        build_statement_summary=runtime_saas_helpers.build_statement_summary,
        build_gold_caixa_metrics_from_pending_grams=inventory_metric_helpers.build_gold_caixa_metrics_from_pending_grams,
        render_dashboard_summary_html=runtime_web_helpers.render_dashboard_summary_html,
        build_week_range=runtime_saas_date_helpers.build_week_range,
        render_dashboard_pending_closings_html=runtime_web_helpers.render_dashboard_pending_closings_html,
        render_dashboard_recent_operations_html=runtime_web_helpers.render_dashboard_recent_operations_html,
        build_lot_monitor_snapshot_payload=market_runtime_helpers.build_lot_monitor_snapshot_payload,
        lot_monitor_stream_events=market_runtime_helpers.lot_monitor_stream_events,
        request_form_dict=runtime_saas_date_helpers.request_form_dict,
        parse_decimal_web_field=runtime_saas_payment_helpers.parse_decimal_web_field,
        invalidate_dashboard_monitors_fragment_cache=runtime_view_helpers.invalidate_dashboard_monitors_fragment_cache,
        invalidate_lot_monitor_snapshot_cache=runtime_view_helpers.invalidate_lot_monitor_snapshot_cache,
        render_saas_dashboard_html=saas_dashboard_page_helpers.render_saas_dashboard_html,
        build_admin_dashboard_html=reporting_runtime_helpers.build_admin_dashboard_html,
        render_saas_login_html=runtime_saas_ui_helpers.render_saas_login_html,
        validate_webhook_token=validate_webhook_token,
    )

    register_analytics_routes(
        app,
        get_db=get_db,
        build_day_range=runtime_saas_date_helpers.build_day_range,
        build_custom_range=runtime_saas_date_helpers.build_custom_range,
    )

    register_ai_health_routes(
        app,
        get_db=get_db,
        get_ai_conf_config=ai_conf_helpers.get_ai_conf_config,
    )

    register_system_routes(
        app,
        get_db=get_db,
        build_day_range=runtime_saas_date_helpers.build_day_range,
    )

    register_webhook_routes(
        app,
        get_db=get_db,
        validate_webhook_token=validate_webhook_token,
        whatsapp_payload_cls=whatsapp_payload_cls,
        processar_webhook=whatsapp_runtime_binding_helpers.processar_webhook,
        idempotency_cache=idempotency_cache,
        friendly_errors=friendly_errors,
        parse_query_string=lambda raw: {key: values[0] for key, values in parse_qs(raw).items() if values},
        logger=logger,
    )

    register_operation_management_routes(
        app,
        get_db=get_db,
        validate_webhook_token=validate_webhook_token,
        invalidate_operation_related_view_caches=runtime_view_helpers.invalidate_operation_related_view_caches,
    )