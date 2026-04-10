from tests.business_rules.common import BusinessRulesTestCase, DatabaseClient, Decimal, _build_inventory_status_report_payload, main_module
from tests.business_rules.fakes_runtime import _FakeInventoryReportDB
from tests.business_rules.fakes_supabase import _FakeSupabaseClient


class BusinessRulesInventoryTests(BusinessRulesTestCase):
    def test_sync_gold_inventory_ledger_persists_fifo_state(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.get_gold_inventory_transactions = lambda end_iso=None: [{"id": 10, "tipo_operacao": "compra", "peso": "100", "preco_usd": "70", "criado_em": "2026-04-01T10:00:00+00:00"}, {"id": 11, "tipo_operacao": "compra", "peso": "50", "preco_usd": "80", "criado_em": "2026-04-01T11:00:00+00:00"}, {"id": 12, "tipo_operacao": "venda", "peso": "120", "preco_usd": "100", "criado_em": "2026-04-01T12:00:00+00:00"}]
        result = db.sync_gold_inventory_ledger()
        self.assertEqual((result["lots"], result["consumptions"]), (2, 2))
        self.assertEqual(Decimal(str(result["open_grams"])), Decimal("30"))

    def test_get_gold_inventory_status_and_pending_closure_rules(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_transactions"] = [{"peso": "25", "fechamento_gramas": "5", "fechamento_tipo": "parcial", "status": "registrada"}]
        db.client.store["gold_inventory_lots"] = [{"id": 1, "source_transaction_id": 10, "created_at_tx": "2026-04-01T10:00:00+00:00", "initial_grams": "100", "remaining_grams": "0", "unit_cost_usd": "70", "total_cost_usd": "7000", "status": "consumed"}, {"id": 2, "source_transaction_id": 11, "created_at_tx": "2026-04-01T11:00:00+00:00", "initial_grams": "50", "remaining_grams": "30", "unit_cost_usd": "80", "total_cost_usd": "4000", "status": "open"}]
        status = db.get_gold_inventory_status()
        self.assertEqual(Decimal(str(status["available_grams"])), Decimal("30"))
        self.assertEqual(Decimal(str(status["inventory_cost_usd"])), Decimal("2400.00"))
        self.assertEqual(Decimal(str(status["avg_cost_usd_per_gram"])), Decimal("80.00"))
        self.assertEqual(len(status["open_lots"]), 1)
        self.assertEqual(db.get_gold_pending_closure_grams(), Decimal("20"))

    def test_inventory_status_open_only_and_monitor_update(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_inventory_lots"] = [{"id": 1, "source_transaction_id": 11, "created_at_tx": "2026-04-01T11:00:00+00:00", "initial_grams": "50", "remaining_grams": "30", "unit_cost_usd": "80", "total_cost_usd": "4000", "status": "open", "metadata": {"teor": "85", "gold_type": "fundido", "quebra": "", "pessoa": "Ana"}}]
        db.get_gold_inventory_transactions = lambda: (_ for _ in ()).throw(AssertionError("fallback should not run"))
        self.assertEqual(db.get_gold_inventory_status(open_only=True)["open_lots"][0]["pessoa"], "Ana")
        updated = db.update_gold_inventory_lot_monitor(1, {"enabled": True, "target_price_usd": "95"})
        self.assertIsNotNone(updated)
        self.assertEqual(db.client.store["gold_inventory_lots"][0]["metadata"]["monitor"]["target_price_usd"], "95")

    def test_inventory_transaction_filters_and_invalid_rows(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_transactions"] = [{"id": 1, "tipo_operacao": "compra", "peso": "10", "preco_usd": "100", "criado_em": "2026-04-01T10:00:00+00:00", "status": "registrada"}, {"id": 2, "tipo_operacao": "venda", "peso": "5", "preco_usd": "120", "criado_em": "2026-04-01T11:00:00+00:00", "status": "cancelada"}]
        self.assertEqual(len(db.get_gold_inventory_transactions()), 1)
        db.get_gold_inventory_transactions = lambda end_iso=None: [{"id": 1, "tipo_operacao": "compra", "peso": "abc", "preco_usd": "100", "criado_em": "2026-04-01T10:00:00+00:00"}, {"id": 2, "tipo_operacao": "compra", "peso": "5", "preco_usd": "80", "criado_em": "2026-04-01T11:00:00+00:00"}]
        result = db.sync_gold_inventory_ledger()
        self.assertEqual((result["lots"], result["consumptions"]), (1, 0))

    def test_inventory_status_report_uses_cache_until_invalidated(self) -> None:
        db = _FakeInventoryReportDB()
        original_get_market_snapshot = main_module._get_market_snapshot
        original_build_open_lot_market_context = main_module._build_open_lot_market_context
        try:
            main_module._get_market_snapshot = lambda: {"updated_at_label": "agora"}
            main_module._build_open_lot_market_context = lambda open_lots, _snapshot: {"available_fine_grams": "4.5", "market_value_usd": "450", "unrealized_pnl_usd": "50", "by_teor": [], "lots": open_lots}
            first = _build_inventory_status_report_payload(db)
            second = _build_inventory_status_report_payload(db)
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_open_lot_market_context = original_build_open_lot_market_context
        self.assertEqual(db.inventory_calls, 1)
        self.assertEqual(first["available_grams"], "5")
        self.assertEqual(second["market_value_usd"], "450")
