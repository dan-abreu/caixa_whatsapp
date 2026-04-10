from tests.business_rules.common import (
    BusinessRulesTestCase,
    _build_admin_dashboard_html,
    _build_gold_receipt_context,
    _build_saas_recent_fx_map,
    _build_saas_statement_context,
    _invalidate_lot_monitor_snapshot_cache_service,
    _invalidate_receipt_context_cache,
    _invalidate_receipt_context_cache_service,
    _invalidate_recent_fx_map_cache,
    _invalidate_reporting_cache,
    _invalidate_statement_context_cache,
    _invalidate_statement_context_cache_service,
    _render_saas_dashboard_html,
    main_module,
)
from tests.business_rules.fakes_runtime import _ExplodingInventoryTransactionsRenderDB, _ExplodingSaldoRenderDB, _FakeAdminDashboardDB, _FakeRecentFxDB, _FakeReceiptDB, _FakeStatementDB


class BusinessRulesCacheRenderTests(BusinessRulesTestCase):
    def test_statement_and_prefix_invalidation_helpers(self) -> None:
        db = _FakeStatementDB()
        first = _build_saas_statement_context(db, "2026-04-09", "2026-04-09")
        second = _build_saas_statement_context(db, "2026-04-09", "2026-04-09")
        self.assertEqual(db.calls, 1)
        self.assertEqual(first["summary"], second["summary"])
        _invalidate_statement_context_cache()
        self.assertEqual(_build_saas_statement_context(db, "2026-04-09", "2026-04-09")["transactions"][0]["id"], 1)
        for invalidate, prefix in [(_invalidate_statement_context_cache_service, "saas:statement"), (_invalidate_receipt_context_cache_service, "saas:receipt"), (_invalidate_lot_monitor_snapshot_cache_service, "saas:lot-monitor")]:
            cache_store = {f"{prefix}:target": {"data": {"ok": True}}, "other:keep": {"data": {"ok": True}}}
            invalidate(cache_store=cache_store, cache_key_prefix=prefix)
            self.assertIn("other:keep", cache_store)

    def test_recent_fx_and_receipt_caches(self) -> None:
        fx_db = _FakeRecentFxDB()
        first = _build_saas_recent_fx_map(fx_db)
        second = _build_saas_recent_fx_map(fx_db)
        self.assertEqual(fx_db.calls, 1)
        self.assertEqual(first["EUR"], second["EUR"])
        _invalidate_recent_fx_map_cache()
        self.assertEqual(_build_saas_recent_fx_map(fx_db)["USD"], "1")
        receipt_db = _FakeReceiptDB()
        self.assertEqual(_build_gold_receipt_context(receipt_db, 18)["operation_id"], 18)
        _invalidate_receipt_context_cache()
        self.assertEqual(_build_gold_receipt_context(receipt_db, 18)["operation_id"], 18)

    def test_dashboard_renderers_skip_unneeded_lookups(self) -> None:
        self.assertIn("Base de Clientes", _render_saas_dashboard_html(_ExplodingSaldoRenderDB(), {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"}, current_page="clients", clients_context={"search_term": "", "clients": [], "selected_account": None}))
        self.assertIn("Extrato Operacional", _render_saas_dashboard_html(_ExplodingSaldoRenderDB(), {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"}, current_page="statement", statement_context={"start_date": "2026-04-09", "end_date": "2026-04-09", "label": "Hoje (2026-04-09)", "summary": {"total_operacoes": 0, "total_usd": "0"}, "transactions": [], "statement_text": ""}))
        original_get_market_snapshot = main_module._get_market_snapshot
        original_build_open_lot_market_context = main_module._build_open_lot_market_context
        try:
            main_module._get_market_snapshot = lambda: {"xau_usd_raw": "3103.50", "grama_ref_raw": "89.80", "usd_brl_raw": "5.50", "eur_usd_raw": "1.10", "eur_brl_raw": "6.05", "xau_source": "test", "xau_source_label": "Teste", "status": "ok", "updated_at_label": "agora"}
            main_module._build_open_lot_market_context = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full lot market context should not be built for operation page"))
            html = _render_saas_dashboard_html(_ExplodingInventoryTransactionsRenderDB(), {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"}, current_page="operation")
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_open_lot_market_context = original_build_open_lot_market_context
        self.assertIn("Registro de Operacao", html)

    def test_singleton_db_and_admin_dashboard_cache(self) -> None:
        original_database_client = main_module.DatabaseClient
        original_db_instance = main_module._DB_INSTANCE
        created_instances = []
        class _FakeSingletonDB:
            def __init__(self):
                created_instances.append(self)
        try:
            main_module._DB_INSTANCE = None
            main_module.DatabaseClient = _FakeSingletonDB
            first = main_module.get_db()
            second = main_module.get_db()
        finally:
            main_module.DatabaseClient = original_database_client
            main_module._DB_INSTANCE = original_db_instance
        self.assertIs(first, second)
        self.assertEqual(len(created_instances), 1)
        db = _FakeAdminDashboardDB()
        first_html = _build_admin_dashboard_html(db)
        second_html = _build_admin_dashboard_html(db)
        self.assertEqual(db.summary_calls, 1)
        self.assertIn("Resumo Diario", second_html)
        _invalidate_reporting_cache()
        self.assertIn("Estoque Ouro", _build_admin_dashboard_html(db))
