from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, cast


def _build_saas_statement_context_cache_key(cache_key_prefix: str, start_iso: str, end_iso: str) -> str:
    return f"{cache_key_prefix}:{start_iso}:{end_iso}"


def _get_saas_statement_context_cached(
    cache_key: str,
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    if ttl_seconds <= 0:
        return None

    now = datetime.now(timezone.utc)
    cache_entry = cache_store.get(cache_key)
    if isinstance(cache_entry, dict):
        expires_at = cache_entry.get("expires_at")
        data = cache_entry.get("data")
        if isinstance(expires_at, datetime) and isinstance(data, dict) and expires_at > now:
            return cast(Dict[str, Any], data)
        cache_store.pop(cache_key, None)
    return None


def _set_saas_statement_context_cached(
    cache_key: str,
    context: Dict[str, Any],
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: int,
) -> Dict[str, Any]:
    if ttl_seconds <= 0:
        return context

    cache_store[cache_key] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        "data": context,
    }
    return context


def _invalidate_statement_context_cache(*, cache_store: Dict[str, Dict[str, Any]]) -> None:
    cache_store.clear()


def _get_saas_recent_fx_cached(*, cache_store: Dict[str, Any], ttl_seconds: int) -> Optional[Dict[str, str]]:
    if ttl_seconds <= 0:
        return None

    expires_at = cache_store.get("expires_at")
    data = cache_store.get("data")
    if isinstance(expires_at, datetime) and isinstance(data, dict) and expires_at > datetime.now(timezone.utc):
        return cast(Dict[str, str], data)
    cache_store["expires_at"] = None
    cache_store["data"] = None
    return None


def _set_saas_recent_fx_cached(snapshot: Dict[str, str], *, cache_store: Dict[str, Any], ttl_seconds: int) -> Dict[str, str]:
    if ttl_seconds <= 0:
        return snapshot

    cache_store["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    cache_store["data"] = dict(snapshot)
    return snapshot


def _invalidate_recent_fx_map_cache(*, cache_store: Dict[str, Any]) -> None:
    cache_store["expires_at"] = None
    cache_store["data"] = None


def _build_saas_receipt_context_cache_key(cache_key_prefix: str, operation_id: int) -> str:
    return f"{cache_key_prefix}:{int(operation_id)}"


def _get_saas_receipt_context_cached(
    cache_key: str,
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    if ttl_seconds <= 0:
        return None

    now = datetime.now(timezone.utc)
    cache_entry = cache_store.get(cache_key)
    if isinstance(cache_entry, dict):
        expires_at = cache_entry.get("expires_at")
        data = cache_entry.get("data")
        if isinstance(expires_at, datetime) and isinstance(data, dict) and expires_at > now:
            return cast(Dict[str, Any], data)
        cache_store.pop(cache_key, None)
    return None


def _set_saas_receipt_context_cached(
    cache_key: str,
    context: Dict[str, Any],
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: int,
) -> Dict[str, Any]:
    if ttl_seconds <= 0:
        return context

    cache_store[cache_key] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        "data": context,
    }
    return context


def _invalidate_receipt_context_cache(*, cache_store: Dict[str, Dict[str, Any]]) -> None:
    cache_store.clear()


def _build_saas_lot_monitor_snapshot_cache_key(
    phone: str,
    *,
    cache_key_prefix: str,
    normalize_phone: Callable[[str], str],
) -> str:
    normalized_phone = normalize_phone(phone) or "default"
    return f"{cache_key_prefix}:{normalized_phone}"


def _get_saas_lot_monitor_snapshot_cached(
    cache_key: str,
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: float,
) -> Optional[Dict[str, Any]]:
    if ttl_seconds <= 0:
        return None

    now = datetime.now(timezone.utc)
    cache_entry = cache_store.get(cache_key)
    if isinstance(cache_entry, dict):
        expires_at = cache_entry.get("expires_at")
        data = cache_entry.get("data")
        if isinstance(expires_at, datetime) and isinstance(data, dict) and expires_at > now:
            return cast(Dict[str, Any], data)
        cache_store.pop(cache_key, None)
    return None


def _set_saas_lot_monitor_snapshot_cached(
    cache_key: str,
    payload: Dict[str, Any],
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: float,
) -> Dict[str, Any]:
    if ttl_seconds <= 0:
        return payload

    cache_store[cache_key] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        "data": payload,
    }
    return payload


def _invalidate_lot_monitor_snapshot_cache(*, cache_store: Dict[str, Dict[str, Any]]) -> None:
    cache_store.clear()


def _build_admin_dashboard_cache_key(cache_key_prefix: str, day_label: str) -> str:
    return f"{cache_key_prefix}:{day_label}"


def _get_inventory_status_report_cached(*, cache_store: Dict[str, Any], ttl_seconds: int) -> Optional[Dict[str, Any]]:
    if ttl_seconds <= 0:
        return None

    expires_at = cache_store.get("expires_at")
    data = cache_store.get("data")
    if isinstance(expires_at, datetime) and isinstance(data, dict) and expires_at > datetime.now(timezone.utc):
        return cast(Dict[str, Any], data)
    cache_store["expires_at"] = None
    cache_store["data"] = None
    return None


def _set_inventory_status_report_cached(payload: Dict[str, Any], *, cache_store: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
    if ttl_seconds <= 0:
        return payload

    cache_store["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    cache_store["data"] = payload
    return payload


def _get_admin_dashboard_cached(cache_key: str, *, cache_store: Dict[str, Dict[str, Any]], ttl_seconds: int) -> Optional[str]:
    if ttl_seconds <= 0:
        return None

    cache_entry = cache_store.get(cache_key)
    if isinstance(cache_entry, dict):
        expires_at = cache_entry.get("expires_at")
        data = cache_entry.get("data")
        if isinstance(expires_at, datetime) and isinstance(data, str) and expires_at > datetime.now(timezone.utc):
            return data
        cache_store.pop(cache_key, None)
    return None


def _set_admin_dashboard_cached(
    cache_key: str,
    html: str,
    *,
    cache_store: Dict[str, Dict[str, Any]],
    ttl_seconds: int,
) -> str:
    if ttl_seconds <= 0:
        return html

    cache_store[cache_key] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        "data": html,
    }
    return html


def _invalidate_reporting_cache(*, inventory_status_cache: Dict[str, Any], admin_dashboard_cache: Dict[str, Dict[str, Any]]) -> None:
    inventory_status_cache["expires_at"] = None
    inventory_status_cache["data"] = None
    admin_dashboard_cache.clear()