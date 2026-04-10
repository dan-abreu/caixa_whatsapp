import json
import threading
from typing import Any, Dict

from fastapi import HTTPException

from app.database import DatabaseClient, DatabaseError
from app.services import lot_monitoring as lot_monitoring_service
from app.services import market as market_service
from app.services import reporting as reporting_service


_DB_INSTANCE_LOCK = threading.Lock()


def build_main_compat_exports(
    *,
    module_globals: Dict[str, Any],
    runtime_composition_helpers: Any,
    runtime_view_helpers: Any,
    market_runtime_helpers: Any,
    inventory_metric_helpers: Any,
    guided_flow_fx_helpers: Any,
    runtime_support_helpers: Any,
    whatsapp_input_parser_helpers: Any,
    runtime_saas_payment_helpers: Any,
    runtime_saas_form_helpers: Any,
    operation_rule_helpers: Any,
    support_helpers: Any,
    lot_monitor_stream_interval_seconds: float,
) -> Dict[str, Any]:
    module_globals.setdefault("DatabaseClient", DatabaseClient)
    module_globals.setdefault("_DB_INSTANCE", None)

    def get_db() -> DatabaseClient:
        db_instance = module_globals.get("_DB_INSTANCE")
        if db_instance is not None:
            return db_instance
        try:
            with _DB_INSTANCE_LOCK:
                cached_instance = module_globals.get("_DB_INSTANCE")
                if cached_instance is None:
                    module_globals["_DB_INSTANCE"] = module_globals["DatabaseClient"]()
                return module_globals["_DB_INSTANCE"]
        except DatabaseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    def _build_web_lot_ai_alerts(lot_market_context: Dict[str, Any], market_trend: Dict[str, Any], limit: int = 4) -> list[Dict[str, Any]]:
        return getattr(lot_monitoring_service, "_build_web_lot_ai_alerts")(
            lot_market_context,
            market_trend,
            build_lot_sell_signal=module_globals["_build_lot_sell_signal"],
            format_lot_signal_status=module_globals["_format_lot_signal_status"],
            limit=limit,
        )

    def _build_web_lot_monitor_view_model(
        lot_market_context: Dict[str, Any],
        market_trend: Dict[str, Any],
        default_alert_phone: str = "",
        entry_limit: int = 8,
        alert_limit: int = 4,
    ) -> Dict[str, Any]:
        return getattr(lot_monitoring_service, "_build_web_lot_monitor_view_model")(
            lot_market_context,
            market_trend,
            build_lot_sell_signal=module_globals["_build_lot_sell_signal"],
            format_lot_signal_status=module_globals["_format_lot_signal_status"],
            build_alert_summary=module_globals["_build_web_lot_ai_alert_summary"],
            default_alert_phone=default_alert_phone,
            entry_limit=entry_limit,
            alert_limit=alert_limit,
        )

    def _build_web_lot_monitor_entries(
        lot_market_context: Dict[str, Any],
        market_trend: Dict[str, Any],
        default_alert_phone: str = "",
        limit: int = 8,
    ) -> list[Dict[str, Any]]:
        return getattr(lot_monitoring_service, "_build_web_lot_monitor_entries")(
            lot_market_context,
            market_trend,
            build_lot_sell_signal=module_globals["_build_lot_sell_signal"],
            format_lot_signal_status=module_globals["_format_lot_signal_status"],
            default_alert_phone=default_alert_phone,
            limit=limit,
        )

    def _build_inventory_status_report_payload(db: Any) -> Dict[str, Any]:
        return getattr(reporting_service, "_build_inventory_status_report_payload")(
            db,
            get_cached_payload=runtime_view_helpers.get_inventory_status_report_cached,
            set_cached_payload=runtime_view_helpers.set_inventory_status_report_cached,
            get_market_snapshot=module_globals["_get_market_snapshot"],
            build_open_lot_market_context=module_globals["_build_open_lot_market_context"],
            compute_inventory_metrics=inventory_metric_helpers.compute_inventory_metrics,
            build_fifo_inventory_lots=inventory_metric_helpers.build_fifo_inventory_lots,
        )

    def _build_lot_monitor_snapshot_payload(db: Any, session_user: Dict[str, Any]) -> Dict[str, Any]:
        return getattr(lot_monitoring_service, "_build_lot_monitor_snapshot_payload")(
            db,
            session_user,
            build_snapshot_cache_key=runtime_view_helpers.build_saas_lot_monitor_snapshot_cache_key,
            get_snapshot_cached=runtime_view_helpers.get_saas_lot_monitor_snapshot_cached,
            set_snapshot_cached=runtime_view_helpers.set_saas_lot_monitor_snapshot_cached,
            get_market_snapshot=module_globals["_get_market_snapshot"],
            build_market_trend_context=module_globals["_build_market_trend_context"],
            build_open_lot_market_context=module_globals["_build_open_lot_market_context"],
            build_web_lot_monitor_view_model=module_globals["_build_web_lot_monitor_view_model"],
        )

    async def _lot_monitor_stream_events(request: Any, session_user: Dict[str, Any], db: Any):
        async for item in getattr(lot_monitoring_service, "_lot_monitor_stream_events")(
            request,
            session_user,
            db,
            build_lot_monitor_snapshot_payload=module_globals["_build_lot_monitor_snapshot_payload"],
            build_sse_message=lambda data: f"event: snapshot\ndata: {json.dumps(data, ensure_ascii=False)}\n\n",
            stream_interval_seconds=lot_monitor_stream_interval_seconds,
        ):
            yield item

    exports = {
        "DatabaseClient": DatabaseClient,
        "_DB_INSTANCE": module_globals.get("_DB_INSTANCE"),
        "get_db": get_db,
        "_MARKET_TICK_HISTORY": getattr(market_service, "_MARKET_TICK_HISTORY"),
        "_extract_awesomeapi_gold_price": getattr(market_service, "_extract_awesomeapi_gold_price"),
        "_extract_gold_api_xau_usd": getattr(market_service, "_extract_gold_api_xau_usd"),
        "_build_market_snapshot_from_rates": getattr(market_service, "_build_market_snapshot_from_rates"),
        "_parse_google_news_feed": getattr(market_service, "_parse_google_news_feed"),
        "_build_fechamento_status": inventory_metric_helpers.build_fechamento_status,
        "_build_gold_caixa_metrics": inventory_metric_helpers.build_gold_caixa_metrics,
        "_build_gold_caixa_metrics_from_pending_grams": inventory_metric_helpers.build_gold_caixa_metrics_from_pending_grams,
        "_build_fifo_inventory_lots": inventory_metric_helpers.build_fifo_inventory_lots,
        "_sum_open_fechamento_grams": inventory_metric_helpers.sum_open_fechamento_grams,
        "_display_cambio_for_web_input": guided_flow_fx_helpers.display_cambio_for_web_input,
        "_normalize_cambio_para_usd": guided_flow_fx_helpers.normalize_cambio_para_usd,
        "_preview_fifo_sale_consumption": inventory_metric_helpers.preview_fifo_sale_consumption,
        "_parse_decimal_from_text": runtime_support_helpers.parse_decimal_from_text,
        "_parse_operation_reference": whatsapp_input_parser_helpers.parse_operation_reference,
        "_parse_web_payments_from_form": runtime_saas_payment_helpers.parse_web_payments_from_form,
        "_derive_forma_pagamento_summary": runtime_saas_payment_helpers.derive_forma_pagamento_summary,
        "_build_saas_recent_fx_map": runtime_saas_form_helpers.build_saas_recent_fx_map,
        "_parse_gold_trade_profile": operation_rule_helpers.parse_gold_trade_profile,
        "_find_negative_caixa_balances": support_helpers.operation_risk_helpers.find_negative_caixa_balances,
        "_project_caixa_balances": support_helpers.operation_risk_helpers.project_caixa_balances,
        "_should_reset_guided_session_for_message": support_helpers.whatsapp_message_pattern_helpers.should_reset_guided_session_for_message,
        "_build_operation_draft_from_message": runtime_composition_helpers.runtime_saas_helpers.build_operation_draft_from_message,
        "_build_saas_statement_context": runtime_composition_helpers.runtime_saas_helpers.build_saas_statement_context,
        "_build_gold_receipt_context": runtime_composition_helpers.runtime_saas_helpers.build_gold_receipt_context,
        "_build_admin_dashboard_html": runtime_composition_helpers.reporting_runtime_helpers.build_admin_dashboard_html,
        "_render_saas_dashboard_html": runtime_composition_helpers.saas_dashboard_page_helpers.render_saas_dashboard_html,
        "_invalidate_statement_context_cache": runtime_view_helpers.invalidate_statement_context_cache,
        "_invalidate_recent_fx_map_cache": runtime_view_helpers.invalidate_recent_fx_map_cache,
        "_invalidate_lot_monitor_snapshot_cache": runtime_view_helpers.invalidate_lot_monitor_snapshot_cache,
        "_invalidate_reporting_cache": runtime_view_helpers.invalidate_reporting_cache,
        "_invalidate_receipt_context_cache": runtime_view_helpers.invalidate_receipt_context_cache,
        "_get_market_snapshot": market_runtime_helpers.get_market_snapshot,
        "_build_market_trend_context": market_runtime_helpers.build_market_trend_context,
        "_build_open_lot_market_context": market_runtime_helpers.build_open_lot_market_context,
        "_build_operation_lot_market_context": market_runtime_helpers.build_operation_lot_market_context,
        "_build_lot_sell_signal": market_runtime_helpers.build_lot_sell_signal,
        "_format_lot_signal_status": market_runtime_helpers.format_lot_signal_status,
        "_build_web_lot_ai_alert_summary": market_runtime_helpers.build_web_lot_ai_alert_summary,
        "_build_web_lot_ai_alerts": _build_web_lot_ai_alerts,
        "_build_web_lot_monitor_view_model": _build_web_lot_monitor_view_model,
        "_build_web_lot_monitor_entries": _build_web_lot_monitor_entries,
        "_build_inventory_status_report_payload": _build_inventory_status_report_payload,
        "_build_lot_monitor_snapshot_payload": _build_lot_monitor_snapshot_payload,
        "_lot_monitor_stream_events": _lot_monitor_stream_events,
    }
    exports["_COMPAT_EXPORTS"] = (
        _build_web_lot_ai_alerts,
        _build_web_lot_monitor_view_model,
        _build_web_lot_monitor_entries,
        _build_inventory_status_report_payload,
        _build_lot_monitor_snapshot_payload,
        _lot_monitor_stream_events,
    )
    return exports