from types import SimpleNamespace
from typing import Any, Callable, Dict, List


def build_runtime_web_helpers(
    *,
    dashboard_rendering_service: Any,
    dashboard_trends_service: Any,
    lot_monitoring_service: Any,
    market_service: Any,
    get_db: Callable[[], Any],
    logger: Any,
    collect_open_fechamentos: Callable[..., Any],
    format_caixa_movement: Callable[..., str],
    build_lot_sell_signal: Callable[..., Any],
    format_lot_signal_status: Callable[..., str],
    build_web_lot_ai_alert_summary: Callable[..., Any],
    normalize_user_phone: Callable[[str], str],
    get_market_snapshot: Callable[[], Dict[str, str]],
    get_market_news: Callable[[], List[Dict[str, str]]],
    build_market_trend_context: Callable[..., Dict[str, Any]],
    build_open_lot_market_context: Callable[..., Dict[str, Any]],
    extract_lot_monitor_config: Callable[..., Dict[str, Any]],
    build_lot_alert_message: Callable[..., str],
    stop_event: Any,
    lot_monitor_interval_seconds: int,
) -> SimpleNamespace:
    def render_market_news_panel_html(news_items: List[Dict[str, str]], limit: int = 6) -> str:
        return dashboard_rendering_service._render_market_news_panel_html(news_items, limit=limit)

    def render_lot_monitor_cards(entries: List[Dict[str, Any]], page_name: str, empty_message: str, default_alert_phone: str) -> str:
        return lot_monitoring_service._render_lot_monitor_cards(entries, page_name, empty_message, default_alert_phone)

    def render_recent_operations_rows(transactions: List[Dict[str, Any]], empty_message: str = "Nenhuma operação recente.") -> str:
        return dashboard_rendering_service._render_recent_operations_rows(transactions, empty_message=empty_message)

    def render_open_fechamentos_rows(
        transactions: List[Dict[str, Any]],
        limit: int = 8,
        empty_message: str = "Nenhum fechamento parcial em aberto nos movimentos recentes.",
    ) -> str:
        return dashboard_rendering_service._render_open_fechamentos_rows(transactions, collect_open_fechamentos=collect_open_fechamentos, limit=limit, empty_message=empty_message)

    def render_dashboard_pending_closings_html(transactions: List[Dict[str, Any]]) -> str:
        return dashboard_rendering_service._render_dashboard_pending_closings_html(transactions, collect_open_fechamentos=collect_open_fechamentos)

    def render_dashboard_recent_operations_html(transactions: List[Dict[str, Any]]) -> str:
        return dashboard_rendering_service._render_dashboard_recent_operations_html(transactions)

    def render_dashboard_inventory_html(inventory: Dict[str, Any], lot_market_context: Dict[str, Any]) -> str:
        return dashboard_rendering_service._render_dashboard_inventory_html(inventory, lot_market_context)

    def render_dashboard_trend_html(transactions: List[Dict[str, Any]]) -> str:
        return dashboard_trends_service._render_dashboard_trend_html(transactions)

    def render_dashboard_summary_html(summary: Dict[str, Any], gross_grams_today: Any, ouro_proprio: Any) -> str:
        return dashboard_rendering_service._render_dashboard_summary_html(summary, gross_grams_today, ouro_proprio, format_caixa_movement=format_caixa_movement)

    def build_web_lot_ai_alerts(lot_market_context: Dict[str, Any], market_trend: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
        return lot_monitoring_service._build_web_lot_ai_alerts(lot_market_context, market_trend, build_lot_sell_signal=build_lot_sell_signal, format_lot_signal_status=format_lot_signal_status, limit=limit)

    def build_web_lot_monitor_view_model(
        lot_market_context: Dict[str, Any],
        market_trend: Dict[str, Any],
        default_alert_phone: str = "",
        entry_limit: int = 8,
        alert_limit: int = 4,
    ) -> Dict[str, Any]:
        return lot_monitoring_service._build_web_lot_monitor_view_model(
            lot_market_context,
            market_trend,
            build_lot_sell_signal=build_lot_sell_signal,
            format_lot_signal_status=format_lot_signal_status,
            build_alert_summary=build_web_lot_ai_alert_summary,
            default_alert_phone=default_alert_phone,
            entry_limit=entry_limit,
            alert_limit=alert_limit,
        )

    def build_web_lot_monitor_entries(
        lot_market_context: Dict[str, Any],
        market_trend: Dict[str, Any],
        default_alert_phone: str = "",
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        return lot_monitoring_service._build_web_lot_monitor_entries(lot_market_context, market_trend, build_lot_sell_signal=build_lot_sell_signal, format_lot_signal_status=format_lot_signal_status, default_alert_phone=default_alert_phone, limit=limit)

    def normalize_whatsapp_to(raw_phone: str) -> str:
        return lot_monitoring_service._normalize_whatsapp_to(raw_phone, normalize_user_phone=normalize_user_phone)

    def send_outbound_whatsapp_alert(phone: str, message: str) -> bool:
        return lot_monitoring_service._send_outbound_whatsapp_alert(phone, message, normalize_whatsapp_to=normalize_whatsapp_to, logger=logger)

    def run_lot_monitor_cycle() -> None:
        lot_monitoring_service._run_lot_monitor_cycle(
            get_db=get_db,
            get_market_snapshot=get_market_snapshot,
            build_market_trend_context=build_market_trend_context,
            build_open_lot_market_context=build_open_lot_market_context,
            build_lot_sell_signal=build_lot_sell_signal,
            extract_lot_monitor_config=extract_lot_monitor_config,
            build_lot_alert_message=build_lot_alert_message,
            send_outbound_whatsapp_alert=send_outbound_whatsapp_alert,
            logger=logger,
        )

    def lot_monitor_worker() -> None:
        lot_monitoring_service._lot_monitor_worker(stop_event=stop_event, interval_seconds=lot_monitor_interval_seconds, run_lot_monitor_cycle=run_lot_monitor_cycle, logger=logger)

    def warm_web_runtime_caches() -> None:
        market_service._warm_web_runtime_caches(get_market_snapshot=get_market_snapshot, get_market_news=get_market_news)

    return SimpleNamespace(
        render_market_news_panel_html=render_market_news_panel_html,
        render_lot_monitor_cards=render_lot_monitor_cards,
        render_recent_operations_rows=render_recent_operations_rows,
        render_open_fechamentos_rows=render_open_fechamentos_rows,
        render_dashboard_pending_closings_html=render_dashboard_pending_closings_html,
        render_dashboard_recent_operations_html=render_dashboard_recent_operations_html,
        render_dashboard_inventory_html=render_dashboard_inventory_html,
        render_dashboard_trend_html=render_dashboard_trend_html,
        render_dashboard_summary_html=render_dashboard_summary_html,
        build_web_lot_ai_alerts=build_web_lot_ai_alerts,
        build_web_lot_monitor_view_model=build_web_lot_monitor_view_model,
        build_web_lot_monitor_entries=build_web_lot_monitor_entries,
        normalize_whatsapp_to=normalize_whatsapp_to,
        send_outbound_whatsapp_alert=send_outbound_whatsapp_alert,
        run_lot_monitor_cycle=run_lot_monitor_cycle,
        lot_monitor_worker=lot_monitor_worker,
        warm_web_runtime_caches=warm_web_runtime_caches,
    )