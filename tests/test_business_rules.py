import unittest
from decimal import Decimal

from app.main import (
    _build_fifo_inventory_lots,
    _find_negative_caixa_balances,
    _preview_fifo_sale_consumption,
    _project_caixa_balances,
    _should_reset_guided_session_for_message,
)


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


if __name__ == "__main__":
    unittest.main()
