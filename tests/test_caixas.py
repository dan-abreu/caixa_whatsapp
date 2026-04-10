import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.database.caixas_rebuild import CaixasRebuildMixin
from app.database.caixas_runtime import CaixasRuntimeMixin
from app.database.transfer_money import TransferMoneyMixin


class _FakeResponse:
    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data


class _FakeTable:
    def __init__(self, store: Dict[str, List[Dict[str, Any]]], name: str):
        self.store = store
        self.name = name
        self._filters: List[tuple[str, Any]] = []
        self._in_filters: List[tuple[str, set[Any]]] = []
        self._pending_insert: Optional[Any] = None

    def select(self, _fields: str):
        return self

    def eq(self, field: str, value: Any):
        self._filters.append((field, value))
        return self

    def in_(self, field: str, values: List[Any]):
        self._in_filters.append((field, set(values)))
        return self

    def insert(self, payload: Any):
        self._pending_insert = payload
        return self

    def update(self, payload: Dict[str, Any]):
        self._pending_insert = {"__update__": dict(payload)}
        return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self._pending_insert is not None:
            pending = self._pending_insert
            self._pending_insert = None
            if isinstance(pending, dict) and "__update__" in pending:
                changes = pending["__update__"]
                updated: List[Dict[str, Any]] = []
                for row in rows:
                    if self._matches(row):
                        row.update(changes)
                        updated.append(dict(row))
                self._reset()
                return _FakeResponse(updated)

            payloads = pending if isinstance(pending, list) else [pending]
            inserted: List[Dict[str, Any]] = []
            for payload in payloads:
                row = dict(payload)
                row.setdefault("id", len(rows) + 1)
                rows.append(row)
                inserted.append(dict(row))
            self._reset()
            return _FakeResponse(inserted)

        selected = [dict(row) for row in rows if self._matches(row)]
        self._reset()
        return _FakeResponse(selected)

    def _matches(self, row: Dict[str, Any]) -> bool:
        for field, value in self._filters:
            if row.get(field) != value:
                return False
        for field, values in self._in_filters:
            if row.get(field) not in values:
                return False
        return True

    def _reset(self) -> None:
        self._filters = []
        self._in_filters = []


class _FakeSupabaseClient:
    def __init__(self):
        self.store: Dict[str, List[Dict[str, Any]]] = {
            "caixas": [],
            "caixas_movimentacoes": [],
        }

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self.store, name)


class _EmptyInsertTable(_FakeTable):
    def execute(self):
        if self._pending_insert is not None:
            self._pending_insert = None
            self._reset()
            return _FakeResponse([])
        return super().execute()


class _TransferMoneyClient(_FakeSupabaseClient):
    def __init__(self, insert_mode: str = "normal"):
        super().__init__()
        self.insert_mode = insert_mode
        self.store.setdefault("transfer_money_transactions", [])

    def table(self, name: str) -> _FakeTable:
        if name == "transfer_money_transactions" and self.insert_mode == "empty":
            return _EmptyInsertTable(self.store, name)
        return _FakeTable(self.store, name)


class _TestCaixasDB(CaixasRuntimeMixin):
    _CAIXAS_READY: Optional[bool] = None
    _RUNTIME_CACHE: Dict[str, Any] = {}

    def __init__(self, client: _FakeSupabaseClient):
        self.client = client

    @classmethod
    def _get_runtime_cache(cls, key: str) -> Optional[Any]:
        return cls._RUNTIME_CACHE.get(key)

    @classmethod
    def _set_runtime_cache(cls, key: str, value: Any) -> Any:
        cls._RUNTIME_CACHE[key] = value
        return value

    @classmethod
    def _invalidate_runtime_cache(cls, *keys: str) -> None:
        for key in keys:
            cls._RUNTIME_CACHE.pop(key, None)


class _TestCaixasRebuildDB(_TestCaixasDB, CaixasRebuildMixin):
    def get_ativo_by_nome(self, nome: str) -> Optional[Dict[str, Any]]:
        if nome in {"Ouro", "Ouro 24k"}:
            return {"id": 1, "nome": nome}
        return None


class _TestTransferMoneyDB(TransferMoneyMixin):
    def __init__(self, client: _TransferMoneyClient):
        self.client = client
        self.fx_rates: List[tuple[str, str, Decimal, str]] = []
        self.journal_entries: List[Dict[str, Any]] = []

    def _safe_record_fx_rate(self, base_currency: str, quote_currency: str, rate: Decimal, source: str = "app_operation") -> None:
        self.fx_rates.append((base_currency, quote_currency, rate, source))

    def _safe_record_journal_entry(self, reference_table: str, reference_id: Optional[int], description: str, source_message_id: Optional[str], created_by: Optional[str], metadata: Dict[str, Any], lines: List[Dict[str, Any]]) -> None:
        self.journal_entries.append({"reference_table": reference_table, "reference_id": reference_id, "description": description, "lines": lines})


class CaixasRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        _TestCaixasDB._CAIXAS_READY = None
        _TestCaixasDB._RUNTIME_CACHE = {}
        self.client = _FakeSupabaseClient()
        self.db = _TestCaixasDB(self.client)

    def test_ensure_caixas_exist_creates_defaults_without_duplicates(self) -> None:
        self.db._ensure_caixas_exist()
        self.db._ensure_caixas_exist()

        moedas = sorted(row["moeda"] for row in self.client.store["caixas"])
        self.assertEqual(["BRL", "EUR", "SRD", "USD", "XAU"], moedas)
        self.assertEqual(5, len(self.client.store["caixas"]))

    def test_compra_updates_balances_movements_and_invalidates_cache(self) -> None:
        initial = self.db.get_saldo_caixa()
        self.assertEqual(
            {"XAU": "0", "EUR": "0", "USD": "0", "SRD": "0", "BRL": "0"},
            initial,
        )
        self.assertIn("saldo_caixa", _TestCaixasDB._RUNTIME_CACHE)

        self.db.update_caixas_from_transaction(
            gold_transaction_id=999,
            tipo_operacao="compra",
            peso_gramas=Decimal("10"),
            pagamentos=[
                {"moeda": "EUR", "valor_moeda": "100"},
                {"moeda": "USD", "valor_moeda": "50"},
            ],
            pessoa="Test User",
        )

        self.assertNotIn("saldo_caixa", _TestCaixasDB._RUNTIME_CACHE)
        saldo = self.db.get_saldo_caixa()
        self.assertEqual("10", saldo["XAU"])
        self.assertEqual("-100", saldo["EUR"])
        self.assertEqual("-50", saldo["USD"])
        self.assertEqual("0", saldo["SRD"])
        self.assertEqual("0", saldo["BRL"])

        movimentacoes = self.client.store["caixas_movimentacoes"]
        self.assertEqual(3, len(movimentacoes))
        self.assertCountEqual(
            [(row["caixa_moeda"], row["tipo_operacao"], row["pessoa"]) for row in movimentacoes],
            [
                ("XAU", "compra", "Test User"),
                ("EUR", "compra", "Test User"),
                ("USD", "compra", "Test User"),
            ],
        )

    def test_venda_reverses_xau_and_payment_currency_balances(self) -> None:
        self.db.update_caixas_from_transaction(
            gold_transaction_id=1000,
            tipo_operacao="compra",
            peso_gramas=Decimal("10"),
            pagamentos=[
                {"moeda": "EUR", "valor_moeda": "100"},
                {"moeda": "USD", "valor_moeda": "50"},
            ],
            pessoa="Compra Base",
        )

        self.db.update_caixas_from_transaction(
            gold_transaction_id=1001,
            tipo_operacao="venda",
            peso_gramas=Decimal("4"),
            pagamentos=[{"moeda": "EUR", "valor_moeda": "80"}],
            pessoa="Venda Parcial",
        )

        saldo = self.db.get_saldo_caixa()
        self.assertEqual("6", saldo["XAU"])
        self.assertEqual("-20", saldo["EUR"])
        self.assertEqual("-50", saldo["USD"])
        self.assertEqual("0", saldo["SRD"])
        self.assertEqual("0", saldo["BRL"])

        movimentacoes = self.client.store["caixas_movimentacoes"]
        self.assertEqual(5, len(movimentacoes))
        self.assertEqual("venda", movimentacoes[-2]["tipo_operacao"])
        self.assertEqual("XAU", movimentacoes[-2]["caixa_moeda"])
        self.assertEqual("EUR", movimentacoes[-1]["caixa_moeda"])


class CaixasRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        _TestCaixasRebuildDB._CAIXAS_READY = None
        _TestCaixasRebuildDB._RUNTIME_CACHE = {}
        self.client = _FakeSupabaseClient()
        self.client.store.update(
            {
                "transacoes": [],
                "gold_transactions": [],
                "gold_payments": [],
            }
        )
        self.db = _TestCaixasRebuildDB(self.client)

    def test_backfill_tolerates_invalid_history_rows_without_losing_valid_totals(self) -> None:
        self.client.store["transacoes"] = [
            {"tipo_operacao": "compra", "ativo_id": 1, "quantidade": "3", "moeda_liquidacao": "USD", "valor_moeda": "30", "status": "registrada"},
            {"tipo_operacao": "compra", "ativo_id": "invalido", "quantidade": "quebrado", "moeda_liquidacao": "EUR", "valor_moeda": "oops", "status": "registrada"},
            {"tipo_operacao": "venda", "ativo_id": 1, "quantidade": "1", "moeda_liquidacao": "EUR", "valor_moeda": "15", "status": "registrada"},
        ]
        self.client.store["gold_transactions"] = [
            {"id": 10, "tipo_operacao": "compra", "peso": "2", "status": "registrada", "contexto": {"pagamentos": [{"moeda": "SRD", "valor_moeda": "999"}]}},
            {"id": 11, "tipo_operacao": "venda", "peso": "0.5", "status": "registrada", "contexto": {"pagamentos": [{"moeda": "BRL", "valor_moeda": "50"}]}},
        ]
        self.client.store["gold_payments"] = [
            {"gold_transaction_id": "invalido", "moeda": "USD", "valor_moeda": "abc"},
            {"gold_transaction_id": 10, "moeda": "SRD", "valor_moeda": "100"},
        ]
        _TestCaixasRebuildDB._RUNTIME_CACHE = {"saldo_caixa": {"USD": "999"}}

        result = self.db.backfill_caixas_from_history()

        self.assertEqual([], result["failed_updates"])
        self.assertNotIn("saldo_caixa", _TestCaixasRebuildDB._RUNTIME_CACHE)
        self.assertEqual(
            {"XAU": "3.5", "EUR": "15", "USD": "-30", "SRD": "-100", "BRL": "50"},
            result["after"],
        )

        saldo = self.db.get_saldo_caixa()
        self.assertEqual("3.5", saldo["XAU"])
        self.assertEqual("15", saldo["EUR"])
        self.assertEqual("-30", saldo["USD"])
        self.assertEqual("-100", saldo["SRD"])
        self.assertEqual("50", saldo["BRL"])


class TransferMoneyTests(unittest.TestCase):
    def test_insert_transfer_money_records_fx_and_journal_on_success(self) -> None:
        db = _TestTransferMoneyDB(_TransferMoneyClient())

        created = db.insert_transfer_money("EUR", "USD", Decimal("110"), Decimal("100"), Decimal("1.1"), Decimal("1"), "operador-1", taxa_servico_origem=Decimal("5"))

        self.assertIsNotNone(created)
        self.assertEqual(1, len(db.client.store["transfer_money_transactions"]))
        self.assertEqual([("USD", "EUR", Decimal("1.1"), "transfer_money")], db.fx_rates)
        self.assertEqual(1, len(db.journal_entries))
        self.assertTrue(any(line["account_code"] == "TRANSFER_FEE_REVENUE" for line in db.journal_entries[0]["lines"]))

    def test_insert_transfer_money_returns_none_on_empty_insert_and_logs_warning(self) -> None:
        db = _TestTransferMoneyDB(_TransferMoneyClient(insert_mode="empty"))

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            created = db.insert_transfer_money("EUR", "USD", Decimal("110"), Decimal("100"), Decimal("1.1"), Decimal("1"), "operador-1")

        self.assertIsNone(created)
        self.assertEqual([], db.fx_rates)
        self.assertEqual([], db.journal_entries)
        self.assertIn("resposta vazia", "\n".join(captured.output))

    def test_insert_transfer_money_rejects_fee_greater_than_origin(self) -> None:
        db = _TestTransferMoneyDB(_TransferMoneyClient())

        created = db.insert_transfer_money("EUR", "USD", Decimal("10"), Decimal("5"), Decimal("1.1"), Decimal("1"), "operador-1", taxa_servico_origem=Decimal("10"))

        self.assertIsNone(created)
        self.assertEqual([], db.client.store["transfer_money_transactions"])


if __name__ == "__main__":
    unittest.main()
