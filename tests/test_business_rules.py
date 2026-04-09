import asyncio
import app.main as main_module
import unittest
from decimal import Decimal
from typing import cast

from fastapi import HTTPException

from app.database import DatabaseClient, _aggregate_cliente_movements, _aggregate_cliente_movements_by_client, _hash_web_pin, _verify_web_pin
from app.main import (
    _MARKET_TICK_HISTORY,
    _build_fechamento_status,
    _build_gold_caixa_metrics,
    _build_gold_caixa_metrics_from_pending_grams,
    _build_open_lot_market_context,
    _build_operation_lot_market_context,
    _build_market_snapshot_from_rates,
    _build_market_trend_context,
    _build_operation_draft_from_message,
    _build_web_lot_ai_alert_summary,
    _build_web_lot_ai_alerts,
    _build_web_lot_monitor_entries,
    _build_lot_sell_signal,
    _build_fifo_inventory_lots,
    _display_cambio_for_web_input,
    _derive_forma_pagamento_summary,
    _extract_awesomeapi_gold_price,
    _extract_gold_api_xau_usd,
    _find_negative_caixa_balances,
    _parse_gold_trade_profile,
    _normalize_cambio_para_usd,
    _parse_decimal_from_text,
    _parse_operation_reference,
    _parse_web_payments_from_form,
    _preview_fifo_sale_consumption,
    _project_caixa_balances,
    _parse_google_news_feed,
    _build_saas_recent_fx_map,
    _should_reset_guided_session_for_message,
    _build_saas_statement_context,
    _build_gold_receipt_context,
    _build_inventory_status_report_payload,
    _build_lot_monitor_snapshot_payload,
    _build_admin_dashboard_html,
    _lot_monitor_stream_events,
    _render_saas_dashboard_html,
    _invalidate_statement_context_cache,
    _invalidate_recent_fx_map_cache,
    _invalidate_lot_monitor_snapshot_cache,
    _invalidate_reporting_cache,
    _invalidate_receipt_context_cache,
    _sum_open_fechamento_grams,
)


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []
        self._ilike_filters = []
        self._in_filters = []
        self._order_by = None
        self._limit = None
        self._pending_insert = None
        self._delete_mode = False
        self._selected_fields = None

    def select(self, _fields):
        self._selected_fields = _fields
        return self

    def order(self, field, desc=False):
        self._order_by = (field, desc)
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def ilike(self, field, value):
        self._ilike_filters.append((field, str(value)))
        return self

    def limit(self, value):
        self._limit = value
        return self

    def in_(self, field, values):
        self._in_filters.append((field, set(values)))
        return self

    def neq(self, _field, _value):
        return self

    def delete(self):
        self._delete_mode = True
        return self

    def insert(self, payload):
        if isinstance(payload, list):
            self._pending_insert = [dict(item) for item in payload]
        else:
            self._pending_insert = dict(payload)
        return self

    def update(self, payload):
        self._pending_insert = {"__update__": dict(payload)}
        return self

    def execute(self):
        if self._delete_mode:
            self.store[self.name] = []
            self._delete_mode = False
            return _FakeResponse([])

        if self._pending_insert is not None:
            pending = self._pending_insert
            self._pending_insert = None
            if isinstance(pending, dict) and "__update__" in pending:
                changes = cast(dict, pending["__update__"])
                rows = [dict(row) for row in self.store[self.name]]
                for field, value in self._filters:
                    rows = [row for row in rows if row.get(field) == value]
                updated_rows = []
                for row in self.store[self.name]:
                    if any(row.get(field) != value for field, value in self._filters):
                        continue
                    row.update(changes)
                    updated_rows.append(dict(row))
                self._filters = []
                return _FakeResponse(updated_rows)
            if isinstance(pending, list):
                rows = []
                for item in pending:
                    row = dict(item)
                    row["id"] = len(self.store[self.name]) + 1
                    self.store[self.name].append(row)
                    rows.append(row)
                return _FakeResponse(rows)
            row = dict(pending)
            row["id"] = len(self.store[self.name]) + 1
            self.store[self.name].append(row)
            return _FakeResponse([row])

        rows = [dict(row) for row in self.store[self.name]]
        for field, value in self._filters:
            rows = [row for row in rows if row.get(field) == value]
        if self._ilike_filters:
            self.store["_cliente_search_exec_count"] = int(self.store.get("_cliente_search_exec_count", 0)) + 1
        for field, pattern in self._ilike_filters:
            needle = pattern.replace("%", "").lower()
            rows = [row for row in rows if needle in str(row.get(field) or "").lower()]
        for field, values in self._in_filters:
            rows = [row for row in rows if row.get(field) in values]
        if self._order_by:
            field, desc = self._order_by
            rows = sorted(rows, key=lambda row: row.get(field), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        self._filters = []
        self._ilike_filters = []
        self._in_filters = []
        self._limit = None
        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self):
        self.store = {
            "clientes": [],
            "gold_transactions": [],
            "gold_inventory_lots": [],
            "gold_inventory_consumptions": [],
            "cliente_movimentacoes": [],
        }

    def table(self, name):
        return _FakeTable(self.store, name)


class _FakeMissingWebPinTable(_FakeTable):
    def execute(self):
        if self.name == "usuarios":
            self.store["_base_select_attempts"] = self.store.get("_base_select_attempts", 0) + 1
            if isinstance(self._pending_insert, dict) and "__update__" in self._pending_insert:
                changes = cast(dict, self._pending_insert["__update__"])
                if "web_pin_hash" in changes or "web_pin_updated_em" in changes:
                    self.store["_web_pin_update_attempts"] = self.store.get("_web_pin_update_attempts", 0) + 1
                    raise Exception("{'message': 'column usuarios.web_pin_hash does not exist', 'code': '42703'}")
        return super().execute()


class _FakeMissingWebPinSupabaseClient(_FakeSupabaseClient):
    def __init__(self):
        super().__init__()
        self.store["_base_select_attempts"] = 0
        self.store["_web_pin_update_attempts"] = 0
        self.store["usuarios"] = [
            {
                "id": 8,
                "nome": "Daniel",
                "telefone": "+5598991438754",
                "tipo_usuario": "admin",
                "ativo": True,
            }
        ]

    def table(self, name):
        return _FakeMissingWebPinTable(self.store, name)


class _FakeFXDB:
    def get_last_cambio_para_usd(self, moeda):
        rates = {
            "EUR": Decimal("0.88"),
            "SRD": Decimal("38"),
            "BRL": Decimal("5.20"),
        }
        return rates.get(str(moeda).upper())

    def get_last_cambio_para_usd_map(self, moedas):
        result = {"USD": Decimal("1")}
        for moeda in moedas:
            rate = self.get_last_cambio_para_usd(moeda)
            if rate is not None:
                result[str(moeda).upper()] = rate
        return result


class _FakeDraftDB(_FakeFXDB):
    def search_clientes(self, query, limit=5):
        items = [
            {"id": 7, "nome": "teste", "apelido": "", "telefone": "+5977000000", "documento": "", "observacoes": ""},
            {"id": 8, "nome": "Carlos Silva", "apelido": "carlos", "telefone": "+5977111111", "documento": "", "observacoes": ""},
        ]
        normalized_query = str(query or "").strip().lower()
        return [item for item in items if normalized_query and (normalized_query in str(item.get("nome", "")).lower() or normalized_query in str(item.get("apelido", "")).lower())][:limit]


class _FakeRecentFxDB(_FakeFXDB):
    def __init__(self) -> None:
        self.calls = 0

    def get_last_cambio_para_usd_map(self, moedas):
        self.calls += 1
        return super().get_last_cambio_para_usd_map(moedas)


class _FakeStatementDB:
    def __init__(self) -> None:
        self.calls = 0

    def get_extrato_transactions(self, start_iso, _end_iso):
        self.calls += 1
        return [
            {
                "id": 1,
                "tipo_operacao": "compra",
                "peso": "10",
                "preco_usd": "100",
                "valor_total": "1000",
                "criado_em": start_iso,
            }
        ]

    def get_gold_summary_range(self, _start_iso, _end_iso):
        return {
            "peso_entrada": Decimal("10"),
            "peso_saida": Decimal("0"),
            "total_compra": Decimal("1000"),
            "total_venda": Decimal("0"),
            "resultado": Decimal("0"),
        }

    def get_daily_gold_summary(self, _date_iso):
        return {
            "peso_entrada": Decimal("10"),
            "peso_saida": Decimal("0"),
            "total_compra": Decimal("1000"),
            "total_venda": Decimal("0"),
            "resultado": Decimal("0"),
        }


class _FakeReceiptDB:
    def __init__(self) -> None:
        self.audit_calls = 0
        self.client_calls = 0
        self.user_calls = 0

    def get_gold_operation_audit(self, operation_id):
        self.audit_calls += 1
        return {
            "operation": {
                "id": operation_id,
                "cliente_id": 3,
                "operador_id": "+5977000000",
                "tipo_operacao": "compra",
                "peso": "10",
                "teor": "90",
                "preco_usd": "100",
                "total_usd": "1000",
                "total_pago_usd": "1000",
                "fechamento_gramas": "10",
                "pessoa": "Ana",
                "criado_em": "2026-04-09T10:00:00+00:00",
            },
            "payments": [
                {"moeda": "USD", "valor_moeda": "1000", "valor_usd": "1000", "forma_pagamento": "dinheiro"},
            ],
            "inventory_consumptions": [],
        }

    def get_cliente_by_id(self, _cliente_id):
        self.client_calls += 1
        return {"id": 3, "nome": "Ana", "telefone": "+5977000000", "documento": "", "apelido": "", "observacoes": ""}

    def get_usuario_by_telefone(self, _telefone):
        self.user_calls += 1
        return {"nome": "Operador Teste"}


class _FakeLotMonitorDB:
    def __init__(self) -> None:
        self.inventory_calls = 0
        self.sync_calls = 0

    def get_gold_inventory_status(self, inventory_transactions=None, *, open_only=False):
        self.inventory_calls += 1
        return {
            "lots": [{"id": 10}],
            "open_lots": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "teor": "90"}],
            "has_any_lots": True,
        }

    def sync_gold_inventory_ledger(self):
        self.sync_calls += 1


class _FakeInventoryReportDB:
    def __init__(self) -> None:
        self.inventory_calls = 0

    def get_gold_inventory_status(self, inventory_transactions=None, *, open_only=False):
        self.inventory_calls += 1
        return {
            "lots": [{"id": 10}],
            "open_lots": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "teor": "90"}],
            "available_grams": "5",
            "inventory_cost_usd": "400",
            "avg_cost_usd_per_gram": "80",
            "has_any_lots": True,
        }

    def sync_gold_inventory_ledger(self):
        raise AssertionError("sync should not be needed in this test")

    def get_gold_inventory_transactions(self):
        raise AssertionError("fallback should not be needed in this test")


class _FakeAdminDashboardDB:
    def __init__(self) -> None:
        self.summary_calls = 0

    def get_daily_gold_summary(self, _start, _end):
        self.summary_calls += 1
        return {"total_operacoes": 1, "total_usd": "100", "total_pago_usd": "100", "total_diferenca_usd": "0"}

    def get_risk_alerts(self, _start, _end):
        return []

    def get_top_divergences(self, _start, _end, limit=5):
        return []

    def get_saldo_caixa(self):
        return {"XAU": "1", "USD": "2", "EUR": "3", "SRD": "4", "BRL": "5"}

    def get_recent_multi_agent_runs(self, limit=5):
        return []

    def get_gold_inventory_status(self, inventory_transactions=None, *, open_only=False):
        return {
            "lots": [{"id": 10}],
            "available_grams": "5",
            "inventory_cost_usd": "400",
            "avg_cost_usd_per_gram": "80",
            "open_lots": [{"source_transaction_id": 18, "remaining_grams": "5", "unit_cost_usd": "80"}],
            "has_any_lots": True,
        }

    def sync_gold_inventory_ledger(self):
        raise AssertionError("sync should not be needed in this test")

    def get_gold_inventory_transactions(self):
        raise AssertionError("fallback should not be needed in this test")

    def get_gold_pending_closure_grams(self):
        return Decimal("1.5")


class _ExplodingSaldoRenderDB:
    def get_saldo_caixa(self):
        raise AssertionError("get_saldo_caixa should not be called for this page")


class _ExplodingInventoryTransactionsRenderDB:
    def get_saldo_caixa(self):
        return {"XAU": "5", "USD": "2", "EUR": "3", "SRD": "4", "BRL": "5"}

    def get_gold_inventory_transactions(self):
        raise AssertionError("get_gold_inventory_transactions should not be called for this page")

    def get_gold_inventory_status(self, inventory_transactions=None, *, open_only=False):
        return {
            "lots": [{"id": 10, "status": "open"}],
            "open_lots": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "unit_cost_usd": "80", "teor": "90", "gold_type": "fundido", "quebra": "", "pessoa": "Ana"}],
            "available_grams": "5",
            "inventory_cost_usd": "400",
            "avg_cost_usd_per_gram": "80",
            "has_any_lots": True,
        }

    def sync_gold_inventory_ledger(self):
        raise AssertionError("sync should not be needed in this test")

    def get_gold_pending_closure_grams(self):
        return Decimal("2")

    def get_last_cambio_para_usd_map(self, moedas):
        return _FakeFXDB().get_last_cambio_para_usd_map(moedas)


class _FakeStreamingRequest:
    def __init__(self, disconnect_after: int = 2) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


class BusinessRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        _MARKET_TICK_HISTORY.clear()
        DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY = None
        DatabaseClient._RUNTIME_CACHE = {}
        _invalidate_statement_context_cache()
        _invalidate_recent_fx_map_cache()
        _invalidate_receipt_context_cache()
        _invalidate_lot_monitor_snapshot_cache()
        _invalidate_reporting_cache()

    def test_fifo_consumption_uses_oldest_purchase_first(self) -> None:
        transactions = [
            {"id": 1, "tipo_operacao": "compra", "peso": "100", "preco_usd": "70", "teor": "90", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"id": 2, "tipo_operacao": "compra", "peso": "50", "preco_usd": "80", "teor": "85", "criado_em": "2026-04-01T11:00:00+00:00"},
            {"id": 3, "tipo_operacao": "venda", "peso": "60", "preco_usd": "120", "criado_em": "2026-04-01T12:00:00+00:00"},
        ]
        lots = _build_fifo_inventory_lots(transactions)
        self.assertEqual(lots[0]["source_id"], 1)
        self.assertEqual(Decimal(str(lots[0]["remaining_grams"])), Decimal("40"))
        self.assertEqual(Decimal(str(lots[1]["remaining_grams"])), Decimal("50"))
        self.assertEqual(lots[0]["teor"], "90")
        self.assertEqual(lots[1]["teor"], "85")

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

    def test_parse_operation_reference_distinguishes_guided_ids(self) -> None:
        self.assertEqual(_parse_operation_reference("GT-24"), ("gold", 24))
        self.assertEqual(_parse_operation_reference("T-15"), ("transacao", 15))
        self.assertEqual(_parse_operation_reference("123"), ("transacao", 123))

    def test_parse_decimal_from_text_returns_invalid_sentinel_for_text(self) -> None:
        self.assertEqual(_parse_decimal_from_text("USD", "preco_usd"), Decimal("-1"))
        self.assertEqual(_parse_decimal_from_text("abc", "peso"), Decimal("-1"))

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
        db.client.store["gold_transactions"] = [
            {"id": 11, "tipo_operacao": "compra", "peso": "50", "preco_usd": "80", "teor": "85", "gold_type": "fundido", "criado_em": "2026-04-01T11:00:00+00:00", "status": "registrada"},
        ]
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
                "metadata": {},
            },
        ]

        status = db.get_gold_inventory_status()

        self.assertEqual(Decimal(str(status["available_grams"])), Decimal("30"))
        self.assertEqual(Decimal(str(status["inventory_cost_usd"])), Decimal("2400.00"))
        self.assertEqual(Decimal(str(status["avg_cost_usd_per_gram"])), Decimal("80.00"))
        self.assertEqual(len(status["open_lots"]), 1)
        self.assertEqual(status["open_lots"][0]["teor"], "85")

    def test_get_gold_inventory_status_skips_transaction_fallback_when_metadata_is_complete(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_inventory_lots"] = [
            {
                "id": 1,
                "source_transaction_id": 11,
                "created_at_tx": "2026-04-01T11:00:00+00:00",
                "initial_grams": "50",
                "remaining_grams": "30",
                "unit_cost_usd": "80",
                "total_cost_usd": "4000",
                "status": "open",
                "metadata": {"teor": "85", "gold_type": "fundido", "quebra": "", "pessoa": "Ana"},
            },
        ]
        db.get_gold_inventory_transactions = lambda: (_ for _ in ()).throw(AssertionError("fallback should not run"))

        status = db.get_gold_inventory_status(open_only=True)

        self.assertEqual(len(status["open_lots"]), 1)
        self.assertEqual(status["open_lots"][0]["pessoa"], "Ana")

    def test_get_gold_inventory_status_open_only_preserves_has_any_lots_when_only_closed_lots_exist(self) -> None:
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
                "metadata": {},
            },
        ]

        status = db.get_gold_inventory_status(open_only=True)

        self.assertEqual(status["open_lots"], [])
        self.assertTrue(bool(status["has_any_lots"]))

    def test_get_gold_pending_closure_grams_sums_only_open_partial_amounts(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_transactions"] = [
            {"peso": "100", "fechamento_gramas": "40", "fechamento_tipo": "parcial", "status": "registrada"},
            {"peso": "50", "fechamento_gramas": "50", "fechamento_tipo": "total", "status": "registrada"},
            {"peso": "20", "fechamento_gramas": "0", "fechamento_tipo": "parcial", "status": "cancelada"},
            {"peso": "30", "fechamento_gramas": "10", "fechamento_tipo": "parcial", "status": "registrada"},
        ]

        pending = db.get_gold_pending_closure_grams()

        self.assertEqual(pending, Decimal("80"))

    def test_open_lot_market_context_tracks_lot_value_by_teor(self) -> None:
        context = _build_open_lot_market_context(
            [
                {"source_id": 1, "remaining_grams": "100", "unit_cost_usd": "70", "teor": "90", "criado_em": "2026-04-01T10:00:00+00:00"},
                {"source_id": 2, "remaining_grams": "100", "unit_cost_usd": "70", "teor": "85", "criado_em": "2026-04-01T10:00:00+00:00"},
            ],
            {"xau_usd_raw": "3103.50"},
        )

        self.assertEqual(context["available_fine_grams"], "175.00")
        self.assertEqual(len(context["by_teor"]), 2)
        self.assertEqual(context["by_teor"][0]["teor"], "90")

    def test_operation_lot_market_context_matches_operation_summary_fields(self) -> None:
        open_lots = [
            {"source_id": 1, "remaining_grams": "100", "unit_cost_usd": "70", "teor": "90", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"source_id": 2, "remaining_grams": "80", "unit_cost_usd": "95", "teor": "85", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"source_id": 3, "remaining_grams": "60", "unit_cost_usd": "110", "teor": "80", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"source_id": 4, "remaining_grams": "40", "unit_cost_usd": "60", "teor": "75", "criado_em": "2026-04-01T10:00:00+00:00"},
            {"source_id": 5, "remaining_grams": "20", "unit_cost_usd": "120", "teor": "70", "criado_em": "2026-04-01T10:00:00+00:00"},
        ]
        market_snapshot = {"xau_usd_raw": "3103.50"}

        full_context = _build_open_lot_market_context(open_lots, market_snapshot)
        operation_context = _build_operation_lot_market_context(open_lots, market_snapshot)

        self.assertEqual(operation_context["available_fine_grams"], full_context["available_fine_grams"])
        self.assertEqual(operation_context["market_value_usd"], full_context["market_value_usd"])
        self.assertEqual(operation_context["unrealized_pnl_usd"], full_context["unrealized_pnl_usd"])
        self.assertEqual(operation_context["by_teor"], full_context["by_teor"])
        expected_risk_ids = [
            item.get("source_transaction_id", item.get("source_id"))
            for item in sorted(cast(list[dict], full_context["lots"]), key=lambda item: Decimal(str(item.get("unrealized_pnl_usd") or "0")))[:4]
        ]
        self.assertEqual(
            [item.get("source_transaction_id", item.get("source_id")) for item in operation_context["risk_lots"]],
            expected_risk_ids,
        )

    def test_market_trend_context_detects_bullish_window(self) -> None:
        for value in ["3000", "3010", "3020", "3030", "3045", "3060", "3075"]:
            _MARKET_TICK_HISTORY.append({"xau_usd_raw": value})
        trend = _build_market_trend_context()
        self.assertIn(trend["signal"], {"bullish", "constructive"})

    def test_lot_sell_signal_uses_target_or_trend(self) -> None:
        lot = {
            "unit_cost_usd": "80",
            "market_unit_usd": "95",
            "unrealized_pnl_usd": "300",
            "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5", "notify_phone": "+5977000000"}},
        }
        signal = _build_lot_sell_signal(lot, {"signal": "constructive"})
        self.assertTrue(signal["should_alert"])
        self.assertEqual(signal["status"], "limite_atingido")

    def test_lot_sell_signal_protects_profit_on_bearish_turn(self) -> None:
        lot = {
            "unit_cost_usd": "80",
            "market_unit_usd": "84",
            "unrealized_pnl_usd": "40",
            "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5", "notify_phone": "+5977000000"}},
        }
        signal = _build_lot_sell_signal(lot, {"signal": "bearish"})
        self.assertTrue(signal["should_alert"])
        self.assertEqual(signal["status"], "proteger_lucro")

    def test_web_lot_ai_alerts_include_only_enabled_triggered_monitors(self) -> None:
        alerts = _build_web_lot_ai_alerts(
            {
                "lots": [
                    {
                        "id": 170,
                        "source_transaction_id": 26,
                        "remaining_grams": "10",
                        "market_unit_usd": "95",
                        "unrealized_pnl_usd": "300",
                        "unit_cost_usd": "80",
                        "teor": "90",
                        "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5"}},
                    },
                    {
                        "id": 171,
                        "source_transaction_id": 27,
                        "remaining_grams": "10",
                        "market_unit_usd": "82",
                        "unrealized_pnl_usd": "20",
                        "unit_cost_usd": "80",
                        "teor": "90",
                        "metadata": {"monitor": {"enabled": False, "target_price_usd": "81", "min_profit_pct": "1"}},
                    },
                ]
            },
            {"signal": "constructive"},
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["lot_id"], 170)
        self.assertEqual(alerts[0]["status"], "limite_atingido")
        self.assertIn("GT-26", _build_web_lot_ai_alert_summary(alerts))

    def test_web_lot_monitor_entries_include_live_card_fields(self) -> None:
        entries = _build_web_lot_monitor_entries(
            {
                "lots": [
                    {
                        "id": 170,
                        "source_transaction_id": 26,
                        "remaining_grams": "10",
                        "market_unit_usd": "95",
                        "unrealized_pnl_usd": "300",
                        "unit_cost_usd": "80",
                        "teor": "90",
                        "metadata": {"monitor": {"enabled": True, "target_price_usd": "90", "min_profit_pct": "5", "notify_phone": "+5977000000"}},
                    },
                ]
            },
            {"signal": "constructive"},
            default_alert_phone="+59711111111",
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], 170)
        self.assertEqual(entries[0]["status"], "limite_atingido")
        self.assertEqual(entries[0]["notify_phone"], "+5977000000")
        self.assertEqual(entries[0]["market_unit_usd"], "95")
        self.assertEqual(entries[0]["entry_unit_usd"], "80.00")
        self.assertEqual(entries[0]["target_gap_usd"], "-5.00")
        self.assertEqual(entries[0]["trend_label"], "Alta moderada")
        self.assertEqual(entries[0]["min_profit_gap_pct"], "13.75")

    def test_web_lot_monitor_view_model_evaluates_each_lot_once(self) -> None:
        original_build_lot_sell_signal = main_module._build_lot_sell_signal
        seen_ids = []

        def _fake_build_lot_sell_signal(lot, _trend):
            seen_ids.append(int(lot.get("id") or 0))
            return {
                "enabled": True,
                "status": "limite_atingido",
                "status_class": "positive",
                "reason": "Meta tocada.",
                "profit_pct": "10",
                "min_profit_gap_pct": "2",
                "notify_phone": "+5977000000",
                "target_price_usd": "90",
                "min_profit_pct": "5",
                "alert_signature": f"sig-{lot.get('id')}",
            }

        try:
            main_module._build_lot_sell_signal = _fake_build_lot_sell_signal
            model = main_module._build_web_lot_monitor_view_model(
                {
                    "lots": [
                        {"id": 170, "source_transaction_id": 26, "remaining_grams": "10", "market_unit_usd": "95", "unrealized_pnl_usd": "300", "unit_cost_usd": "80", "teor": "90"},
                        {"id": 171, "source_transaction_id": 27, "remaining_grams": "8", "market_unit_usd": "92", "unrealized_pnl_usd": "120", "unit_cost_usd": "82", "teor": "85"},
                    ]
                },
                {"signal": "constructive"},
                default_alert_phone="+59711111111",
                entry_limit=24,
                alert_limit=4,
            )
        finally:
            main_module._build_lot_sell_signal = original_build_lot_sell_signal

        self.assertEqual(seen_ids, [170, 171])
        self.assertEqual(len(model["entries"]), 2)
        self.assertEqual(len(model["alerts"]), 2)
        self.assertIn("monitores ativos", model["summary"])

    def test_parse_google_news_feed_extracts_items(self) -> None:
        xml_text = """
        <rss><channel>
            <item><title>Ouro sobe com dolar fraco</title><link>https://example.com/a</link><pubDate>Wed, 08 Apr 2026 10:00:00 GMT</pubDate><source>Fonte X</source></item>
        </channel></rss>
        """
        items = _parse_google_news_feed(xml_text, "ouro")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["topic"], "ouro")

    def test_update_gold_inventory_lot_monitor_persists_metadata(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_inventory_lots"] = [
            {"id": 4, "metadata": {"source": "sync"}},
        ]
        updated = db.update_gold_inventory_lot_monitor(4, {"enabled": True, "target_price_usd": "95"})
        self.assertIsNotNone(updated)
        self.assertEqual(db.client.store["gold_inventory_lots"][0]["metadata"]["monitor"]["target_price_usd"], "95")

    def test_get_gold_inventory_transactions_ignores_cancelled_rows(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_transactions"] = [
            {"id": 1, "tipo_operacao": "compra", "peso": "10", "preco_usd": "100", "criado_em": "2026-04-01T10:00:00+00:00", "status": "registrada"},
            {"id": 2, "tipo_operacao": "venda", "peso": "5", "preco_usd": "120", "criado_em": "2026-04-01T11:00:00+00:00", "status": "cancelada"},
        ]

        rows = db.get_gold_inventory_transactions()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 1)

    def test_web_pin_hash_roundtrip(self) -> None:
        pin_hash = _hash_web_pin("123456", salt="fixed-salt")

        self.assertTrue(_verify_web_pin("123456", pin_hash))
        self.assertFalse(_verify_web_pin("654321", pin_hash))

    def test_get_usuario_web_auth_caches_missing_web_pin_schema(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeMissingWebPinSupabaseClient()
        DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY = None

        usuario = db.get_usuario_web_auth("+5598991438754")
        usuario_repeat = db.get_usuario_web_auth("+5598991438754")

        self.assertIsNotNone(usuario)
        self.assertFalse(bool(usuario["web_pin_schema_ready"]))
        self.assertFalse(bool(usuario["web_pin_hash"]))
        self.assertFalse(DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY)
        self.assertIsNotNone(usuario_repeat)
        self.assertEqual(db.client.store["_base_select_attempts"], 2)

    def test_set_usuario_web_pin_short_circuits_when_schema_missing(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeMissingWebPinSupabaseClient()
        DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY = False

        result = db.set_usuario_web_pin("+5598991438754", "123456")

        self.assertIsNotNone(result)
        self.assertFalse(bool(result["web_pin_schema_ready"]))

    def test_parse_web_payments_supports_multi_currency_rows(self) -> None:
        pagamentos = _parse_web_payments_from_form(
            _FakeFXDB(),
            {
                "payment_1_moeda": "USD",
                "payment_1_valor": "100",
                "payment_1_cambio": "1",
                "payment_1_forma": "dinheiro",
                "payment_2_moeda": "SRD",
                "payment_2_valor": "380",
                "payment_2_cambio": "",
                "payment_2_forma": "transferencia",
            },
        )

        self.assertEqual(len(pagamentos), 2)
        self.assertEqual(pagamentos[0]["moeda"], "USD")
        self.assertEqual(Decimal(str(pagamentos[1]["valor_usd"])), Decimal("10.00"))
        self.assertEqual(_derive_forma_pagamento_summary(pagamentos), "misto")

    def test_normalize_cambio_para_usd_inverts_manual_eur_input(self) -> None:
        self.assertEqual(_normalize_cambio_para_usd("EUR", Decimal("1.25")), Decimal("0.8000"))

    def test_display_cambio_for_web_input_inverts_internal_eur_rate(self) -> None:
        self.assertEqual(_display_cambio_for_web_input("EUR", Decimal("0.8000")), "1.25")

    def test_parse_web_payments_respects_manual_eur_rate(self) -> None:
        pagamentos = _parse_web_payments_from_form(
            _FakeFXDB(),
            {
                "payment_1_moeda": "EUR",
                "payment_1_valor": "100",
                "payment_1_cambio": "1.25",
                "payment_1_forma": "dinheiro",
            },
        )

        self.assertEqual(Decimal(str(pagamentos[0]["cambio_para_usd"])), Decimal("0.8000"))
        self.assertEqual(Decimal(str(pagamentos[0]["valor_usd"])), Decimal("125.00"))

    def test_aggregate_cliente_movements_sums_balances_by_currency(self) -> None:
        balances = _aggregate_cliente_movements(
            [
                {"moeda": "XAU", "valor": "12.5"},
                {"moeda": "XAU", "valor": "-3.0"},
                {"moeda": "USD", "valor": "150"},
            ]
        )

        self.assertEqual(balances["XAU"], Decimal("9.5"))
        self.assertEqual(balances["USD"], Decimal("150"))
        self.assertEqual(balances["EUR"], Decimal("0"))

    def test_aggregate_cliente_movements_by_client_groups_balances(self) -> None:
        balances_by_client = _aggregate_cliente_movements_by_client(
            [
                {"cliente_id": 3, "moeda": "XAU", "valor": "4.25"},
                {"cliente_id": 3, "moeda": "USD", "valor": "150"},
                {"cliente_id": 4, "moeda": "XAU", "valor": "1.75"},
                {"cliente_id": 3, "moeda": "XAU", "valor": "-1.00"},
            ]
        )

        self.assertEqual(balances_by_client[3]["XAU"], Decimal("3.25"))
        self.assertEqual(balances_by_client[3]["USD"], Decimal("150"))
        self.assertEqual(balances_by_client[4]["XAU"], Decimal("1.75"))

    def test_list_clientes_with_balances_uses_batch_balance_lookup(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": 1, "nome": "Ana", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
            {"id": 2, "nome": "Bruno", "ativo": True, "atualizado_em": "2026-04-09T09:00:00+00:00"},
        ]
        db.client.store["cliente_movimentacoes"] = [
            {"cliente_id": 1, "moeda": "XAU", "valor": "2.5"},
            {"cliente_id": 1, "moeda": "USD", "valor": "100"},
            {"cliente_id": 2, "moeda": "XAU", "valor": "1.0"},
        ]

        items = db.list_clientes_with_balances(limit=10)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["balances"]["XAU"], "2.5")
        self.assertEqual(items[0]["balances"]["USD"], "100")
        self.assertEqual(items[1]["balances"]["XAU"], "1.0")

    def test_list_clientes_with_balances_cache_is_invalidated_after_cliente_movement(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": 1, "nome": "Ana", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
        ]
        db.client.store["cliente_movimentacoes"] = [
            {"cliente_id": 1, "moeda": "XAU", "valor": "2.5"},
        ]

        first = db.list_clientes_with_balances(limit=10)
        self.assertEqual(first[0]["balances"]["XAU"], "2.5")

        db.record_cliente_operation_balance(
            cliente_id=1,
            gold_transaction_id=99,
            tipo_operacao="compra",
            pending_grams=Decimal("1.0"),
            pessoa="Ana",
        )

        second = db.list_clientes_with_balances(limit=10)
        self.assertEqual(second[0]["balances"]["XAU"], "3.5")

    def test_search_clientes_uses_cache_for_repeated_query(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": 1, "nome": "Ana Paula", "apelido": "ana", "telefone": "+5977000000", "documento": "ABC", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
            {"id": 2, "nome": "Bruno", "apelido": "bru", "telefone": "+5977111111", "documento": "XYZ", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T09:00:00+00:00"},
        ]

        first = db.search_clientes("Ana", limit=8)
        first_exec_count = int(db.client.store.get("_cliente_search_exec_count", 0))
        second = db.search_clientes("Ana", limit=8)

        self.assertEqual(len(first), 1)
        self.assertEqual(second[0]["nome"], "Ana Paula")
        self.assertGreater(first_exec_count, 0)
        self.assertEqual(int(db.client.store.get("_cliente_search_exec_count", 0)), first_exec_count)

    def test_search_clientes_cache_is_invalidated_after_create_cliente(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": 1, "nome": "Ana Paula", "apelido": "ana", "telefone": "+5977000000", "documento": "ABC", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
        ]

        first = db.search_clientes("Ana", limit=8)
        first_exec_count = int(db.client.store.get("_cliente_search_exec_count", 0))
        self.assertEqual(len(first), 1)

        created = db.create_cliente("Ana Maria")
        self.assertIsNotNone(created)

        second = db.search_clientes("Ana", limit=8)
        self.assertEqual(len(second), 2)
        self.assertGreater(int(db.client.store.get("_cliente_search_exec_count", 0)), first_exec_count)

    def test_list_clientes_with_balances_search_cache_is_invalidated_after_cliente_movement(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": 1, "nome": "Ana", "apelido": "", "telefone": "+5977000000", "documento": "", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"},
        ]
        db.client.store["cliente_movimentacoes"] = [
            {"cliente_id": 1, "moeda": "XAU", "valor": "2.5"},
        ]

        first = db.list_clientes_with_balances(search="Ana", limit=10)
        self.assertEqual(first[0]["balances"]["XAU"], "2.5")

        db.record_cliente_operation_balance(
            cliente_id=1,
            gold_transaction_id=99,
            tipo_operacao="compra",
            pending_grams=Decimal("1.0"),
            pessoa="Ana",
        )

        second = db.list_clientes_with_balances(search="Ana", limit=10)
        self.assertEqual(second[0]["balances"]["XAU"], "3.5")

    def test_record_cliente_operation_balance_tracks_pending_gold_direction(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()

        db.record_cliente_operation_balance(
            cliente_id=3,
            gold_transaction_id=77,
            tipo_operacao="compra",
            pending_grams=Decimal("4.25"),
            pessoa="Joao",
        )
        db.record_cliente_operation_balance(
            cliente_id=3,
            gold_transaction_id=78,
            tipo_operacao="venda",
            pending_grams=Decimal("1.25"),
            pessoa="Joao",
        )

        rows = db.client.store["cliente_movimentacoes"]
        self.assertEqual(Decimal(str(rows[0]["valor"])), Decimal("4.25"))
        self.assertEqual(Decimal(str(rows[1]["valor"])), Decimal("-1.25"))

    def test_get_cliente_account_snapshot_cache_is_invalidated_by_cliente_movement(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [
            {"id": 3, "nome": "Joao", "ativo": True},
        ]
        db.client.store["cliente_movimentacoes"] = [
            {"id": 1, "cliente_id": 3, "moeda": "XAU", "valor": "2.50", "criado_em": "2026-04-09T10:00:00+00:00"},
        ]

        snapshot_before = db.get_cliente_account_snapshot(3)
        self.assertIsNotNone(snapshot_before)
        self.assertEqual(snapshot_before["balances"]["XAU"], "2.50")

        db.record_cliente_operation_balance(
            cliente_id=3,
            gold_transaction_id=91,
            tipo_operacao="compra",
            pending_grams=Decimal("1.25"),
            pessoa="Joao",
        )

        snapshot_after = db.get_cliente_account_snapshot(3)
        self.assertIsNotNone(snapshot_after)
        self.assertEqual(snapshot_after["balances"]["XAU"], "3.75")

    def test_build_saas_statement_context_uses_cache_until_invalidated(self) -> None:
        db = _FakeStatementDB()

        first = _build_saas_statement_context(db, "2026-04-09", "2026-04-09")
        second = _build_saas_statement_context(db, "2026-04-09", "2026-04-09")

        self.assertEqual(db.calls, 1)
        self.assertEqual(first["summary"], second["summary"])

        _invalidate_statement_context_cache()

        third = _build_saas_statement_context(db, "2026-04-09", "2026-04-09")
        self.assertEqual(db.calls, 2)
        self.assertEqual(third["transactions"][0]["id"], 1)

    def test_build_saas_recent_fx_map_uses_cache_until_invalidated(self) -> None:
        db = _FakeRecentFxDB()

        first = _build_saas_recent_fx_map(db)
        second = _build_saas_recent_fx_map(db)

        self.assertEqual(db.calls, 1)
        self.assertEqual(first["EUR"], second["EUR"])

        _invalidate_recent_fx_map_cache()

        third = _build_saas_recent_fx_map(db)
        self.assertEqual(db.calls, 2)
        self.assertEqual(third["USD"], "1")

    def test_build_gold_receipt_context_uses_cache_until_invalidated(self) -> None:
        db = _FakeReceiptDB()

        first = _build_gold_receipt_context(db, 18)
        second = _build_gold_receipt_context(db, 18)

        self.assertEqual(db.audit_calls, 1)
        self.assertEqual(db.client_calls, 1)
        self.assertEqual(db.user_calls, 1)
        self.assertEqual(first["operation_id"], second["operation_id"])

        _invalidate_receipt_context_cache()

        third = _build_gold_receipt_context(db, 18)
        self.assertEqual(db.audit_calls, 2)
        self.assertEqual(third["operation_id"], 18)

    def test_build_lot_monitor_snapshot_payload_uses_cache_until_invalidated(self) -> None:
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

            self.assertEqual(db.inventory_calls, 1)
            self.assertEqual(first["updated_at_label"], "agora")
            self.assertEqual(second["lots"][0]["notify_phone"], "+5977000000")

            _invalidate_lot_monitor_snapshot_cache()

            third = _build_lot_monitor_snapshot_payload(db, {"telefone": "+5977000000"})
            self.assertEqual(db.inventory_calls, 2)
            self.assertEqual(third["alerts"][0]["signature"], "sig-1")
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_market_trend_context = original_build_market_trend_context
            main_module._build_open_lot_market_context = original_build_open_lot_market_context
            main_module._build_web_lot_monitor_view_model = original_build_web_lot_monitor_view_model

    def test_render_saas_dashboard_clients_page_skips_saldo_lookup(self) -> None:
        html = _render_saas_dashboard_html(
            _ExplodingSaldoRenderDB(),
            {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"},
            current_page="clients",
            clients_context={"search_term": "", "clients": [], "selected_account": None},
        )

        self.assertIn("Base de Clientes", html)

    def test_render_saas_dashboard_statement_page_skips_saldo_lookup(self) -> None:
        html = _render_saas_dashboard_html(
            _ExplodingSaldoRenderDB(),
            {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"},
            current_page="statement",
            statement_context={
                "start_date": "2026-04-09",
                "end_date": "2026-04-09",
                "label": "Hoje (2026-04-09)",
                "summary": {"total_operacoes": 0, "total_usd": "0"},
                "transactions": [],
                "statement_text": "",
            },
        )

        self.assertIn("Extrato Operacional", html)

    def test_render_saas_dashboard_operation_page_skips_inventory_transaction_lookup(self) -> None:
        original_get_market_snapshot = main_module._get_market_snapshot
        original_build_open_lot_market_context = main_module._build_open_lot_market_context
        try:
            main_module._get_market_snapshot = lambda: {"xau_usd_raw": "3103.50", "grama_ref_raw": "89.80", "usd_brl_raw": "5.50", "eur_usd_raw": "1.10", "eur_brl_raw": "6.05", "xau_source": "test", "xau_source_label": "Teste", "status": "ok", "updated_at_label": "agora"}
            main_module._build_open_lot_market_context = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full lot market context should not be built for operation page"))
            html = _render_saas_dashboard_html(
                _ExplodingInventoryTransactionsRenderDB(),
                {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"},
                current_page="operation",
            )
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_open_lot_market_context = original_build_open_lot_market_context

        self.assertIn("Registro de Operacao", html)
        self.assertIn("Ouro de terceiros pendente", html)

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
            html = _render_saas_dashboard_html(
                _ExplodingInventoryTransactionsRenderDB(),
                {"nome": "Ana", "telefone": "+5977000000", "tipo_usuario": "admin"},
                current_page="monitors",
            )
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_web_lot_ai_alerts = original_build_web_lot_ai_alerts
            main_module._build_web_lot_monitor_entries = original_build_web_lot_monitor_entries
            main_module._build_web_lot_monitor_view_model = original_build_web_lot_monitor_view_model

        self.assertIn("Monitores IA dos Lotes", html)
        self.assertIn("Gatilhos Ativos", html)

    def test_lot_monitor_stream_reuses_passed_db(self) -> None:
        db = object()
        request = _FakeStreamingRequest(disconnect_after=2)
        original_builder = main_module._build_lot_monitor_snapshot_payload
        original_interval = main_module._LOT_MONITOR_STREAM_INTERVAL_SECONDS
        seen_db_ids = []

        async def _collect_events() -> list[str]:
            events = []
            async for item in _lot_monitor_stream_events(request, {"telefone": "+5977000000"}, db):
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
        self.assertEqual(seen_db_ids, [id(db), id(db)])

    def test_get_db_reuses_singleton_database_client(self) -> None:
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

    def test_build_inventory_status_report_payload_uses_cache_until_invalidated(self) -> None:
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

        _invalidate_reporting_cache()

        original_get_market_snapshot = main_module._get_market_snapshot
        original_build_open_lot_market_context = main_module._build_open_lot_market_context
        try:
            main_module._get_market_snapshot = lambda: {"updated_at_label": "agora"}
            main_module._build_open_lot_market_context = lambda open_lots, _snapshot: {"available_fine_grams": "4.5", "market_value_usd": "450", "unrealized_pnl_usd": "50", "by_teor": [], "lots": open_lots}
            third = _build_inventory_status_report_payload(db)
        finally:
            main_module._get_market_snapshot = original_get_market_snapshot
            main_module._build_open_lot_market_context = original_build_open_lot_market_context

        self.assertEqual(db.inventory_calls, 2)
        self.assertEqual(third["ledger_mode"], "persisted")

    def test_build_admin_dashboard_html_uses_cache_until_invalidated(self) -> None:
        db = _FakeAdminDashboardDB()

        first = _build_admin_dashboard_html(db)
        second = _build_admin_dashboard_html(db)

        self.assertEqual(db.summary_calls, 1)
        self.assertIn("Caixa Admin Dashboard", first)
        self.assertIn("Resumo Diario", second)

        _invalidate_reporting_cache()

        third = _build_admin_dashboard_html(db)
        self.assertEqual(db.summary_calls, 2)
        self.assertIn("Estoque Ouro", third)

    def test_build_fechamento_status_tracks_open_grams_for_partial(self) -> None:
        status = _build_fechamento_status(
            {
                "peso": "100",
                "fechamento_gramas": "40",
                "fechamento_tipo": "parcial",
            }
        )

        self.assertTrue(bool(status["is_partial"]))
        self.assertEqual(Decimal(str(status["fechado"])), Decimal("40"))
        self.assertEqual(Decimal(str(status["aberto"])), Decimal("60"))

    def test_sum_open_fechamento_grams_uses_only_pending_open_amounts(self) -> None:
        total = _sum_open_fechamento_grams(
            [
                {"peso": "100", "fechamento_gramas": "40", "fechamento_tipo": "parcial"},
                {"peso": "50", "fechamento_gramas": "50", "fechamento_tipo": "total"},
                {"peso": "30", "fechamento_gramas": "10", "fechamento_tipo": "parcial"},
            ]
        )

        self.assertEqual(total, Decimal("80"))

    def test_build_gold_caixa_metrics_separates_owned_and_pending_gold(self) -> None:
        metrics = _build_gold_caixa_metrics(
            Decimal("1000"),
            [
                {"tipo_operacao": "compra", "peso": "1000", "fechamento_gramas": "700", "fechamento_tipo": "parcial"},
            ],
        )

        self.assertEqual(metrics["ouro_em_caixa"], Decimal("1000"))
        self.assertEqual(metrics["ouro_pendente"], Decimal("300"))
        self.assertEqual(metrics["ouro_proprio"], Decimal("700"))

    def test_build_gold_caixa_metrics_from_pending_grams_separates_owned_gold(self) -> None:
        metrics = _build_gold_caixa_metrics_from_pending_grams(Decimal("1000"), Decimal("300"))

        self.assertEqual(metrics["ouro_em_caixa"], Decimal("1000"))
        self.assertEqual(metrics["ouro_pendente"], Decimal("300"))
        self.assertEqual(metrics["ouro_proprio"], Decimal("700"))

    def test_parse_gold_trade_profile_requires_quebra_for_queimado_purchase(self) -> None:
        gold_type, quebra = _parse_gold_trade_profile("compra", "queimado", "3.5")

        self.assertEqual(gold_type, "queimado")
        self.assertEqual(quebra, Decimal("3.50"))

        with self.assertRaises(HTTPException):
            _parse_gold_trade_profile("compra", "queimado", "")

    def test_market_snapshot_from_rates_derives_gram_and_cross_rate(self) -> None:
        snapshot = _build_market_snapshot_from_rates(
            Decimal("3103.50"),
            Decimal("5.5000"),
            Decimal("1.1000"),
            None,
        )

        self.assertEqual(snapshot["grama_ref"], Decimal("89.80"))
        self.assertEqual(snapshot["eur_brl"], Decimal("6.0500"))
        self.assertEqual(snapshot["usd_brl"], Decimal("5.5000"))

    def test_extract_gold_api_xau_usd_reads_primary_payload(self) -> None:
        price = _extract_gold_api_xau_usd({"price": 3025.45})

        self.assertEqual(price, Decimal("3025.45"))

    def test_extract_awesomeapi_gold_price_reads_xauusd_bid(self) -> None:
        price = _extract_awesomeapi_gold_price(
            {
                "XAUUSD": {
                    "bid": "4801.06",
                    "ask": "4801.84",
                }
            }
        )

        self.assertEqual(price, Decimal("4801.06"))

    def test_operation_draft_extracts_gold_type_and_quebra(self) -> None:
        result = _build_operation_draft_from_message(
            _FakeDraftDB(),
            {"telefone": "+59711111111"},
            "comprei ouro queimado 12,4g teor 91,6 a 104 usd de Joao com quebra 3,5% pago em 300 USD e 7600 SRD",
        )

        draft = result["draft"]
        self.assertEqual(draft["gold_type"], "queimado")
        self.assertEqual(draft["quebra"], "3.5")
        self.assertEqual(draft["pessoa"], "Joao")
        self.assertEqual(draft["preco_usd"], "104")
        self.assertIn("Material: queimado (3.5% quebra)", result["summary"])
        self.assertIn("Cliente: Joao", result["summary"])

    def test_operation_draft_ignores_material_when_extracting_client_name(self) -> None:
        result = _build_operation_draft_from_message(
            _FakeDraftDB(),
            {"telefone": "+59711111111"},
            "comprei 100 gramas de ouro fundido a 120 dolares do cliente teste",
        )

        self.assertEqual(result["draft"]["pessoa"], "teste")
        self.assertEqual(result["draft"]["preco_usd"], "120")
        self.assertEqual(result["draft"]["cliente_id"], "7")
        self.assertIn("CL-000007", result["draft"]["cliente_lookup_meta"])

    def test_operation_draft_extracts_price_when_phrase_uses_por(self) -> None:
        result = _build_operation_draft_from_message(
            _FakeDraftDB(),
            {"telefone": "+59711111111"},
            "compra 50 g ouro fundido por 135 dolares do cliente carlos",
        )

        self.assertEqual(result["draft"]["preco_usd"], "135")
        self.assertEqual(result["draft"]["pessoa"], "Carlos Silva")
        self.assertEqual(result["draft"]["cliente_id"], "8")


if __name__ == "__main__":
    unittest.main()
