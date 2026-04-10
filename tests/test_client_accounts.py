import unittest
from typing import Any, Dict, List

from app.database import DatabaseClient


class _FakeResponse:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class _FakeTable:
    def __init__(self, store: Dict[str, Any], name: str):
        self.store = store
        self.name = name
        self._filters: List[tuple[str, Any]] = []
        self._ilike_filters: List[tuple[str, str]] = []
        self._in_filters: List[tuple[str, set[Any]]] = []
        self._limit: int | None = None
        self._order_by: tuple[str, bool] | None = None
        self._pending_insert: Dict[str, Any] | List[Dict[str, Any]] | None = None

    def select(self, _fields: str):
        return self

    def eq(self, field: str, value: Any):
        self._filters.append((field, value))
        return self

    def ilike(self, field: str, value: str):
        self._ilike_filters.append((field, value))
        return self

    def in_(self, field: str, values: List[Any]):
        self._in_filters.append((field, set(values)))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def order(self, field: str, desc: bool = False):
        self._order_by = (field, desc)
        return self

    def insert(self, payload: Dict[str, Any] | List[Dict[str, Any]]):
        self._pending_insert = payload
        return self

    def execute(self):
        if self._pending_insert is not None:
            pending = self._pending_insert
            self._pending_insert = None
            rows = pending if isinstance(pending, list) else [pending]
            inserted: List[Dict[str, Any]] = []
            for row in rows:
                created = dict(row)
                created.setdefault("id", len(self.store.setdefault(self.name, [])) + 1)
                self.store.setdefault(self.name, []).append(created)
                inserted.append(dict(created))
            return _FakeResponse(inserted)

        rows = [dict(row) for row in self.store.get(self.name, [])]
        for field, value in self._filters:
            rows = [row for row in rows if row.get(field) == value]
        for field, pattern in self._ilike_filters:
            self.store["_cliente_search_exec_count"] = int(self.store.get("_cliente_search_exec_count", 0)) + 1
            needle = pattern.replace("%", "").lower()
            rows = [row for row in rows if needle in str(row.get(field) or "").lower()]
        for field, values in self._in_filters:
            rows = [row for row in rows if row.get(field) in values]
        if self._order_by is not None:
            field, desc = self._order_by
            rows = sorted(rows, key=lambda row: row.get(field), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        self._filters = []
        self._ilike_filters = []
        self._in_filters = []
        self._limit = None
        self._order_by = None
        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self):
        self.store: Dict[str, Any] = {
            "clientes": [],
            "cliente_movimentacoes": [],
            "_cliente_search_exec_count": 0,
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.store, name)


class ClientAccountsTests(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseClient._RUNTIME_CACHE = {}
        DatabaseClient._LOCAL_RUNTIME_CACHE = {}

    def test_create_cliente_tolerates_invalid_opening_balance_and_keeps_valid_rows(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            created = db.create_cliente("Ana", opening_balances={"USD": "100", "XAU": "quebrado", "EUR": "0"})

        self.assertIsNotNone(created)
        self.assertEqual(1, len(db.client.store["clientes"]))
        self.assertEqual(1, len(db.client.store["cliente_movimentacoes"]))
        self.assertEqual("USD", db.client.store["cliente_movimentacoes"][0]["moeda"])
        self.assertIn("opening_balances.XAU", "\n".join(captured.output))

    def test_search_clientes_skips_invalid_row_ids_without_losing_valid_matches(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": "quebrado", "nome": "Ana Ruim", "apelido": "ana", "telefone": "+5977000000", "documento": "A", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
            {"id": 2, "nome": "Ana Paula", "apelido": "ana", "telefone": "+5977111111", "documento": "B", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T09:00:00+00:00"},
        ]

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            result = db.search_clientes("Ana", limit=8)

        self.assertEqual(1, len(result))
        self.assertEqual("Ana Paula", result[0]["nome"])
        self.assertIn("clientes.nome.id", "\n".join(captured.output))

    def test_list_clientes_with_balances_skips_invalid_client_ids_and_keeps_valid_balances(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": "quebrado", "nome": "Ana Ruim", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
            {"id": 2, "nome": "Bruno", "ativo": True, "atualizado_em": "2026-04-09T09:00:00+00:00"},
        ]
        db.client.store["cliente_movimentacoes"] = [
            {"cliente_id": 2, "moeda": "USD", "valor": "50"},
        ]

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            items = db.list_clientes_with_balances(limit=10)

        self.assertEqual(1, len(items))
        self.assertEqual("Bruno", items[0]["nome"])
        self.assertEqual("50", items[0]["balances"]["USD"])
        self.assertEqual(1, sum("clientes_with_balances.id" in line for line in captured.output))

    def test_get_cliente_balance_summaries_logs_invalid_ids_once(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["cliente_movimentacoes"] = [
            {"cliente_id": 2, "moeda": "USD", "valor": "50"},
        ]

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            balances = db.get_cliente_balance_summaries(["quebrado", 2])

        self.assertEqual("50", str(balances[2]["USD"]))
        self.assertEqual(1, sum("cliente_ids" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()