from tests.business_rules.common import (
    BusinessRulesTestCase,
    DatabaseClient,
    Decimal,
    HTTPException,
    _build_fechamento_status,
    _build_fifo_inventory_lots,
    _build_gold_caixa_metrics,
    _build_gold_caixa_metrics_from_pending_grams,
    _build_market_snapshot_from_rates,
    _build_operation_draft_from_message,
    _derive_forma_pagamento_summary,
    _display_cambio_for_web_input,
    _extract_awesomeapi_gold_price,
    _extract_gold_api_xau_usd,
    _find_negative_caixa_balances,
    _hash_web_pin,
    _normalize_cambio_para_usd,
    _parse_decimal_from_text,
    _parse_gold_trade_profile,
    _parse_operation_reference,
    _parse_web_payments_from_form,
    _preview_fifo_sale_consumption,
    _project_caixa_balances,
    _should_reset_guided_session_for_message,
    _sum_open_fechamento_grams,
    _verify_web_pin,
    build_runtime_saas_ui_helpers,
    build_whatsapp_session_helpers,
)
from tests.business_rules.fakes_runtime import _FakeDraftDB, _FakeFXDB
from tests.business_rules.fakes_supabase import _FakeMissingWebPinSupabaseClient, _FakeSupabaseClient


class BusinessRulesGeneralTests(BusinessRulesTestCase):
    def test_fifo_consumption_uses_oldest_purchase_first(self) -> None:
        transactions = [{"id": 1, "tipo_operacao": "compra", "peso": "100", "preco_usd": "70", "teor": "90", "criado_em": "2026-04-01T10:00:00+00:00"}, {"id": 2, "tipo_operacao": "compra", "peso": "50", "preco_usd": "80", "teor": "85", "criado_em": "2026-04-01T11:00:00+00:00"}, {"id": 3, "tipo_operacao": "venda", "peso": "60", "preco_usd": "120", "criado_em": "2026-04-01T12:00:00+00:00"}]
        lots = _build_fifo_inventory_lots(transactions)
        self.assertEqual(lots[0]["source_id"], 1)
        self.assertEqual(Decimal(str(lots[0]["remaining_grams"])), Decimal("40"))
        preview = _preview_fifo_sale_consumption(lots, Decimal("70"))
        self.assertEqual(Decimal(str(preview["consumed_cost_usd"])), Decimal("5200.00"))

    def test_projected_balances_flag_negative_boxes(self) -> None:
        projected = _project_caixa_balances({"XAU": "20", "USD": "1000", "EUR": "0", "SRD": "0", "BRL": "0"}, "compra", Decimal("10"), [{"moeda": "USD", "valor_moeda": "1500"}])
        self.assertEqual(_find_negative_caixa_balances(projected)[0], ("USD", Decimal("-500")))

    def test_session_and_operation_reference_helpers(self) -> None:
        self.assertTrue(_should_reset_guided_session_for_message("oii"))
        self.assertEqual(_parse_operation_reference("GT-24"), ("gold", 24))
        self.assertEqual(_parse_decimal_from_text("abc", "peso"), Decimal("-1"))

    def test_web_pin_schema_and_hash_helpers(self) -> None:
        pin_hash = _hash_web_pin("123456", salt="fixed-salt")
        self.assertTrue(_verify_web_pin("123456", pin_hash))
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeMissingWebPinSupabaseClient()
        DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY = None
        usuario = db.get_usuario_web_auth("+5598991438754")
        self.assertIsNotNone(usuario)
        DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY = False
        self.assertFalse(bool(db.set_usuario_web_pin("+5598991438754", "123456")["web_pin_schema_ready"]))

    def test_currency_and_payment_helpers(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["transacoes"] = [{"moeda_liquidacao": "EUR", "cambio_para_usd": "invalido", "data_hora": "2026-04-09T10:00:00+00:00"}, {"moeda_liquidacao": "EUR", "cambio_para_usd": "0.82", "data_hora": "2026-04-09T09:00:00+00:00"}]
        db.client.store["gold_payments"] = [{"moeda": "SRD", "cambio_para_usd": None, "id": 2}, {"moeda": "SRD", "cambio_para_usd": "38", "id": 1}]
        self.assertEqual(db.get_last_cambio_para_usd_map(["EUR", "SRD", "USD"])["EUR"], Decimal("0.82"))
        db.client.store["caixas"] = [{"moeda": "USD", "saldo": "abc"}, {"moeda": "XAU", "saldo": "2.5"}]
        self.assertEqual(db.get_saldo_caixa()["USD"], "0")
        pagamentos = _parse_web_payments_from_form(_FakeFXDB(), {"payment_1_moeda": "USD", "payment_1_valor": "100", "payment_1_cambio": "1", "payment_1_forma": "dinheiro", "payment_2_moeda": "SRD", "payment_2_valor": "380", "payment_2_cambio": "", "payment_2_forma": "transferencia"})
        self.assertEqual(_derive_forma_pagamento_summary(pagamentos), "misto")

    def test_runtime_session_and_eur_display_helpers(self) -> None:
        helpers = build_runtime_saas_ui_helpers(asset_url=lambda path: f"/static/{path}", normalize_text=lambda value: value.strip().lower())
        self.assertEqual(helpers.format_cliente_code("abc"), "CL-000000")
        session_helpers = build_whatsapp_session_helpers(session_cache={}, guided_session_idle_minutes=5)
        self.assertIsNone(session_helpers.guided_session_idle_minutes({"atualizado_em": "nao-e-data"}))
        self.assertEqual(_normalize_cambio_para_usd("EUR", Decimal("1.25")), Decimal("0.8000"))
        self.assertEqual(_display_cambio_for_web_input("EUR", Decimal("0.8000")), "1.25")

    def test_gold_metric_and_trade_profile_helpers(self) -> None:
        status = _build_fechamento_status({"peso": "100", "fechamento_gramas": "40", "fechamento_tipo": "parcial"})
        self.assertTrue(bool(status["is_partial"]))
        self.assertEqual(_sum_open_fechamento_grams([{"peso": "100", "fechamento_gramas": "40", "fechamento_tipo": "parcial"}, {"peso": "30", "fechamento_gramas": "10", "fechamento_tipo": "parcial"}]), Decimal("80"))
        self.assertEqual(_build_gold_caixa_metrics(Decimal("1000"), [{"tipo_operacao": "compra", "peso": "1000", "fechamento_gramas": "700", "fechamento_tipo": "parcial"}])["ouro_proprio"], Decimal("700"))
        self.assertEqual(_build_gold_caixa_metrics_from_pending_grams(Decimal("1000"), Decimal("300"))["ouro_pendente"], Decimal("300"))
        self.assertEqual(_parse_gold_trade_profile("compra", "queimado", "3.5"), ("queimado", Decimal("3.50")))
        with self.assertRaises(HTTPException):
            _parse_gold_trade_profile("compra", "queimado", "")

    def test_market_snapshot_extractors_and_operation_draft(self) -> None:
        snapshot = _build_market_snapshot_from_rates(Decimal("3103.50"), Decimal("5.5000"), Decimal("1.1000"), None)
        self.assertEqual(snapshot["grama_ref"], Decimal("89.80"))
        self.assertEqual(_extract_gold_api_xau_usd({"price": 3025.45}), Decimal("3025.45"))
        self.assertEqual(_extract_awesomeapi_gold_price({"XAUUSD": {"bid": "4801.06", "ask": "4801.84"}}), Decimal("4801.06"))
        burned = _build_operation_draft_from_message(_FakeDraftDB(), {"telefone": "+59711111111"}, "comprei ouro queimado 12,4g teor 91,6 a 104 usd de Joao com quebra 3,5% pago em 300 USD e 7600 SRD")
        self.assertEqual(burned["draft"]["quebra"], "3.5")
        named = _build_operation_draft_from_message(_FakeDraftDB(), {"telefone": "+59711111111"}, "comprei 100 gramas de ouro fundido a 120 dolares do cliente teste")
        self.assertEqual(named["draft"]["cliente_id"], "7")
        price_phrase = _build_operation_draft_from_message(_FakeDraftDB(), {"telefone": "+59711111111"}, "compra 50 g ouro fundido por 135 dolares do cliente carlos")
        self.assertEqual(price_phrase["draft"]["preco_usd"], "135")

    def test_reporting_tolerates_invalid_transaction_ids(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["gold_transactions"] = [
            {"id": "quebrado", "status": "registrada", "criado_em": "2026-04-09T09:00:00+00:00", "operador_id": "op-a"},
            {"id": 2, "status": "registrada", "criado_em": "2026-04-09T10:00:00+00:00", "operador_id": "op-b", "tipo_operacao": "compra", "pessoa": "Ana"},
        ]
        db.client.store["gold_payments"] = [
            {"gold_transaction_id": "ruim", "moeda": "EUR", "valor_moeda": "10", "valor_usd": "11", "forma_pagamento": "pix"},
            {"gold_transaction_id": 2, "moeda": "USD", "valor_moeda": "50", "valor_usd": "50", "forma_pagamento": "dinheiro"},
        ]
        db.client.store["transacoes"] = []

        with self.assertLogs("caixa_whatsapp", level="WARNING") as captured:
            by_currency = db.get_gold_summary_by_currency("2026-04-09T00:00:00+00:00", "2026-04-10T00:00:00+00:00")
            extrato = db.get_extrato_transactions("2026-04-09T00:00:00+00:00", "2026-04-10T00:00:00+00:00")

        self.assertEqual("USD", by_currency[0]["moeda"])
        self.assertEqual("50", by_currency[0]["total_valor_usd"])
        valid_entry = next(item for item in extrato if item.get("id") == 2)
        self.assertEqual(1, len(valid_entry["pagamentos"]))
        self.assertTrue(any("gold_transactions.summary.id" in line for line in captured.output))
        self.assertTrue(any("gold_transactions.extrato.id" in line for line in captured.output))
