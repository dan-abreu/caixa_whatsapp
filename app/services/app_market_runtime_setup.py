from types import SimpleNamespace
from typing import Any, Dict, List

from app.services import dashboard_rendering as dashboard_rendering_service
from app.services import dashboard_trends as dashboard_trends_service
from app.services import lot_monitoring as lot_monitoring_service
from app.services import market as market_service
from app.services import market_runtime_bindings as market_runtime_bindings_service
from app.services import runtime_web_helpers as runtime_web_helpers_service


def build_app_market_runtime_setup(
    *,
    get_db: Any,
    logger: Any,
    inventory_metric_helpers: Any,
    runtime_support_helpers: Any,
    runtime_saas_form_helpers: Any,
    runtime_view_helpers: Any,
    market_monitor_cards: List[Dict[str, Any]],
    market_alert_threshold_pct: Any,
    lot_monitor_stop: Any,
    lot_monitor_interval_seconds: int,
    market_stream_interval_seconds: float,
    lot_monitor_stream_interval_seconds: float,
    lot_monitor_enabled: bool,
    lot_monitor_lock: Any,
    lot_monitor_state: Dict[str, Any],
    threading_module: Any,
) -> SimpleNamespace:
    market_runtime_helpers = None
    runtime_web_helpers = None

    def dispatch_build_open_lot_market_context(
        open_lots: List[Dict[str, Any]],
        market_snapshot: Dict[str, str],
    ) -> Dict[str, Any]:
        return market_runtime_helpers.build_open_lot_market_context(open_lots, market_snapshot)

    def dispatch_build_web_lot_monitor_view_model(
        lot_market_context: Dict[str, Any],
        market_trend: Dict[str, Any],
        default_alert_phone: str = "",
        entry_limit: int = 8,
        alert_limit: int = 4,
    ) -> Dict[str, Any]:
        return runtime_web_helpers.build_web_lot_monitor_view_model(
            lot_market_context,
            market_trend,
            default_alert_phone=default_alert_phone,
            entry_limit=entry_limit,
            alert_limit=alert_limit,
        )

    def dispatch_lot_monitor_worker() -> None:
        runtime_web_helpers.lot_monitor_worker()

    def dispatch_warm_web_runtime_caches() -> None:
        runtime_web_helpers.warm_web_runtime_caches()

    runtime_web_helpers = runtime_web_helpers_service.build_runtime_web_helpers(
        dashboard_rendering_service=dashboard_rendering_service,
        dashboard_trends_service=dashboard_trends_service,
        lot_monitoring_service=lot_monitoring_service,
        market_service=market_service,
        get_db=get_db,
        logger=logger,
        collect_open_fechamentos=inventory_metric_helpers.collect_open_fechamentos,
        format_caixa_movement=runtime_support_helpers.format_caixa_movement,
        build_lot_sell_signal=lot_monitoring_service._build_lot_sell_signal,
        format_lot_signal_status=lot_monitoring_service._format_lot_signal_status,
        build_web_lot_ai_alert_summary=lot_monitoring_service._build_web_lot_ai_alert_summary,
        normalize_user_phone=runtime_support_helpers.normalize_user_phone,
        get_market_snapshot=market_service._get_market_snapshot,
        get_market_news=market_service._get_market_news,
        build_market_trend_context=market_service._build_market_trend_context,
        build_open_lot_market_context=dispatch_build_open_lot_market_context,
        extract_lot_monitor_config=lot_monitoring_service._extract_lot_monitor_config,
        build_lot_alert_message=lot_monitoring_service._build_lot_alert_message,
        stop_event=lot_monitor_stop,
        lot_monitor_interval_seconds=lot_monitor_interval_seconds,
    )

    market_runtime_helpers = market_runtime_bindings_service.build_market_runtime_helpers(
        market_service=market_service,
        lot_monitoring_service=lot_monitoring_service,
        market_monitor_cards=market_monitor_cards,
        market_alert_threshold_pct=market_alert_threshold_pct,
        format_decimal_for_form=runtime_saas_form_helpers.format_decimal_for_form,
        build_saas_lot_monitor_snapshot_cache_key=runtime_view_helpers.build_saas_lot_monitor_snapshot_cache_key,
        get_saas_lot_monitor_snapshot_cached=runtime_view_helpers.get_saas_lot_monitor_snapshot_cached,
        set_saas_lot_monitor_snapshot_cached=runtime_view_helpers.set_saas_lot_monitor_snapshot_cached,
        build_web_lot_monitor_view_model=dispatch_build_web_lot_monitor_view_model,
        market_stream_interval_seconds=market_stream_interval_seconds,
        lot_monitor_stream_interval_seconds=lot_monitor_stream_interval_seconds,
        lot_monitor_enabled=lot_monitor_enabled,
        lot_monitor_lock=lot_monitor_lock,
        lot_monitor_state=lot_monitor_state,
        lot_monitor_stop=lot_monitor_stop,
        lot_monitor_worker=dispatch_lot_monitor_worker,
        warm_web_runtime_caches=dispatch_warm_web_runtime_caches,
        threading_module=threading_module,
    )

    return SimpleNamespace(
        runtime_web_helpers=runtime_web_helpers,
        market_runtime_helpers=market_runtime_helpers,
        market_cache_ttl_seconds=market_service._MARKET_CACHE_TTL_SECONDS,
    )