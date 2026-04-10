from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, cast

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse


def register_dashboard_runtime_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    build_inventory_status_report_payload: Callable[[Any], Dict[str, Any]],
    get_market_snapshot: Callable[[], Dict[str, Any]],
    market_cache_ttl_seconds: int,
    market_stream_events: Callable[[Request], Any],
    get_market_news: Callable[[], List[Dict[str, str]]],
    build_dashboard_fragment_cache_key: Callable[..., str],
    dashboard_fragment_news_name: str,
    dashboard_fragment_monitors_name: str,
    dashboard_fragment_inventory_name: str,
    dashboard_fragment_trend_name: str,
    dashboard_fragment_summary_name: str,
    dashboard_fragment_pending_closings_name: str,
    dashboard_fragment_recent_operations_name: str,
    render_cached_dashboard_fragment: Callable[..., Response],
    render_market_news_panel_html: Callable[[List[Dict[str, str]], int], str],
    normalize_user_phone: Callable[[str], str],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    build_market_trend_context: Callable[[], Dict[str, Any]],
    build_web_lot_monitor_view_model: Callable[..., Dict[str, Any]],
    render_lot_monitor_cards: Callable[[List[Dict[str, Any]], str, str, str], str],
    render_dashboard_inventory_html: Callable[[Dict[str, Any], Dict[str, Any]], str],
    render_dashboard_trend_html: Callable[[List[Dict[str, Any]]], str],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_statement_summary: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    build_gold_caixa_metrics_from_pending_grams: Callable[[Decimal, Decimal], Dict[str, Decimal]],
    render_dashboard_summary_html: Callable[[Dict[str, Any], Decimal, Decimal], str],
    build_week_range: Callable[[], Dict[str, str]],
    render_dashboard_pending_closings_html: Callable[[List[Dict[str, Any]]], str],
    render_dashboard_recent_operations_html: Callable[[List[Dict[str, Any]]], str],
    build_lot_monitor_snapshot_payload: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
    lot_monitor_stream_events: Callable[[Request, Dict[str, Any], Any], Any],
    request_form_dict: Callable[[Request], Any],
    parse_decimal_web_field: Callable[[str, str], Decimal],
    invalidate_dashboard_monitors_fragment_cache: Callable[[], None],
    invalidate_lot_monitor_snapshot_cache: Callable[[], None],
    render_saas_dashboard_html: Callable[..., str],
    build_admin_dashboard_html: Callable[[Any], str],
    render_saas_login_html: Callable[..., str],
    validate_webhook_token: Callable[[Optional[str]], None],
) -> None:
    @app.get("/reports/inventory-status")
    def inventory_status_report() -> Dict[str, Any]:
        return build_inventory_status_report_payload(get_db())

    @app.get("/saas/market-snapshot")
    def saas_market_snapshot(request: Request) -> Dict[str, Any]:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            raise HTTPException(status_code=401, detail="Sessao expirada")
        return {"ok": True, "snapshot": get_market_snapshot(), "cache_ttl_seconds": market_cache_ttl_seconds}

    @app.get("/saas/market-stream")
    async def saas_market_stream(request: Request) -> StreamingResponse:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            raise HTTPException(status_code=401, detail="Sessao expirada")
        return StreamingResponse(market_stream_events(request), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    @app.get("/saas/market-news")
    def saas_market_news(request: Request) -> Dict[str, Any]:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            raise HTTPException(status_code=401, detail="Sessao expirada")
        return {"ok": True, "items": get_market_news()}

    @app.get("/saas/fragments/dashboard-news")
    def saas_dashboard_news_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_news_name)
        return render_cached_dashboard_fragment(cache_key, lambda: render_market_news_panel_html(get_market_news(), limit=3))

    @app.get("/saas/fragments/dashboard-monitors")
    def saas_dashboard_monitors_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        default_alert_phone = normalize_user_phone(str(session_user.get("telefone") or ""))
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_monitors_name, scope=default_alert_phone or "default")

        def _render_fragment() -> str:
            inventory = db.get_gold_inventory_status(open_only=True)
            if not inventory.get("has_any_lots"):
                db.sync_gold_inventory_ledger()
                inventory = db.get_gold_inventory_status(open_only=True)
            lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), get_market_snapshot())
            market_trend = build_market_trend_context()
            entries = cast(List[Dict[str, Any]], build_web_lot_monitor_view_model(lot_market_context, market_trend, default_alert_phone=default_alert_phone, entry_limit=24, alert_limit=0).get("entries") or [])
            enabled_entries = [item for item in entries if item.get("enabled")]
            return render_lot_monitor_cards(enabled_entries, "dashboard", "Nenhum lote foi selecionado para monitoramento no dashboard.", default_alert_phone)

        return render_cached_dashboard_fragment(cache_key, _render_fragment, use_shared=False)

    @app.get("/saas/fragments/dashboard-inventory")
    def saas_dashboard_inventory_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_inventory_name)

        def _render_fragment() -> str:
            inventory = db.get_gold_inventory_status(open_only=True)
            if not inventory.get("has_any_lots"):
                db.sync_gold_inventory_ledger()
                inventory = db.get_gold_inventory_status(open_only=True)
            lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), get_market_snapshot())
            return render_dashboard_inventory_html(inventory, lot_market_context)

        return render_cached_dashboard_fragment(cache_key, _render_fragment)

    @app.get("/saas/fragments/dashboard-trend")
    def saas_dashboard_trend_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_trend_name)
        return render_cached_dashboard_fragment(cache_key, lambda: render_dashboard_trend_html(db.get_gold_inventory_transactions()))

    @app.get("/saas/fragments/dashboard-summary")
    def saas_dashboard_summary_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_summary_name)

        def _render_fragment() -> str:
            day = build_day_range(None)
            day_transactions = db.get_extrato_transactions(day["start"], day["end"])
            summary = build_statement_summary(day_transactions)
            gross_grams_today = sum((Decimal(str(item.get("peso") or "0")) for item in day_transactions), Decimal("0"))
            saldo = db.get_saldo_caixa()
            gold_caixa_metrics = build_gold_caixa_metrics_from_pending_grams(Decimal(str(saldo.get("XAU", "0"))), db.get_gold_pending_closure_grams())
            return render_dashboard_summary_html(summary, gross_grams_today, gold_caixa_metrics["ouro_proprio"])

        return render_cached_dashboard_fragment(cache_key, _render_fragment)

    @app.get("/saas/fragments/dashboard-pending-closings")
    def saas_dashboard_pending_closings_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_pending_closings_name)

        def _render_fragment() -> str:
            week = build_week_range()
            return render_dashboard_pending_closings_html(db.get_extrato_transactions(week["start"], week["end"]))

        return render_cached_dashboard_fragment(cache_key, _render_fragment)

    @app.get("/saas/fragments/dashboard-recent-operations")
    def saas_dashboard_recent_operations_fragment(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
        cache_key = build_dashboard_fragment_cache_key(dashboard_fragment_recent_operations_name)

        def _render_fragment() -> str:
            week = build_week_range()
            return render_dashboard_recent_operations_html(db.get_extrato_transactions(week["start"], week["end"])[-12:])

        return render_cached_dashboard_fragment(cache_key, _render_fragment)

    @app.get("/saas/lot-monitor-snapshot")
    def saas_lot_monitor_snapshot(request: Request) -> Dict[str, Any]:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            raise HTTPException(status_code=401, detail="Sessao expirada")
        return build_lot_monitor_snapshot_payload(db, session_user)

    @app.get("/saas/lot-monitor-stream")
    async def saas_lot_monitor_stream(request: Request) -> StreamingResponse:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            raise HTTPException(status_code=401, detail="Sessao expirada")
        return StreamingResponse(lot_monitor_stream_events(request, session_user, db), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    @app.post("/saas/lots/{lot_id}/monitor")
    async def saas_update_lot_monitor(lot_id: int, request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=render_saas_login_html(), media_type="text/html")
        form = await request_form_dict(request)
        try:
            target_raw = str(form.get("target_price_usd") or "").strip()
            min_profit_raw = str(form.get("min_profit_pct") or "4").strip()
            monitor_payload = {
                "enabled": bool(form.get("enabled")),
                "notify_phone": normalize_user_phone(str(form.get("notify_phone") or "")),
                "target_price_usd": str(parse_decimal_web_field(target_raw, "target_price_usd")) if target_raw else "",
                "min_profit_pct": str(parse_decimal_web_field(min_profit_raw, "min_profit_pct")) if min_profit_raw else "4.00",
                "updated_by": str(session_user.get("telefone") or ""),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            updated = db.update_gold_inventory_lot_monitor(lot_id, monitor_payload)
            if not updated:
                raise HTTPException(status_code=404, detail="Lote nao encontrado para monitoramento")
            invalidate_dashboard_monitors_fragment_cache()
            invalidate_lot_monitor_snapshot_cache()
            html = render_saas_dashboard_html(db, session_user, notice=f"Monitor do lote GT-{lot_id} atualizado.", notice_kind="info", current_page="dashboard")
            return Response(content=html, media_type="text/html")
        except HTTPException as exc:
            html = render_saas_dashboard_html(db, session_user, notice=str(exc.detail), notice_kind="error", current_page="dashboard")
            return Response(content=html, media_type="text/html", status_code=exc.status_code)

    @app.get("/admin/dashboard")
    def admin_dashboard(x_webhook_token: Optional[str] = Header(default=None)) -> Response:
        validate_webhook_token(x_webhook_token)
        return Response(content=build_admin_dashboard_html(get_db()), media_type="text/html")