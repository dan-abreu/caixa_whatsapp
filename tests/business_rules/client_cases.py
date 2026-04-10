from tests.business_rules.common import BusinessRulesTestCase, DatabaseClient, Decimal, _aggregate_cliente_movements, _aggregate_cliente_movements_by_client
from tests.business_rules.fakes_supabase import _FakeSupabaseClient


class BusinessRulesClientTests(BusinessRulesTestCase):
    def test_aggregate_cliente_movements_helpers(self) -> None:
        balances = _aggregate_cliente_movements([{"moeda": "XAU", "valor": "12.5"}, {"moeda": "XAU", "valor": "-3.0"}, {"moeda": "USD", "valor": "150"}])
        self.assertEqual(balances["XAU"], Decimal("9.5"))
        by_client = _aggregate_cliente_movements_by_client([{"cliente_id": 3, "moeda": "XAU", "valor": "4.25"}, {"cliente_id": 3, "moeda": "USD", "valor": "150"}, {"cliente_id": 4, "moeda": "XAU", "valor": "1.75"}, {"cliente_id": 3, "moeda": "XAU", "valor": "-1.00"}])
        self.assertEqual(by_client[3]["XAU"], Decimal("3.25"))

    def test_list_clientes_with_balances_and_search_caches(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [{"id": 1, "nome": "Ana", "apelido": "ana", "telefone": "+5977000000", "documento": "ABC", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"}, {"id": 2, "nome": "Bruno", "ativo": True, "atualizado_em": "2026-04-09T09:00:00+00:00"}]
        db.client.store["cliente_movimentacoes"] = [{"cliente_id": 1, "moeda": "XAU", "valor": "2.5"}, {"cliente_id": 1, "moeda": "USD", "valor": "100"}, {"cliente_id": 2, "moeda": "XAU", "valor": "1.0"}]
        items = db.list_clientes_with_balances(limit=10)
        self.assertEqual(items[0]["balances"]["XAU"], "2.5")
        first = db.search_clientes("Ana", limit=8)
        first_exec_count = int(db.client.store.get("_cliente_search_exec_count", 0))
        second = db.search_clientes("Ana", limit=8)
        self.assertEqual(len(first), len(second))
        self.assertEqual(int(db.client.store.get("_cliente_search_exec_count", 0)), first_exec_count)

    def test_cliente_balance_and_search_caches_are_invalidated(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.client.store["clientes"] = [{"id": 1, "nome": "Ana", "apelido": "", "telefone": "+5977000000", "documento": "", "observacoes": "", "ativo": True, "atualizado_em": "2026-04-09T10:00:00+00:00"}]
        db.client.store["cliente_movimentacoes"] = [{"cliente_id": 1, "moeda": "XAU", "valor": "2.5"}]
        self.assertEqual(db.list_clientes_with_balances(search="Ana", limit=10)[0]["balances"]["XAU"], "2.5")
        db.record_cliente_operation_balance(cliente_id=1, gold_transaction_id=99, tipo_operacao="compra", pending_grams=Decimal("1.0"), pessoa="Ana")
        self.assertEqual(db.list_clientes_with_balances(search="Ana", limit=10)[0]["balances"]["XAU"], "3.5")
        created = db.create_cliente("Ana Maria")
        self.assertIsNotNone(created)
        self.assertEqual(len(db.search_clientes("Ana", limit=8)), 2)

    def test_record_cliente_operation_balance_and_snapshot_cache(self) -> None:
        db = DatabaseClient.__new__(DatabaseClient)
        db.client = _FakeSupabaseClient()
        db.record_cliente_operation_balance(cliente_id=3, gold_transaction_id=77, tipo_operacao="compra", pending_grams=Decimal("4.25"), pessoa="Joao")
        db.record_cliente_operation_balance(cliente_id=3, gold_transaction_id=78, tipo_operacao="venda", pending_grams=Decimal("1.25"), pessoa="Joao")
        self.assertEqual(Decimal(str(db.client.store["cliente_movimentacoes"][1]["valor"])), Decimal("-1.25"))
        db.client.store["clientes"] = [{"id": 3, "nome": "Joao", "ativo": True}]
        snapshot = db.get_cliente_account_snapshot(3)
        self.assertIsNotNone(snapshot)
