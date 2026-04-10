import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.database.gold_transactions import GoldTransactionsMixin


class _FakeResponse:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class _FakeTable:
    def __init__(self, client: "_FakeSupabaseClient", name: str):
        self.client = client
        self.name = name
        self._filters: List[tuple[str, Any]] = []
        self._limit: Optional[int] = None
        self._pending_insert: Any = None
        self._pending_update: Optional[Dict[str, Any]] = None

    def select(self, _fields: str):
        return self

    def eq(self, field: str, value: Any):
        self._filters.append((field, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def order(self, _field: str, desc: bool = False):
        self._descending = desc
        return self

    def insert(self, payload: Any):
        self._pending_insert = payload
        return self

    def update(self, payload: Dict[str, Any]):
        self._pending_update = dict(payload)
        return self

    def execute(self):
        if self._pending_insert is not None:
            if self.name == "gold_payments" and self.client.fail_gold_payments_insert:
                self._pending_insert = None
                raise Exception("gold_payments insert failed")
            pending = self._pending_insert
            self._pending_insert = None
            rows = pending if isinstance(pending, list) else [pending]
            inserted: List[Dict[str, Any]] = []
            for row in rows:
                created = dict(row)
                created.setdefault("id", len(self.client.store.setdefault(self.name, [])) + 1)
                self.client.store.setdefault(self.name, []).append(created)
                inserted.append(dict(created))
            return _FakeResponse(inserted)

        if self._pending_update is not None:
            changes = self._pending_update
            self._pending_update = None
            updated: List[Dict[str, Any]] = []
            for row in self.client.store.get(self.name, []):
                if all(row.get(field) == value for field, value in self._filters):
                    row.update(changes)
                    updated.append(dict(row))
            self._filters = []
            return _FakeResponse(updated)

        rows = [dict(row) for row in self.client.store.get(self.name, []) if all(row.get(field) == value for field, value in self._filters)]
        if self._limit is not None:
            rows = rows[: self._limit]
        self._filters = []
        self._limit = None
        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self, *, fail_gold_payments_insert: bool = False):
        self.fail_gold_payments_insert = fail_gold_payments_insert
        self.store: Dict[str, List[Dict[str, Any]]] = {
            "gold_transactions": [],
            "gold_payments": [],
            "caixas": [{"moeda": "USD", "saldo": "100", "atualizado_em": "2026-04-10T10:00:00+00:00"}],
            "caixas_movimentacoes": [],
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)


class _TestGoldTransactionsDB(GoldTransactionsMixin):
    def __init__(self, client: _FakeSupabaseClient):
        self.client = client
        self.fx_rates: List[tuple[str, str, Decimal, str]] = []
        self.caixa_updates: List[Dict[str, Any]] = []
        self.journal_entries: List[Dict[str, Any]] = []
        self.client_balance_calls: List[Dict[str, Any]] = []
        self.caixa_movimentos: List[Dict[str, Any]] = []
        self.synced = 0

    def _safe_record_fx_rate(self, base_currency: str, quote_currency: str, rate: Decimal, source: str = "app_operation") -> None:
        self.fx_rates.append((base_currency, quote_currency, rate, source))

    def update_caixas_from_transaction(self, gold_transaction_id: int, tipo_operacao: str, peso_gramas: Decimal, pagamentos: List[Dict[str, Any]], pessoa: str) -> None:
        self.caixa_updates.append({"gold_transaction_id": gold_transaction_id, "tipo_operacao": tipo_operacao, "peso": str(peso_gramas), "pessoa": pessoa})

    def _safe_record_journal_entry(self, reference_table: str, reference_id: Optional[int], description: str, source_message_id: Optional[str], created_by: Optional[str], metadata: Dict[str, Any], lines: List[Dict[str, Any]]) -> None:
        self.journal_entries.append({"reference_table": reference_table, "reference_id": reference_id, "description": description, "lines": lines})

    def record_cliente_operation_balance(self, cliente_id: int, gold_transaction_id: int, tipo_operacao: str, pending_grams: Decimal, pessoa: Optional[str] = None, reverse: bool = False) -> None:
        self.client_balance_calls.append({"cliente_id": cliente_id, "gold_transaction_id": gold_transaction_id, "tipo_operacao": tipo_operacao, "pending_grams": str(pending_grams), "reverse": reverse})

    def sync_gold_inventory_ledger(self) -> None:
        self.synced += 1

    def _invalidate_runtime_cache(self, *keys: str) -> None:
        self.invalidated = list(keys)

    def _gold_inventory_status_cache_key(self, *, open_only: bool) -> str:
        return f"gold_inventory_status:{open_only}"

    def _invalidate_cliente_account_snapshot_cache(self, cliente_id: int) -> None:
        self.invalidated_cliente_id = cliente_id

    def _invalidate_client_list_cache(self) -> None:
        self.client_list_invalidated = True

    def get_saldo_caixa(self) -> Dict[str, str]:
        return {row["moeda"]: str(row["saldo"]) for row in self.client.store["caixas"]}

    def _record_caixa_movimentacao(self, caixa_moeda: str, tipo_operacao: str, gold_transaction_id: int, valor: Decimal, saldo_anterior: Decimal, saldo_atual: Decimal, descricao: str, pessoa: str) -> None:
        self.caixa_movimentos.append({"moeda": caixa_moeda, "valor": str(valor), "descricao": descricao, "pessoa": pessoa})


class GoldTransactionsTests(unittest.TestCase):
    def test_insert_gold_transaction_tolerates_invalid_cliente_id(self) -> None:
        db = _TestGoldTransactionsDB(_FakeSupabaseClient())

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            created = db.insert_gold_transaction(
                {"tipo_operacao": "compra", "peso": "5", "fechamento_gramas": "3", "pessoa": "Ana", "operador_id": "op", "cliente_id": "quebrado"},
                [{"moeda": "USD", "valor_moeda": "100", "cambio_para_usd": "1", "valor_usd": "100", "forma_pagamento": "dinheiro"}],
            )

        self.assertIsNotNone(created)
        self.assertEqual(1, len(db.caixa_updates))
        self.assertEqual([], db.client_balance_calls)
        self.assertIn("gold_transactions.cliente_id", "\n".join(captured.output))

    def test_insert_gold_transaction_tolerates_invalid_payment_exchange_rate(self) -> None:
        db = _TestGoldTransactionsDB(_FakeSupabaseClient())

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            created = db.insert_gold_transaction(
                {"tipo_operacao": "compra", "peso": "5", "fechamento_gramas": "5", "pessoa": "Ana", "operador_id": "op"},
                [{"moeda": "EUR", "valor_moeda": "100", "cambio_para_usd": "invalido", "valor_usd": "90", "forma_pagamento": "dinheiro"}],
            )

        self.assertIsNotNone(created)
        self.assertEqual([], db.fx_rates)
        self.assertEqual("0", db.client.store["gold_payments"][0]["cambio_para_usd"])
        self.assertIn("gold_payments.EUR.cambio_para_usd", "\n".join(captured.output))

    def test_cancel_gold_transaction_skips_invalid_movement_value_and_still_cancels(self) -> None:
        client = _FakeSupabaseClient()
        client.store["gold_transactions"] = [{"id": 1, "cliente_id": 2, "peso": "5", "fechamento_gramas": "3", "tipo_operacao": "compra", "pessoa": "Ana", "status": "registrada"}]
        client.store["caixas_movimentacoes"] = [
            {"gold_transaction_id": 1, "caixa_moeda": "USD", "valor": "invalido"},
            {"gold_transaction_id": 1, "caixa_moeda": "USD", "valor": "10"},
        ]
        db = _TestGoldTransactionsDB(client)

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            result = db.cancel_gold_transaction(1, cancelled_by="op")

        self.assertTrue(result)
        self.assertEqual("cancelada", client.store["gold_transactions"][0]["status"])
        self.assertEqual(1, len(db.caixa_movimentos))
        self.assertEqual(1, len(db.client_balance_calls))
        self.assertTrue(db.client_balance_calls[0]["reverse"])
        self.assertIn("caixas_movimentacoes.USD.valor", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()