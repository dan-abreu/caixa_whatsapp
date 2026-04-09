import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import Response


def _build_dashboard_fragment_cache_key(fragment_name: str, cache_key_prefix: str, scope: str = "global") -> str:
    normalized_scope = re.sub(r"[^a-zA-Z0-9:_+-]+", "_", str(scope or "global")).strip("_") or "global"
    return f"{cache_key_prefix}:{fragment_name}:{normalized_scope}"


def _get_dashboard_fragment_cached_html(
    cache_key: str,
    *,
    dashboard_fragment_cache: Dict[str, Dict[str, Any]],
    dashboard_fragment_cache_ttl_seconds: int,
    get_shared_cache_backend: Callable[[], Any],
    use_shared: bool = True,
) -> Optional[str]:
    if dashboard_fragment_cache_ttl_seconds <= 0:
        return None

    now = datetime.now(timezone.utc)
    local_entry = dashboard_fragment_cache.get(cache_key)
    if isinstance(local_entry, dict):
        expires_at = local_entry.get("expires_at")
        html = local_entry.get("html")
        if isinstance(expires_at, datetime) and isinstance(html, str) and expires_at > now:
            return html
        dashboard_fragment_cache.pop(cache_key, None)

    if use_shared:
        shared_cache = get_shared_cache_backend()
        if shared_cache is not None:
            shared_html = shared_cache.get_json(cache_key)
            if isinstance(shared_html, str) and shared_html:
                dashboard_fragment_cache[cache_key] = {
                    "expires_at": now + timedelta(seconds=dashboard_fragment_cache_ttl_seconds),
                    "html": shared_html,
                }
                return shared_html
    return None


def _set_dashboard_fragment_cached_html(
    cache_key: str,
    html: str,
    *,
    dashboard_fragment_cache: Dict[str, Dict[str, Any]],
    dashboard_fragment_cache_ttl_seconds: int,
    get_shared_cache_backend: Callable[[], Any],
    use_shared: bool = True,
) -> None:
    if dashboard_fragment_cache_ttl_seconds <= 0 or not html:
        return

    dashboard_fragment_cache[cache_key] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=dashboard_fragment_cache_ttl_seconds),
        "html": html,
    }
    if use_shared:
        shared_cache = get_shared_cache_backend()
        if shared_cache is not None:
            shared_cache.set_json(cache_key, html, dashboard_fragment_cache_ttl_seconds)


def _render_cached_dashboard_fragment(
    cache_key: str,
    render_html: Callable[[], str],
    *,
    get_cached_html: Callable[..., Optional[str]],
    set_cached_html: Callable[..., None],
    use_shared: bool = True,
) -> Response:
    cached_html = get_cached_html(cache_key, use_shared=use_shared)
    if cached_html is not None:
        return Response(content=cached_html, media_type="text/html")

    html = render_html()
    set_cached_html(cache_key, html, use_shared=use_shared)
    return Response(content=html, media_type="text/html")


def _invalidate_dashboard_fragment_cache_keys(
    *cache_keys: str,
    dashboard_fragment_cache: Dict[str, Dict[str, Any]],
    get_shared_cache_backend: Callable[[], Any],
) -> None:
    unique_keys = [key for key in dict.fromkeys(key for key in cache_keys if key)]
    if not unique_keys:
        return

    for cache_key in unique_keys:
        dashboard_fragment_cache.pop(cache_key, None)

    shared_cache = get_shared_cache_backend()
    if shared_cache is not None:
        shared_cache.delete(*unique_keys)


def _invalidate_dashboard_monitors_fragment_cache(
    *,
    dashboard_fragment_cache: Dict[str, Dict[str, Any]],
    dashboard_fragment_cache_key_prefix: str,
    dashboard_fragment_monitors_name: str,
) -> None:
    prefix = f"{dashboard_fragment_cache_key_prefix}:{dashboard_fragment_monitors_name}:"
    for cache_key in [key for key in dashboard_fragment_cache if key.startswith(prefix)]:
        dashboard_fragment_cache.pop(cache_key, None)


def _invalidate_dashboard_operation_fragments(
    *,
    build_dashboard_fragment_cache_key: Callable[[str], str],
    invalidate_dashboard_fragment_cache_keys: Callable[..., None],
    invalidate_dashboard_monitors_fragment_cache: Callable[[], None],
    dashboard_fragment_inventory_name: str,
    dashboard_fragment_trend_name: str,
    dashboard_fragment_summary_name: str,
    dashboard_fragment_pending_closings_name: str,
    dashboard_fragment_recent_operations_name: str,
) -> None:
    invalidate_dashboard_fragment_cache_keys(
        build_dashboard_fragment_cache_key(dashboard_fragment_inventory_name),
        build_dashboard_fragment_cache_key(dashboard_fragment_trend_name),
        build_dashboard_fragment_cache_key(dashboard_fragment_summary_name),
        build_dashboard_fragment_cache_key(dashboard_fragment_pending_closings_name),
        build_dashboard_fragment_cache_key(dashboard_fragment_recent_operations_name),
    )
    invalidate_dashboard_monitors_fragment_cache()