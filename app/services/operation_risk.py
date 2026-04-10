from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple, cast


def build_operation_risk_helpers(
    *,
    money: Callable[[Decimal], Decimal],
    build_fifo_inventory_lots: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    preview_fifo_sale_consumption: Callable[[List[Dict[str, Any]], Decimal], Dict[str, Any]],
    format_caixa_movement: Callable[[str, Decimal], str],
) -> SimpleNamespace:
    def compute_sale_profit_reference(
        db: Any,
        ativo_id: int,
        peso: Decimal,
        total_pago_usd: Decimal,
    ) -> Optional[Dict[str, str]]:
        taxa_atual = db.get_taxa_atual(ativo_id)
        if not taxa_atual:
            return None

        preco_compra_raw = taxa_atual.get("preco_compra")
        if preco_compra_raw is None:
            return None

        try:
            preco_compra_ref = Decimal(str(preco_compra_raw))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if preco_compra_ref <= 0:
            return None

        custo_ref_usd = money(peso * preco_compra_ref)
        lucro_ref_usd = money(total_pago_usd - custo_ref_usd)
        return {
            "preco_compra_ref_usd": str(money(preco_compra_ref)),
            "custo_ref_usd": str(custo_ref_usd),
            "lucro_ref_usd": str(lucro_ref_usd),
        }

    def attach_sale_profit_reference(db: Any, contexto: Dict[str, Any]) -> None:
        if str(contexto.get("tipo_operacao", "")).lower() != "venda":
            return

        try:
            peso = Decimal(str(contexto.get("peso", "0")))
            total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
        except (InvalidOperation, TypeError, ValueError):
            return

        if peso <= 0 or total_pago <= 0:
            return

        ativo = db.get_ativo_by_nome("Ouro")
        if not ativo:
            ativo = db.get_ativo_by_nome("Ouro 24k")
        if not ativo:
            return

        profit_ref = compute_sale_profit_reference(db, int(ativo["id"]), peso, total_pago)
        if profit_ref:
            contexto.update(profit_ref)

        inventory_txs = db.get_gold_inventory_transactions()
        selected_sale_lots = contexto.get("selected_sale_lots") if isinstance(contexto.get("selected_sale_lots"), list) else []
        if selected_sale_lots and hasattr(db, "preview_gold_inventory_selection"):
            fifo_result = db.preview_gold_inventory_selection(peso, cast(List[Dict[str, Any]], selected_sale_lots))
        else:
            lots = build_fifo_inventory_lots(inventory_txs)
            fifo_result = preview_fifo_sale_consumption(lots, peso)
        consumed_grams = Decimal(str(fifo_result.get("consumed_grams") or "0"))
        consumed_cost = Decimal(str(fifo_result.get("consumed_cost_usd") or "0"))
        shortfall = Decimal(str(fifo_result.get("shortfall_grams") or "0"))
        if consumed_grams > 0 and shortfall == 0:
            contexto.update(
                {
                    "profit_method": "selected_real" if selected_sale_lots else "fifo_real",
                    "custo_fifo_usd": str(money(consumed_cost)),
                    "lucro_real_usd": str(money(total_pago - consumed_cost)),
                    "consumo_fifo": fifo_result.get("breakdown", []),
                }
            )
        elif shortfall > 0:
            contexto["profit_method"] = "selected_insufficient_stock" if selected_sale_lots else "fifo_insufficient_stock"
            contexto["fifo_shortfall_grams"] = str(shortfall)

    def project_caixa_balances(
        current_saldos: Dict[str, Any],
        tipo_operacao: str,
        peso_gramas: Decimal,
        pagamentos: List[Dict[str, Any]],
    ) -> Dict[str, Decimal]:
        projected = {moeda.upper(): Decimal(str(valor)) for moeda, valor in current_saldos.items()}
        for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]:
            projected.setdefault(moeda, Decimal("0"))

        if peso_gramas > 0:
            projected["XAU"] += peso_gramas if tipo_operacao == "compra" else -peso_gramas

        for pagamento in pagamentos:
            moeda = str(pagamento.get("moeda") or "USD").upper()
            valor_moeda = Decimal(str(pagamento.get("valor_moeda") or "0"))
            if moeda not in projected or valor_moeda == 0:
                continue
            projected[moeda] += -valor_moeda if tipo_operacao == "compra" else valor_moeda

        return projected

    def find_negative_caixa_balances(projected_saldos: Dict[str, Decimal]) -> List[Tuple[str, Decimal]]:
        negatives: List[Tuple[str, Decimal]] = []
        for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]:
            saldo = projected_saldos.get(moeda, Decimal("0"))
            if saldo < 0:
                negatives.append((moeda, saldo))
        return negatives

    def format_negative_caixa_lines(negatives: List[Tuple[str, Decimal]]) -> List[str]:
        return [f"- {moeda}: {format_caixa_movement(moeda, saldo)}" for moeda, saldo in negatives]

    return SimpleNamespace(
        compute_sale_profit_reference=compute_sale_profit_reference,
        attach_sale_profit_reference=attach_sale_profit_reference,
        project_caixa_balances=project_caixa_balances,
        find_negative_caixa_balances=find_negative_caixa_balances,
        format_negative_caixa_lines=format_negative_caixa_lines,
    )