from types import SimpleNamespace
from typing import Any, Callable, Dict


def build_runtime_view_helpers(
    *,
    dashboard_fragments_service: Any,
    view_caches_service: Any,
    get_shared_cache_backend: Callable[[], Any],
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
    normalize_user_phone: Callable[[str], str],
) -> SimpleNamespace:
    def build_dashboard_fragment_cache_key(fragment_name: str, scope: str = "global") -> str:
        return dashboard_fragments_service._build_dashboard_fragment_cache_key(fragment_name, dashboard_fragment_cache_key_prefix, scope)

    def get_dashboard_fragment_cached_html(cache_key: str, *, use_shared: bool = True) -> Any:
        return dashboard_fragments_service._get_dashboard_fragment_cached_html(
            cache_key,
            dashboard_fragment_cache=dashboard_fragment_cache,
            dashboard_fragment_cache_ttl_seconds=dashboard_fragment_cache_ttl_seconds,
            get_shared_cache_backend=get_shared_cache_backend,
            use_shared=use_shared,
        )

    def set_dashboard_fragment_cached_html(cache_key: str, html: str, *, use_shared: bool = True) -> None:
        dashboard_fragments_service._set_dashboard_fragment_cached_html(
            cache_key,
            html,
            dashboard_fragment_cache=dashboard_fragment_cache,
            dashboard_fragment_cache_ttl_seconds=dashboard_fragment_cache_ttl_seconds,
            get_shared_cache_backend=get_shared_cache_backend,
            use_shared=use_shared,
        )

    def render_cached_dashboard_fragment(cache_key: str, render_html: Callable[[], str], *, use_shared: bool = True) -> Any:
        return dashboard_fragments_service._render_cached_dashboard_fragment(
            cache_key,
            render_html,
            get_cached_html=get_dashboard_fragment_cached_html,
            set_cached_html=set_dashboard_fragment_cached_html,
            use_shared=use_shared,
        )

    def invalidate_dashboard_fragment_cache_keys(*cache_keys: str) -> None:
        dashboard_fragments_service._invalidate_dashboard_fragment_cache_keys(
            *cache_keys,
            dashboard_fragment_cache=dashboard_fragment_cache,
            get_shared_cache_backend=get_shared_cache_backend,
        )

    def invalidate_dashboard_monitors_fragment_cache() -> None:
        dashboard_fragments_service._invalidate_dashboard_monitors_fragment_cache(
            dashboard_fragment_cache=dashboard_fragment_cache,
            dashboard_fragment_cache_key_prefix=dashboard_fragment_cache_key_prefix,
            dashboard_fragment_monitors_name=dashboard_fragment_monitors_name,
        )

    def invalidate_dashboard_operation_fragments() -> None:
        dashboard_fragments_service._invalidate_dashboard_operation_fragments(
            build_dashboard_fragment_cache_key=build_dashboard_fragment_cache_key,
            invalidate_dashboard_fragment_cache_keys=invalidate_dashboard_fragment_cache_keys,
            invalidate_dashboard_monitors_fragment_cache=invalidate_dashboard_monitors_fragment_cache,
            dashboard_fragment_inventory_name=dashboard_fragment_inventory_name,
            dashboard_fragment_trend_name=dashboard_fragment_trend_name,
            dashboard_fragment_summary_name=dashboard_fragment_summary_name,
            dashboard_fragment_pending_closings_name=dashboard_fragment_pending_closings_name,
            dashboard_fragment_recent_operations_name=dashboard_fragment_recent_operations_name,
        )

    def build_saas_statement_context_cache_key(start_iso: str, end_iso: str) -> str:
        return view_caches_service._build_saas_statement_context_cache_key(saas_statement_context_cache_key_prefix, start_iso, end_iso)

    def get_saas_statement_context_cached(cache_key: str) -> Any:
        return view_caches_service._get_saas_statement_context_cached(cache_key, cache_store=saas_statement_context_cache, ttl_seconds=saas_statement_context_cache_ttl_seconds)

    def set_saas_statement_context_cached(cache_key: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return view_caches_service._set_saas_statement_context_cached(cache_key, context, cache_store=saas_statement_context_cache, ttl_seconds=saas_statement_context_cache_ttl_seconds)

    def invalidate_statement_context_cache() -> None:
        view_caches_service._invalidate_statement_context_cache(
            cache_store=saas_statement_context_cache,
            cache_key_prefix=saas_statement_context_cache_key_prefix,
        )

    def get_saas_recent_fx_cached() -> Any:
        return view_caches_service._get_saas_recent_fx_cached(cache_store=saas_recent_fx_cache, ttl_seconds=saas_recent_fx_cache_ttl_seconds)

    def set_saas_recent_fx_cached(snapshot: Dict[str, str]) -> Dict[str, str]:
        return view_caches_service._set_saas_recent_fx_cached(snapshot, cache_store=saas_recent_fx_cache, ttl_seconds=saas_recent_fx_cache_ttl_seconds)

    def invalidate_recent_fx_map_cache() -> None:
        view_caches_service._invalidate_recent_fx_map_cache(cache_store=saas_recent_fx_cache)

    def build_saas_receipt_context_cache_key(operation_id: int) -> str:
        return view_caches_service._build_saas_receipt_context_cache_key(saas_receipt_context_cache_key_prefix, operation_id)

    def get_saas_receipt_context_cached(cache_key: str) -> Any:
        return view_caches_service._get_saas_receipt_context_cached(cache_key, cache_store=saas_receipt_context_cache, ttl_seconds=saas_receipt_context_cache_ttl_seconds)

    def set_saas_receipt_context_cached(cache_key: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return view_caches_service._set_saas_receipt_context_cached(cache_key, context, cache_store=saas_receipt_context_cache, ttl_seconds=saas_receipt_context_cache_ttl_seconds)

    def invalidate_receipt_context_cache() -> None:
        view_caches_service._invalidate_receipt_context_cache(
            cache_store=saas_receipt_context_cache,
            cache_key_prefix=saas_receipt_context_cache_key_prefix,
        )

    def build_saas_lot_monitor_snapshot_cache_key(phone: str) -> str:
        return view_caches_service._build_saas_lot_monitor_snapshot_cache_key(phone, cache_key_prefix=saas_lot_monitor_snapshot_cache_key_prefix, normalize_phone=normalize_user_phone)

    def get_saas_lot_monitor_snapshot_cached(cache_key: str) -> Any:
        return view_caches_service._get_saas_lot_monitor_snapshot_cached(cache_key, cache_store=saas_lot_monitor_snapshot_cache, ttl_seconds=saas_lot_monitor_snapshot_cache_ttl_seconds)

    def set_saas_lot_monitor_snapshot_cached(cache_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return view_caches_service._set_saas_lot_monitor_snapshot_cached(cache_key, payload, cache_store=saas_lot_monitor_snapshot_cache, ttl_seconds=saas_lot_monitor_snapshot_cache_ttl_seconds)

    def invalidate_lot_monitor_snapshot_cache() -> None:
        view_caches_service._invalidate_lot_monitor_snapshot_cache(
            cache_store=saas_lot_monitor_snapshot_cache,
            cache_key_prefix=saas_lot_monitor_snapshot_cache_key_prefix,
        )

    def build_admin_dashboard_cache_key(day_label: str) -> str:
        return view_caches_service._build_admin_dashboard_cache_key(admin_dashboard_cache_key_prefix, day_label)

    def get_inventory_status_report_cached() -> Any:
        return view_caches_service._get_inventory_status_report_cached(cache_store=inventory_status_cache, ttl_seconds=inventory_status_cache_ttl_seconds)

    def set_inventory_status_report_cached(payload: Dict[str, Any]) -> Dict[str, Any]:
        return view_caches_service._set_inventory_status_report_cached(payload, cache_store=inventory_status_cache, ttl_seconds=inventory_status_cache_ttl_seconds)

    def get_admin_dashboard_cached(cache_key: str) -> Any:
        return view_caches_service._get_admin_dashboard_cached(cache_key, cache_store=admin_dashboard_cache, ttl_seconds=admin_dashboard_cache_ttl_seconds)

    def set_admin_dashboard_cached(cache_key: str, html: str) -> str:
        return view_caches_service._set_admin_dashboard_cached(cache_key, html, cache_store=admin_dashboard_cache, ttl_seconds=admin_dashboard_cache_ttl_seconds)

    def invalidate_reporting_cache() -> None:
        view_caches_service._invalidate_reporting_cache(
            inventory_status_cache=inventory_status_cache,
            admin_dashboard_cache=admin_dashboard_cache,
            admin_dashboard_cache_key_prefix=admin_dashboard_cache_key_prefix,
        )

    def invalidate_operation_related_view_caches() -> None:
        invalidate_dashboard_operation_fragments()
        invalidate_statement_context_cache()
        invalidate_recent_fx_map_cache()
        invalidate_receipt_context_cache()
        invalidate_lot_monitor_snapshot_cache()
        invalidate_reporting_cache()

    return SimpleNamespace(
        build_dashboard_fragment_cache_key=build_dashboard_fragment_cache_key,
        get_dashboard_fragment_cached_html=get_dashboard_fragment_cached_html,
        set_dashboard_fragment_cached_html=set_dashboard_fragment_cached_html,
        render_cached_dashboard_fragment=render_cached_dashboard_fragment,
        invalidate_dashboard_fragment_cache_keys=invalidate_dashboard_fragment_cache_keys,
        invalidate_dashboard_monitors_fragment_cache=invalidate_dashboard_monitors_fragment_cache,
        invalidate_dashboard_operation_fragments=invalidate_dashboard_operation_fragments,
        build_saas_statement_context_cache_key=build_saas_statement_context_cache_key,
        get_saas_statement_context_cached=get_saas_statement_context_cached,
        set_saas_statement_context_cached=set_saas_statement_context_cached,
        invalidate_statement_context_cache=invalidate_statement_context_cache,
        get_saas_recent_fx_cached=get_saas_recent_fx_cached,
        set_saas_recent_fx_cached=set_saas_recent_fx_cached,
        invalidate_recent_fx_map_cache=invalidate_recent_fx_map_cache,
        build_saas_receipt_context_cache_key=build_saas_receipt_context_cache_key,
        get_saas_receipt_context_cached=get_saas_receipt_context_cached,
        set_saas_receipt_context_cached=set_saas_receipt_context_cached,
        invalidate_receipt_context_cache=invalidate_receipt_context_cache,
        build_saas_lot_monitor_snapshot_cache_key=build_saas_lot_monitor_snapshot_cache_key,
        get_saas_lot_monitor_snapshot_cached=get_saas_lot_monitor_snapshot_cached,
        set_saas_lot_monitor_snapshot_cached=set_saas_lot_monitor_snapshot_cached,
        invalidate_lot_monitor_snapshot_cache=invalidate_lot_monitor_snapshot_cache,
        build_admin_dashboard_cache_key=build_admin_dashboard_cache_key,
        get_inventory_status_report_cached=get_inventory_status_report_cached,
        set_inventory_status_report_cached=set_inventory_status_report_cached,
        get_admin_dashboard_cached=get_admin_dashboard_cached,
        set_admin_dashboard_cached=set_admin_dashboard_cached,
        invalidate_reporting_cache=invalidate_reporting_cache,
        invalidate_operation_related_view_caches=invalidate_operation_related_view_caches,
    )