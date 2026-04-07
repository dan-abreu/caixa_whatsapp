import unittest
from decimal import Decimal

from app.database import DatabaseClient
from app.main import (
    _build_fifo_inventory_lots,
    _find_negative_caixa_balances,
    _preview_fifo_sale_consumption,
    _project_caixa_balances,
    _should_reset_guided_session_for_message,
)


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []
        self._order_by = None
        self._pending_insert = None
        self._delete_mode = False

    def select(self, _fields):
        return self

    def order(self, field, desc=False):
        self._order_by = (field, desc)
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def neq(self, _field, _value):
        return self

    def delete(self):
        self._delete_mode = True
        return self

    def insert(self, payload):
        self._pending_insert = dict(payload)
        return self

    def execute(self):
        if self._delete_mode:
            self.store[self.name] = []
            self._delete_mode = False
            return _FakeResponse([])

        if self._pending_insert is not None:
            row = dict(self._pending_insert)
            row["id"] = len(self.store[self.name]) + 1
            self.store[self.name].append(row)
            self._pending_insert = None
            return _FakeResponse([row])

        rows = [dict(row) for row in self.store[self.name]]
        for field, value in self._filters:
            rows = [row for row in rows if row.get(field) == value]
        if self._order_by:
            field, desc = self._order_by
            rows = sorted(rows, key=lambda row: row.get(field), reverse=desc)
        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self):
        self.store = {
            "gold_inventory_lots": [],
            "gold_inventory_consumptions": [],
        }

    def table(self, name):
        return _FakeTable(self.store, name)


class BusinessRulesTests(unittest.TestCase):
    def test_fifo_consumption_uses_oldest_purchase_first(self) -> None:
        transactions = [
            {"id": 1, "tipo_operacao": "compra", "peso": "100", "preco_usd": "70", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"id": 2, "tipo_operacao": "compra", "peso": "50", "preco_usd": "80", "criado_em": "2026-04-01T11:00:00+00:00"},
            {"id": 3, "tipo_operacao": "venda", "peso": "60", "preco_usd": "120", "criado_em": "2026-04-01T12:00:00+00:00"},
        ]
        lots = _build_fifo_inventory_lots(transactions)
        self.assertEqual(lots[0]["source_id"], 1)
        self.assertEqual(Decimal(str(lots[0]["remaining_grams"])), Decimal("40"))
        self.assertEqual(Decimal(str(lots[1]["remaining_grams"])), Decimal("50"))

        preview = _preview_fifo_sale_consumption(lots, Decimal("70"))
        self.assertEqual(Decimal(str(preview["consumed_grams"])), Decimal("70"))
        self.assertEqual(Decimal(str(preview["shortfall_grams"])), Decimal("0"))
        self.assertEqual(Decimal(str(preview["consumed_cost_usd"])), Decimal("5200.00"))

    def test_projected_balances_flag_negative_boxes(self) -> None:
        projected = _project_caixa_balances(
            {"XAU": "20", "USD": "1000", "EUR": "0", "SRD": "0", "BRL": "0"},
            "compra",
            Decimal("10"),
            [{"moeda": "USD", "valor_moeda": "1500"}],
        )
        negatives = _find_negative_caixa_balances(projected)
        self.assertEqual(negatives[0][0], "USD")
        self.assertEqual(negatives[0][1], Decimal("-500"))

    def test_reset_session_for_greeting_and_global_commands(self) -> None:
        self.assertTrue(_should_reset_guided_session_for_message("oii"))
        self.assertTrue(_should_reset_guided_session_for_message("caixa"))
        self.assertTrue(_should_reset_guided_session_for_message("extrato hoje"))
        self.assertFalse(_should_reset_guided_session_for_message("50000"))

    def test_sync_gold_inventory_ledger_persists_fifo_state(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.get_gold_inventory_transactions = lambda end_iso=None: [
            {"id": 10, "tipo_operacao": "compra", "peso": "100", "preco_usd": "70", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"id": 11, "tipo_operacao": "compra", "peso": "50", "preco_usd": "80", "criado_em": "2026-04-01T11:00:00+00:00"},
            {"id": 12, "tipo_operacao": "venda", "peso": "120", "preco_usd": "100", "criado_em": "2026-04-01T12:00:00+00:00"},
        ]

        result = db.sync_gold_inventory_ledger()

        self.assertEqual(result["lots"], 2)
        self.assertEqual(result["consumptions"], 2)
        self.assertEqual(Decimal(str(result["open_grams"])), Decimal("30"))

        lots = db.client.store["gold_inventory_lots"]
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots[0]["status"], "consumed")
        self.assertEqual(Decimal(str(lots[1]["remaining_grams"])), Decimal("30"))

        consumptions = db.client.store["gold_inventory_consumptions"]
        self.assertEqual(len(consumptions), 2)
        self.assertEqual(Decimal(str(consumptions[0]["consumed_grams"])), Decimal("100"))
        self.assertEqual(Decimal(str(consumptions[1]["consumed_grams"])), Decimal("20"))

    def test_get_gold_inventory_status_aggregates_open_lots(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_inventory_lots"] = [
            {
                "id": 1,
                "source_transaction_id": 10,
                "created_at_tx": "2026-04-01T10:00:00+00:00",
                "initial_grams": "100",
                "remaining_grams": "0",
                "unit_cost_usd": "70",
                "total_cost_usd": "7000",
                "status": "consumed",
            },
            {
                "id": 2,
                "source_transaction_id": 11,
                "created_at_tx": "2026-04-01T11:00:00+00:00",
                "initial_grams": "50",
                "remaining_grams": "30",
                "unit_cost_usd": "80",
                "total_cost_usd": "4000",
                "status": "open",
            },
        ]

        status = db.get_gold_inventory_status()

        self.assertEqual(Decimal(str(status["available_grams"])), Decimal("30"))
        self.assertEqual(Decimal(str(status["inventory_cost_usd"])), Decimal("2400.00"))
        self.assertEqual(Decimal(str(status["avg_cost_usd_per_gram"])), Decimal("80.00"))
        self.assertEqual(len(status["open_lots"]), 1)


if __name__ == "__main__":
    unittest.main()
