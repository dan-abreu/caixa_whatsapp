import asyncio
from typing import Any, Callable, Dict, List, cast


def _build_lot_monitor_snapshot_payload(
    db: Any,
    session_user: Dict[str, Any],
    *,
    build_snapshot_cache_key: Callable[[str], str],
    get_snapshot_cached: Callable[[str], Dict[str, Any] | None],
    set_snapshot_cached: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    get_market_snapshot: Callable[[], Dict[str, str]],
    build_market_trend_context: Callable[[], Dict[str, Any]],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, str]], Dict[str, Any]],
    build_web_lot_monitor_view_model: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    cache_key = build_snapshot_cache_key(str(session_user.get("telefone") or ""))
    cached_payload = get_snapshot_cached(cache_key)
    if cached_payload is not None:
        return cached_payload

    inventory = db.get_gold_inventory_status(open_only=True)
    if not inventory.get("has_any_lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status(open_only=True)
    market_snapshot = get_market_snapshot()
    market_trend = build_market_trend_context()
    lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)
    lot_monitor_model = build_web_lot_monitor_view_model(
        lot_market_context,
        market_trend,
        default_alert_phone=str(session_user.get("telefone") or ""),
        entry_limit=24,
        alert_limit=4,
    )
    payload = {
        "ok": True,
        "summary": str(lot_monitor_model.get("summary") or ""),
        "alerts": cast(List[Dict[str, Any]], lot_monitor_model.get("alerts") or []),
        "lots": cast(List[Dict[str, Any]], lot_monitor_model.get("entries") or []),
        "updated_at_label": str(market_snapshot.get("updated_at_label") or ""),
    }
    return set_snapshot_cached(cache_key, payload)


async def _lot_monitor_stream_events(
    request: Any,
    session_user: Dict[str, Any],
    db: Any,
    *,
    build_lot_monitor_snapshot_payload: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
    build_sse_message: Callable[[Dict[str, Any]], str],
    stream_interval_seconds: float,
):
    while True:
        if await request.is_disconnected():
            break
        payload = build_lot_monitor_snapshot_payload(db, session_user)
        yield build_sse_message(payload)
        await asyncio.sleep(stream_interval_seconds)