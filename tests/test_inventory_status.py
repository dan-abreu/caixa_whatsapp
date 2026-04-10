import unittest
from decimal import Decimal
from typing import Any, Dict, List

from app.database import DatabaseClient


class _FakeResponse:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class _FakeTable:
    def __init__(self, client: "_FakeSupabaseClient", name: str):
        self.client = client
        self.name = name
        self._filters: List[tuple[str, Any]] = []
        self._selected_fields = ""
        self._limit: int | None = None

    def select(self, fields: str):
        self._selected_fields = fields
        return self

    def order(self, _field: str, desc: bool = False):
        self._descending = desc
        return self

    def eq(self, field: str, value: Any):
        self._filters.append((field, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def execute(self):
        if (
            self.name == "gold_transactions"
            and self.client.fail_extended_gold_select
            and self._selected_fields == "peso,fechamento_gramas,fechamento_tipo,status"
        ):
            raise Exception("column gold_transactions.status does not exist")

        rows = [dict(row) for row in self.client.store.get(self.name, [])]
        for field, value in self._filters:
            rows = [row for row in rows if row.get(field) == value]
        if self._limit is not None:
            rows = rows[: self._limit]
        self._filters = []
        self._selected_fields = ""
        self._limit = None
        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self, *, fail_extended_gold_select: bool = False):
        self.fail_extended_gold_select = fail_extended_gold_select
        self.store: Dict[str, List[Dict[str, Any]]] = {
            "gold_inventory_lots": [],
            "gold_transactions": [],
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)


class InventoryStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseClient._RUNTIME_CACHE = {}
        DatabaseClient._GOLD_PENDING_CLOSURE_SCHEMA_READY = None

    def test_get_gold_inventory_status_tolerates_invalid_lookup_ids_per_row(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_inventory_lots"] = [
            {
                "id": 1,
                "source_transaction_id": "quebrado",
                "created_at_tx": "2026-04-01T10:00:00+00:00",
                "initial_grams": "10",
                "remaining_grams": "10",
                "unit_cost_usd": "70",
                "total_cost_usd": "700",
                "status": "open",
                "metadata": {},
            },
            {
                "id": 2,
                "source_transaction_id": 11,
                "created_at_tx": "2026-04-01T11:00:00+00:00",
                "initial_grams": "5",
                "remaining_grams": "5",
                "unit_cost_usd": "80",
                "total_cost_usd": "400",
                "status": "open",
                "metadata": {},
            },
        ]
        inventory_transactions = [
            {"id": "quebrado", "teor": "90", "gold_type": "fundido", "quebra": "", "pessoa": "Ignorado"},
            {"id": 11, "teor": "85", "gold_type": "fundido", "quebra": "", "pessoa": "Ana"},
        ]

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            status = db.get_gold_inventory_status(inventory_transactions=inventory_transactions)

        self.assertEqual(Decimal(str(status["available_grams"])), Decimal("15"))
        self.assertEqual(2, len(status["open_lots"]))
        valid_lot = next(row for row in status["open_lots"] if row["id"] == 2)
        invalid_lot = next(row for row in status["open_lots"] if row["id"] == 1)
        self.assertEqual("85", valid_lot["teor"])
        self.assertIsNone(invalid_lot["teor"])
        self.assertIn("converter inteiro", "\n".join(captured.output))

    def test_get_gold_pending_closure_grams_logs_and_uses_reduced_select_fallback(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient(fail_extended_gold_select=True)
        db.client.store["gold_transactions"] = [
            {"peso": "25", "fechamento_gramas": "5", "fechamento_tipo": "parcial", "status": "registrada"},
        ]

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            pending = db.get_gold_pending_closure_grams()

        self.assertEqual(Decimal("20"), pending)
        self.assertIn("schema estendido", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()