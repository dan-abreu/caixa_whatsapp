from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, cast

from fastapi import HTTPException

from app.core.formatting import (
    _format_usd_pt_br,
    _format_grams_pt_br,
    _format_receipt_caixa_movement,
    grams,
    money,
)
from app.database import DatabaseClient


def _payment_status_message_map(receipt: Dict[str, Any]) -> Dict[str, str]:
    gap_abs = cast(Decimal, receipt.get("payment_gap_abs_usd") or Decimal("0"))
    return {
        "quitado": "Pagamento conferido sem diferenca.",
        "faltante": f"Faltam {_format_usd_pt_br(gap_abs)} para cobrir o fechamento.",
        "excedente": f"Ha excedente de {_format_usd_pt_br(gap_abs)} frente ao fechamento.",
    }


def _build_gold_receipt_context(
    db: DatabaseClient,
    operation_id: int,
    *,
    build_cache_key: Callable[[int], str],
    get_cached_context: Callable[[str], Optional[Dict[str, Any]]],
    set_cached_context: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    format_datetime_pt_br: Callable[[Any], str],
) -> Dict[str, Any]:
    cache_key = build_cache_key(operation_id)
    cached_context = get_cached_context(cache_key)
    if cached_context is not None:
        return cached_context

    audit = db.get_gold_operation_audit(operation_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Recibo da operacao nao encontrado")

    operation = dict(cast(Dict[str, Any], audit.get("operation") or {}))
    payments = cast(List[Dict[str, Any]], audit.get("payments") or [])
    inventory_consumptions = cast(List[Dict[str, Any]], audit.get("inventory_consumptions") or [])
    cliente_id = operation.get("cliente_id")
    cliente_id_value = int(str(cliente_id)) if cliente_id not in {None, "", 0, "0"} else None
    cliente = db.get_cliente_by_id(cliente_id_value) if cliente_id_value is not None else None
    operador = db.get_usuario_by_telefone(str(operation.get("operador_id") or ""))

    peso = Decimal(str(operation.get("peso") or "0"))
    teor = Decimal(str(operation.get("teor") or "0"))
    preco_usd = Decimal(str(operation.get("preco_usd") or "0"))
    total_usd = Decimal(str(operation.get("total_usd") or "0"))
    recorded_paid_usd = Decimal(str(operation.get("total_pago_usd") or "0"))
    fechamento_gramas = Decimal(str(operation.get("fechamento_gramas") or peso or "0"))
    open_grams = max(Decimal("0"), peso - fechamento_gramas)
    fine_gold = grams(peso * (teor / Decimal("100"))) if peso > 0 and teor > 0 else Decimal("0")
    closed_fine_gold = grams(fechamento_gramas * (teor / Decimal("100"))) if fechamento_gramas > 0 and teor > 0 else Decimal("0")
    open_fine_gold = grams(open_grams * (teor / Decimal("100"))) if open_grams > 0 and teor > 0 else Decimal("0")
    target_payment_usd = money((total_usd * fechamento_gramas / peso) if peso > 0 else total_usd)
    actual_paid_usd = money(sum((Decimal(str(item.get("valor_usd") or "0")) for item in payments), Decimal("0")))
    payment_gap_usd = money(target_payment_usd - actual_paid_usd)
    payment_gap_abs_usd = money(abs(payment_gap_usd))
    payment_status = "quitado"
    if payment_gap_usd > Decimal("0.004"):
        payment_status = "faltante"
    elif payment_gap_usd < Decimal("-0.004"):
        payment_status = "excedente"
    payment_audit_warning = ""
    if abs(recorded_paid_usd - actual_paid_usd) > Decimal("0.004"):
        payment_audit_warning = (
            "O valor pago registrado no cabecalho nao bate com os pagamentos detalhados desta operacao. "
            "O recibo esta exibindo o total conferido a partir dos pagamentos vinculados."
        )
    tipo_operacao = str(operation.get("tipo_operacao") or "compra").lower()
    gold_direction = "Entrada de ouro" if tipo_operacao == "compra" else "Saida de ouro"
    cash_direction = "Saida financeira" if tipo_operacao == "compra" else "Entrada financeira"
    caixa_effects: List[str] = [f"{gold_direction}: {_format_grams_pt_br(peso)}"]
    for payment in payments:
        currency = str(payment.get("moeda") or "USD").upper()
        amount = Decimal(str(payment.get("valor_moeda") or "0"))
        caixa_effects.append(
            f"{cash_direction}: {_format_receipt_caixa_movement(currency, amount if tipo_operacao == 'venda' else amount * Decimal('-1'))}"
        )

    whatsapp_lines = [
        f"Recibo da operacao GT-{operation_id}",
        f"Cliente: {str(operation.get('pessoa') or '-').strip() or '-'}",
        f"Tipo: {tipo_operacao.upper()}",
        f"Peso: {peso:,.3f} g",
        f"Total: USD {money(total_usd)}",
        f"Fechamento: {fechamento_gramas:,.3f} g | Pago conferido: USD {actual_paid_usd}",
        "PDF: __PDF_URL__",
    ]

    context = {
        "operation_id": operation_id,
        "operation": operation,
        "payments": payments,
        "inventory_consumptions": inventory_consumptions,
        "cliente": cliente,
        "operador": operador,
        "created_at": format_datetime_pt_br(operation.get("criado_em")),
        "peso": peso,
        "teor": teor,
        "preco_usd": preco_usd,
        "total_usd": total_usd,
        "total_pago_usd": actual_paid_usd,
        "recorded_paid_usd": recorded_paid_usd,
        "target_payment_usd": target_payment_usd,
        "diferenca_usd": payment_gap_usd,
        "payment_gap_abs_usd": payment_gap_abs_usd,
        "payment_status": payment_status,
        "payment_audit_warning": payment_audit_warning,
        "fechamento_gramas": fechamento_gramas,
        "open_grams": open_grams,
        "fine_gold": fine_gold,
        "closed_fine_gold": closed_fine_gold,
        "open_fine_gold": open_fine_gold,
        "tipo_operacao": tipo_operacao,
        "caixa_effects": caixa_effects,
        "whatsapp_template": "\n".join(whatsapp_lines),
    }
    return set_cached_context(cache_key, context)