from tests.business_rules.common import (
    BusinessRulesTestCase,
    _MARKET_TICK_HISTORY,
    _build_lot_monitor_snapshot_payload,
    _build_lot_sell_signal,
    _build_market_trend_context,
    _build_open_lot_market_context,
    _build_operation_lot_market_context,
    _build_web_lot_ai_alert_summary,
    _build_web_lot_ai_alerts,
    _build_web_lot_monitor_entries,
    _invalidate_lot_monitor_snapshot_cache,
    _lot_monitor_stream_events,
    _parse_google_news_feed,
    _render_saas_dashboard_html,
    asyncio,
    main_module,
)
from tests.business_rules.fakes_runtime import _ExplodingInventoryTransactionsRenderDB, _FakeLotMonitorDB, _FakeStreamingRequest


class BusinessRulesMarketTests(BusinessRulesTestCase):
    def test_open_lot_market_context_and_operation_projection(self) -> None:
        open_lots = [{"source_id": 1, "remaining_grams": "100", "unit_cost_usd": "70", "teor": "90", "criado_em": "2026-04-01T10:00:00+00:00"}, {"source_id": 2, "remaining_grams": "100", "unit_cost_usd": "70", "teor": "85", "criado_em": "2026-04-01T10:00:00+00:00"}]
        context = _build_open_lot_market_context(open_lots, {"xau_usd_raw": "3103.50"})
        self.assertEqual(context["available_fine_grams"], "175.00")
        self.assertEqual(_build_operation_lot_market_context(open_lots, {"xau_usd_raw": "3103.50"})["available_fine_grams"], context["available_fine_grams"])

    def test_market_trend_and_sell_signal_helpers(self) -> None:
        for value in ["3000", "3010", "3020", "3030", "3045", "3060", "3075"]:
            _MARKET_TICK_HISTORY.append({"xau_usd_raw": value})
        self.assertIn(_build_market_trend_context()["signal"], {"bullish", "constructive"})
        limit_signal = _build_lot_sell_signal({"unit_cost_usd": "80", "market_unit_usd": "95", "unrealized_pnl_usd": "300", "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5", "notify_phone": "+5977000000"}}}, {"signal": "constructive"})
        self.assertEqual(limit_signal["status"], "limite_atingido")
        protect_signal = _build_lot_sell_signal({"unit_cost_usd": "80", "market_unit_usd": "84", "unrealized_pnl_usd": "40", "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5", "notify_phone": "+5977000000"}}}, {"signal": "bearish"})
        self.assertEqual(protect_signal["status"], "proteger_lucro")

    def test_lot_alerts_entries_and_news_feed(self) -> None:
        context = {"lots": [{"id": 170, "source_transaction_id": 26, "remaining_grams": "10", "market_unit_usd": "95", "unrealized_pnl_usd": "300", "unit_cost_usd": "80", "teor": "90", "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5", "notify_phone": "+5977000000"}}}]}
        alerts = _build_web_lot_ai_alerts(context, {"signal": "constructive"})
        self.assertIn("GT-26", _build_web_lot_ai_alert_summary(alerts))
        entries = _build_web_lot_monitor_entries(context, {"signal": "constructive"}, default_alert_phone="+59711111111")
        self.assertEqual(entries[0]["notify_phone"], "+5977000000")
        self.assertEqual(_parse_google_news_feed("<rss><channel><item><title>Ouro sobe</title><link>https://example.com/a</link><pubDate>Wed, 08 Apr 2026 10:00:00 GMT</pubDate><source>Fonte X</source></item></channel></rss>", "ouro")[0]["topic"], "ouro")

    def test_snapshot_payload_and_stream_reuse_passed_db(self) -> None:
        db = _FakeLotMonitorDB()
        original_get_market_snapshot = main_module._get_market_snapshot
        original_build_market_trend_context = main_module._build_market_trend_context
        original_build_open_lot_market_context = main_module._build_open_lot_market_context
        original_build_web_lot_monitor_view_model = main_module._build_web_lot_monitor_view_model
        try:
            main_module._get_market_snapshot = lambda: {"updated_at_label": "agora"}
            main_module._build_market_trend_context = lambda: {"trend_label": "Alta", "signal": "constructive"}
            main_module._build_open_lot_market_context = lambda open_lots, snapshot: {"lots": open_lots, "snapshot": snapshot}
            main_module._build_web_lot_monitor_view_model = lambda context, trend, default_alert_phone="", entry_limit=24, alert_limit=4: {"summary": "1 monitor ativo.", "alerts": [{"signature": "sig-1", "message": "alerta"}] if context.get("lots") else [], "entries": [{"id": 10, "enabled": True, "notify_phone": default_alert_phone}]}
            first = _build_lot_monitor_snapshot_payload(db, {"telefone": "+5977000000"})
            second = _build_lot_monitor_snapshot_payload(db, {"telefone": "+5977000000"})
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_market_trend_context = original_build_market_trend_context
            main_module._build_open_lot_market_context = original_build_open_lot_market_context
            main_module._build_web_lot_monitor_view_model = original_build_web_lot_monitor_view_model
        self.assertEqual(db.inventory_calls, 1)
        self.assertEqual(second["lots"][0]["notify_phone"], "+5977000000")
        _invalidate_lot_monitor_snapshot_cache()
        request = _FakeStreamingRequest(disconnect_after=2)
        original_builder = main_module._build_lot_monitor_snapshot_payload
        original_interval = main_module._LOT_MONITOR_STREAM_INTERVAL_SECONDS
        seen_db_ids = []
        async def _collect_events() -> list[str]:
            events = []
            current_db = object()
            async for item in _lot_monitor_stream_events(request, {"telefone": "+5977000000"}, current_db):
                events.append(item)
            return events
        try:
            main_module._LOT_MONITOR_STREAM_INTERVAL_SECONDS = 0
            main_module._build_lot_monitor_snapshot_payload = lambda current_db, _user: seen_db_ids.append(id(current_db)) or {"ok": True}
            events = asyncio.run(_collect_events())
        finally:
            main_module._build_lot_monitor_snapshot_payload = original_builder
            main_module._LOT_MONITOR_STREAM_INTERVAL_SECONDS = original_interval
        self.assertEqual(len(events), 2)

    def test_render_saas_dashboard_monitors_page_uses_combined_monitor_view_model(self) -> None:
        original_get_market_snapshot = main_module._get_market_snapshot
        original_build_web_lot_ai_alerts = main_module._build_web_lot_ai_alerts
        original_build_web_lot_monitor_entries = main_module._build_web_lot_monitor_entries
        original_build_web_lot_monitor_view_model = main_module._build_web_lot_monitor_view_model
        try:
            main_module._get_market_snapshot = lambda: {"xau_usd_raw": "3103.50", "grama_ref_raw": "89.80", "usd_brl_raw": "5.50", "eur_usd_raw": "1.10", "eur_brl_raw": "6.05", "xau_source": "test", "xau_source_label": "Teste", "status": "ok", "updated_at_label": "agora"}
            main_module._build_web_lot_ai_alerts = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("separate alert builder should not be used for monitors page"))
            main_module._build_web_lot_monitor_entries = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("separate entry builder should not be used for monitors page"))
            main_module._build_web_lot_monitor_view_model = lambda *_args, **_kwargs: {"summary": "1 monitor ativo.", "alerts": [{"source_transaction_id": 18, "status_label": "Limite atingido", "profit_pct": "12", "reason": "Meta tocada."}], "entries": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "teor": "90", "hold_days": "0", "entry_unit_usd": "80.00", "market_unit_usd": "95", "target_unit_usd": "90", "target_gap_usd": "-5.00", "target_progress_pct": "100.00", "unrealized_pnl_usd": "75", "profit_pct": "12", "min_profit_gap_pct": "7", "reason": "Meta tocada.", "status": "limite_atingido", "status_label": "Limite atingido", "action_label": "Executar venda", "status_class": "positive", "trend_signal": "constructive", "trend_label": "Alta moderada", "enabled": True, "notify_phone": "+5977000000", "target_price_usd": "90", "min_profit_pct": "5"}]}
            html = _render_saas_dashboard_html(_ExplodingInventoryTransactionsRenderDB(), {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"}, current_page="monitors")
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_web_lot_ai_alerts = original_build_web_lot_ai_alerts
            main_module._build_web_lot_monitor_entries = original_build_web_lot_monitor_entries
            main_module._build_web_lot_monitor_view_model = original_build_web_lot_monitor_view_model
        self.assertIn("Monitores IA dos Lotes", html)
