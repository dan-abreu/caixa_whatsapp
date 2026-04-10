from types import SimpleNamespace
from typing import Any, Callable, Dict


def build_reporting_runtime_helpers(
    *,
    reporting_service: Any,
    get_cached_payload: Callable[[], Dict[str, Any] | None],
    set_cached_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    get_market_snapshot: Callable[[], Dict[str, Any]],
    build_open_lot_market_context: Callable[..., Dict[str, Any]],
    compute_inventory_metrics: Callable[..., Dict[str, Any]],
    build_fifo_inventory_lots: Callable[..., Any],
    build_day_range: Callable[..., Dict[str, str]],
    build_cache_key: Callable[[str], str],
    get_cached_html: Callable[[str], str | None],
    set_cached_html: Callable[[str, str], str],
    format_caixa_movement: Callable[..., str],
) -> SimpleNamespace:
    def build_inventory_status_report_payload(db: Any) -> Dict[str, Any]:
        return reporting_service._build_inventory_status_report_payload(
            db,
            get_cached_payload=get_cached_payload,
            set_cached_payload=set_cached_payload,
            get_market_snapshot=get_market_snapshot,
            build_open_lot_market_context=build_open_lot_market_context,
            compute_inventory_metrics=compute_inventory_metrics,
            build_fifo_inventory_lots=build_fifo_inventory_lots,
        )

    def build_admin_dashboard_html(db: Any) -> str:
        return reporting_service._build_admin_dashboard_html(
            db,
            build_day_range=build_day_range,
            build_cache_key=build_cache_key,
            get_cached_html=get_cached_html,
            set_cached_html=set_cached_html,
            compute_inventory_metrics=compute_inventory_metrics,
            build_fifo_inventory_lots=build_fifo_inventory_lots,
            format_caixa_movement=format_caixa_movement,
        )

    return SimpleNamespace(
        build_inventory_status_report_payload=build_inventory_status_report_payload,
        build_admin_dashboard_html=build_admin_dashboard_html,
    )
