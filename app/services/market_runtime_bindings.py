import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List


def build_market_runtime_helpers(
    *,
    market_service: Any,
    lot_monitoring_service: Any,
    market_monitor_cards: List[Dict[str, Any]],
    market_alert_threshold_pct: Any,
    format_decimal_for_form: Callable[..., str],
    build_saas_lot_monitor_snapshot_cache_key: Callable[[str], str],
    get_saas_lot_monitor_snapshot_cached: Callable[[str], Dict[str, Any] | None],
    set_saas_lot_monitor_snapshot_cached: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    build_web_lot_monitor_view_model: Callable[..., Dict[str, Any]],
    market_stream_interval_seconds: float,
    lot_monitor_stream_interval_seconds: float,
    lot_monitor_enabled: bool,
    lot_monitor_lock: Any,
    lot_monitor_state: Dict[str, Any],
    lot_monitor_stop: Any,
    lot_monitor_worker: Callable[[], None],
    warm_web_runtime_caches: Callable[[], None],
    threading_module: Any,
) -> SimpleNamespace:
    get_market_snapshot = market_service._get_market_snapshot
    build_market_trend_context = market_service._build_market_trend_context
    get_market_news = market_service._get_market_news
    extract_lot_monitor_config = lot_monitoring_service._extract_lot_monitor_config
    build_lot_sell_signal = lot_monitoring_service._build_lot_sell_signal
    format_lot_signal_status = lot_monitoring_service._format_lot_signal_status
    build_web_lot_ai_alert_summary = lot_monitoring_service._build_web_lot_ai_alert_summary
    build_lot_alert_message = lot_monitoring_service._build_lot_alert_message

    def render_market_panel_html(
        market_snapshot: Dict[str, str],
        heading: str = "Painel de Mercado",
        compact: bool = False,
        rail: bool = False,
    ) -> str:
        return market_service._render_market_panel_html(
            market_snapshot,
            market_monitor_cards=market_monitor_cards,
            market_alert_threshold_pct=market_alert_threshold_pct,
            format_live_market_value=market_service._format_live_market_value,
            heading=heading,
            compact=compact,
            rail=rail,
        )

    def build_open_lot_market_context(open_lots: List[Dict[str, Any]], market_snapshot: Dict[str, str]) -> Dict[str, Any]:
        return lot_monitoring_service._build_open_lot_market_context(
            open_lots,
            market_snapshot,
            format_decimal_for_form=format_decimal_for_form,
        )

    def build_operation_lot_market_context(
        open_lots: List[Dict[str, Any]],
        market_snapshot: Dict[str, str],
    ) -> Dict[str, Any]:
        return lot_monitoring_service._build_operation_lot_market_context(
            open_lots,
            market_snapshot,
            format_decimal_for_form=format_decimal_for_form,
        )

    def build_lot_monitor_snapshot_payload(db: Any, session_user: Dict[str, Any]) -> Dict[str, Any]:
        return lot_monitoring_service._build_lot_monitor_snapshot_payload(
            db,
            session_user,
            build_snapshot_cache_key=build_saas_lot_monitor_snapshot_cache_key,
            get_snapshot_cached=get_saas_lot_monitor_snapshot_cached,
            set_snapshot_cached=set_saas_lot_monitor_snapshot_cached,
            get_market_snapshot=get_market_snapshot,
            build_market_trend_context=build_market_trend_context,
            build_open_lot_market_context=build_open_lot_market_context,
            build_web_lot_monitor_view_model=build_web_lot_monitor_view_model,
        )

    def sse_message(data: Dict[str, Any], event: str = "snapshot") -> str:
        return f"event: {event}\\ndata: {json.dumps(data, ensure_ascii=False)}\\n\\n"

    async def market_stream_events(request: Any):
        async for item in market_service._market_stream_events(
            request,
            get_market_snapshot=get_market_snapshot,
            build_sse_message=sse_message,
            cache_ttl_seconds=market_service._MARKET_CACHE_TTL_SECONDS,
            stream_interval_seconds=market_stream_interval_seconds,
        ):
            yield item

    async def lot_monitor_stream_events(request: Any, session_user: Dict[str, Any], db: Any):
        async for item in lot_monitoring_service._lot_monitor_stream_events(
            request,
            session_user,
            db,
            build_lot_monitor_snapshot_payload=build_lot_monitor_snapshot_payload,
            build_sse_message=sse_message,
            stream_interval_seconds=lot_monitor_stream_interval_seconds,
        ):
            yield item

    def start_lot_monitor_background() -> None:
        if lot_monitor_enabled:
            with lot_monitor_lock:
                thread = lot_monitor_state.get("thread")
                if not (thread and thread.is_alive()):
                    lot_monitor_stop.clear()
                    lot_monitor_state["thread"] = threading_module.Thread(
                        target=lot_monitor_worker,
                        name="lot-monitor",
                        daemon=True,
                    )
                    lot_monitor_state["thread"].start()
        threading_module.Thread(target=warm_web_runtime_caches, name="web-cache-warmup", daemon=True).start()

    def stop_lot_monitor_background() -> None:
        lot_monitor_stop.set()

    return SimpleNamespace(
        get_market_snapshot=get_market_snapshot,
        build_market_trend_context=build_market_trend_context,
        get_market_news=get_market_news,
        extract_lot_monitor_config=extract_lot_monitor_config,
        build_lot_sell_signal=build_lot_sell_signal,
        format_lot_signal_status=format_lot_signal_status,
        build_web_lot_ai_alert_summary=build_web_lot_ai_alert_summary,
        build_lot_alert_message=build_lot_alert_message,
        render_market_panel_html=render_market_panel_html,
        build_open_lot_market_context=build_open_lot_market_context,
        build_operation_lot_market_context=build_operation_lot_market_context,
        build_lot_monitor_snapshot_payload=build_lot_monitor_snapshot_payload,
        market_stream_events=market_stream_events,
        lot_monitor_stream_events=lot_monitor_stream_events,
        start_lot_monitor_background=start_lot_monitor_background,
        stop_lot_monitor_background=stop_lot_monitor_background,
    )