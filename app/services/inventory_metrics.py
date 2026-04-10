from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, cast


def build_inventory_metric_helpers(*, money: Callable[[Decimal], Decimal]) -> SimpleNamespace:
    def build_fechamento_status(item: Dict[str, Any]) -> Dict[str, Decimal | str | bool]:
        peso = Decimal(str(item.get("peso") or "0"))
        fechamento = Decimal(str(item.get("fechamento_gramas") or peso or "0"))
        fechamento_tipo = str(item.get("fechamento_tipo") or "total").lower()
        if fechamento <= 0 and peso > 0:
            fechamento = peso
        fechado = min(fechamento, peso) if peso > 0 else Decimal("0")
        aberto = max(Decimal("0"), peso - fechado)
        is_partial = fechamento_tipo == "parcial" or aberto > 0
        return {
            "peso": peso,
            "fechado": fechado,
            "aberto": aberto,
            "tipo": fechamento_tipo,
            "is_partial": is_partial,
        }

    def collect_open_fechamentos(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        open_items: List[Dict[str, Any]] = []
        for item in transactions:
            status = build_fechamento_status(item)
            if not bool(status["is_partial"]):
                continue
            if Decimal(str(status["aberto"])) <= 0:
                continue
            open_items.append({**item, "fechamento_status": status})
        open_items.sort(key=lambda row: str(row.get("criado_em") or ""), reverse=True)
        return open_items

    def sum_open_fechamento_grams(transactions: List[Dict[str, Any]]) -> Decimal:
        total_open = Decimal("0")
        for item in transactions:
            status = cast(Dict[str, Any], item.get("fechamento_status") or build_fechamento_status(item))
            total_open += Decimal(str(status.get("aberto") or "0"))
        return total_open

    def build_gold_caixa_metrics(saldo_xau: Decimal, transactions: List[Dict[str, Any]]) -> Dict[str, Decimal]:
        open_fechamentos = collect_open_fechamentos(transactions)
        ouro_pendente = sum_open_fechamento_grams(open_fechamentos)
        return build_gold_caixa_metrics_from_pending_grams(saldo_xau, ouro_pendente)

    def build_gold_caixa_metrics_from_pending_grams(saldo_xau: Decimal, ouro_pendente: Decimal) -> Dict[str, Decimal]:
        ouro_proprio = saldo_xau - ouro_pendente
        return {
            "ouro_em_caixa": saldo_xau,
            "ouro_pendente": ouro_pendente,
            "ouro_proprio": ouro_proprio,
        }

    def build_fifo_inventory_lots(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lots: List[Dict[str, Any]] = []
        ordered = sorted(
            transactions,
            key=lambda tx: (
                str(tx.get("criado_em") or ""),
                int(tx.get("id") or 0),
            ),
        )
        for tx in ordered:
            tipo = str(tx.get("tipo_operacao") or "").lower()
            if tipo not in {"compra", "venda"}:
                continue

            try:
                peso = Decimal(str(tx.get("peso") or "0"))
            except (InvalidOperation, TypeError, ValueError):
                continue

            if peso <= 0:
                continue

            if tipo == "compra":
                try:
                    unit_cost = Decimal(str(tx.get("preco_usd") or "0"))
                except (InvalidOperation, TypeError, ValueError):
                    unit_cost = Decimal("0")
                lots.append(
                    {
                        "source_id": int(tx.get("id") or 0),
                        "criado_em": str(tx.get("criado_em") or ""),
                        "initial_grams": peso,
                        "remaining_grams": peso,
                        "unit_cost_usd": unit_cost,
                        "teor": str(tx.get("teor") or ""),
                        "gold_type": str(tx.get("gold_type") or ""),
                        "quebra": str(tx.get("quebra") or ""),
                        "pessoa": str(tx.get("pessoa") or ""),
                    }
                )
                continue

            remaining_sale = peso
            while remaining_sale > 0 and lots:
                head = lots[0]
                head_remaining = Decimal(str(head.get("remaining_grams") or "0"))
                if head_remaining <= 0:
                    lots.pop(0)
                    continue
                consumed = min(head_remaining, remaining_sale)
                head["remaining_grams"] = str(head_remaining - consumed)
                remaining_sale -= consumed
                if Decimal(str(head.get("remaining_grams") or "0")) <= 0:
                    lots.pop(0)

        normalized: List[Dict[str, Any]] = []
        for lot in lots:
            remaining = Decimal(str(lot.get("remaining_grams") or "0"))
            if remaining > 0:
                normalized.append(
                    {
                        "source_id": int(lot.get("source_id") or 0),
                        "criado_em": str(lot.get("criado_em") or ""),
                        "initial_grams": str(Decimal(str(lot.get("initial_grams") or remaining))),
                        "remaining_grams": str(remaining),
                        "unit_cost_usd": str(Decimal(str(lot.get("unit_cost_usd") or "0"))),
                        "teor": str(lot.get("teor") or ""),
                        "gold_type": str(lot.get("gold_type") or ""),
                        "quebra": str(lot.get("quebra") or ""),
                        "pessoa": str(lot.get("pessoa") or ""),
                    }
                )
        return normalized

    def preview_fifo_sale_consumption(lots: List[Dict[str, Any]], peso_venda: Decimal) -> Dict[str, Any]:
        remaining_sale = peso_venda
        consumed_cost = Decimal("0")
        consumed_grams = Decimal("0")
        breakdown: List[Dict[str, Any]] = []

        working_lots = [dict(lot) for lot in lots]
        for lot in working_lots:
            if remaining_sale <= 0:
                break
            lot_remaining = Decimal(str(lot.get("remaining_grams") or "0"))
            if lot_remaining <= 0:
                continue
            unit_cost = Decimal(str(lot.get("unit_cost_usd") or "0"))
            consumed = min(lot_remaining, remaining_sale)
            cost_usd = money(consumed * unit_cost)
            breakdown.append(
                {
                    "source_id": int(lot.get("source_id") or 0),
                    "grams": str(consumed),
                    "unit_cost_usd": str(money(unit_cost)),
                    "cost_usd": str(cost_usd),
                }
            )
            consumed_cost += cost_usd
            consumed_grams += consumed
            remaining_sale -= consumed

        return {
            "consumed_grams": consumed_grams,
            "consumed_cost_usd": money(consumed_cost),
            "shortfall_grams": remaining_sale if remaining_sale > 0 else Decimal("0"),
            "breakdown": breakdown,
        }

    def compute_inventory_metrics(transactions: List[Dict[str, Any]]) -> Dict[str, Decimal]:
        lots = build_fifo_inventory_lots(transactions)
        total_grams = sum((Decimal(str(lot.get("remaining_grams") or "0")) for lot in lots), Decimal("0"))
        total_cost = sum(
            (
                Decimal(str(lot.get("remaining_grams") or "0"))
                * Decimal(str(lot.get("unit_cost_usd") or "0"))
                for lot in lots
            ),
            Decimal("0"),
        )
        avg_cost = money(total_cost / total_grams) if total_grams > 0 else Decimal("0")
        return {
            "available_grams": total_grams,
            "inventory_cost_usd": money(total_cost),
            "avg_cost_usd_per_gram": avg_cost,
        }

    return SimpleNamespace(
        build_fechamento_status=build_fechamento_status,
        collect_open_fechamentos=collect_open_fechamentos,
        sum_open_fechamento_grams=sum_open_fechamento_grams,
        build_gold_caixa_metrics=build_gold_caixa_metrics,
        build_gold_caixa_metrics_from_pending_grams=build_gold_caixa_metrics_from_pending_grams,
        build_fifo_inventory_lots=build_fifo_inventory_lots,
        preview_fifo_sale_consumption=preview_fifo_sale_consumption,
        compute_inventory_metrics=compute_inventory_metrics,
    )