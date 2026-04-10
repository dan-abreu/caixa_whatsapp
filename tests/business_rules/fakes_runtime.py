from decimal import Decimal


class _FakeFXDB:
    def get_last_cambio_para_usd(self, moeda):
        rates = {"EUR": Decimal("0.88"), "SRD": Decimal("38"), "BRL": Decimal("5.20")}
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
        return [{"id": 1, "tipo_operacao": "compra", "peso": "10", "preco_usd": "100", "valor_total": "1000", "criado_em": start_iso}]

    def get_gold_summary_range(self, _start_iso, _end_iso):
        return {"peso_entrada": Decimal("10"), "peso_saida": Decimal("0"), "total_compra": Decimal("1000"), "total_venda": Decimal("0"), "resultado": Decimal("0")}

    def get_daily_gold_summary(self, _date_iso):
        return self.get_gold_summary_range(None, None)


class _FakeReceiptDB:
    def __init__(self) -> None:
        self.audit_calls = 0
        self.client_calls = 0
        self.user_calls = 0

    def get_gold_operation_audit(self, operation_id):
        self.audit_calls += 1
        return {"operation": {"id": operation_id, "cliente_id": 3, "operador_id": "+5977000000", "tipo_operacao": "compra", "peso": "10", "teor": "90", "preco_usd": "100", "total_usd": "1000", "total_pago_usd": "1000", "fechamento_gramas": "10", "pessoa": "Ana", "criado_em": "2026-04-09T10:00:00+00:00"}, "payments": [{"moeda": "USD", "valor_moeda": "1000", "valor_usd": "1000", "forma_pagamento": "dinheiro"}], "inventory_consumptions": []}

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
        return {"lots": [{"id": 10}], "open_lots": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "teor": "90"}], "has_any_lots": True}

    def sync_gold_inventory_ledger(self):
        self.sync_calls += 1


class _FakeInventoryReportDB:
    def __init__(self) -> None:
        self.inventory_calls = 0

    def get_gold_inventory_status(self, inventory_transactions=None, *, open_only=False):
        self.inventory_calls += 1
        return {"lots": [{"id": 10}], "open_lots": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "teor": "90"}], "available_grams": "5", "inventory_cost_usd": "400", "avg_cost_usd_per_gram": "80", "has_any_lots": True}

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
        return {"lots": [{"id": 10}], "available_grams": "5", "inventory_cost_usd": "400", "avg_cost_usd_per_gram": "80", "open_lots": [{"source_transaction_id": 18, "remaining_grams": "5", "unit_cost_usd": "80"}], "has_any_lots": True}

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
        return {"lots": [{"id": 10, "status": "open"}], "open_lots": [{"id": 10, "source_transaction_id": 18, "remaining_grams": "5", "unit_cost_usd": "80", "teor": "90", "gold_type": "fundido", "quebra": "", "pessoa": "Ana"}], "available_grams": "5", "inventory_cost_usd": "400", "avg_cost_usd_per_gram": "80", "has_any_lots": True}

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
