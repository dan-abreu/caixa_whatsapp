from html import escape
from io import BytesIO
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, cast
from urllib.parse import quote

from fastapi import HTTPException
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.formatting import (
    _format_decimal_pt_br,
    _format_grams_pt_br,
    _format_percent_pt_br,
    _format_receipt_caixa_movement,
    _format_usd_pt_br,
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


def _render_saas_receipt_html(
    receipt: Dict[str, Any],
    pdf_url: str,
    back_url: str,
    *,
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
) -> str:
    operation = cast(Dict[str, Any], receipt.get("operation") or {})
    cliente = cast(Optional[Dict[str, Any]], receipt.get("cliente"))
    operador = cast(Optional[Dict[str, Any]], receipt.get("operador"))
    payments = cast(List[Dict[str, Any]], receipt.get("payments") or [])
    consumptions = cast(List[Dict[str, Any]], receipt.get("inventory_consumptions") or [])
    whatsapp_text = quote(str(receipt.get("whatsapp_template") or "").replace("__PDF_URL__", pdf_url))
    payment_warning = str(receipt.get("payment_audit_warning") or "").strip()
    payment_warning_html = f"<div class='notice warning'>{escape(payment_warning)}</div>" if payment_warning else ""
    payment_status_map = _payment_status_message_map(receipt)

    payment_rows = "".join(
        f"<tr><td>{escape(str(item.get('moeda') or 'USD').upper())}</td><td>{escape(str(item.get('forma_pagamento') or '-'))}</td><td>{escape(_format_decimal_pt_br(Decimal(str(item.get('valor_moeda') or '0')), 2))}</td><td>{escape(_format_usd_pt_br(Decimal(str(item.get('valor_usd') or '0'))))}</td></tr>"
        for item in payments
    ) or "<tr><td colspan='4'>Nenhum pagamento detalhado vinculado a esta operacao.</td></tr>"
    consumption_rows = "".join(
        f"<tr><td>{escape(str(item.get('lot_id') or '-'))}</td><td>{escape(_format_grams_pt_br(Decimal(str(item.get('consumed_grams') or '0'))))}</td><td>{escape(_format_usd_pt_br(Decimal(str(item.get('unit_cost_usd') or '0'))))}</td><td>{escape(_format_usd_pt_br(Decimal(str(item.get('consumed_cost_usd') or '0'))))}</td></tr>"
        for item in consumptions
    ) or "<tr><td colspan='4'>Sem consumo FIFO registrado para esta operacao.</td></tr>"
    caixa_rows = "".join(f"<li>{escape(line)}</li>" for line in cast(List[str], receipt.get("caixa_effects") or []))
    observacoes = escape(str(operation.get("observacoes") or "-") or "-")
    compact_details_rows = "".join(
        [
            f"<tr><th>ID</th><td>GT-{int(receipt.get('operation_id') or 0)}</td><th>Data</th><td>{escape(str(receipt.get('created_at') or '-'))}</td></tr>",
            f"<tr><th>Cliente</th><td>{escape(str(operation.get('pessoa') or '-'))}</td><th>Operador</th><td>{escape(str((operador or {}).get('nome') or operation.get('operador_id') or '-'))}</td></tr>",
            f"<tr><th>Tipo</th><td>{escape(str(receipt.get('tipo_operacao') or '-').upper())}</td><th>Origem</th><td>{escape(str(operation.get('origem') or '-'))}</td></tr>",
            f"<tr><th>Material</th><td>{escape(str(operation.get('gold_type') or '-'))}</td><th>Quebra</th><td>{escape(str(operation.get('quebra') or '-'))}</td></tr>",
            f"<tr><th>Peso</th><td>{escape(_format_grams_pt_br(cast(Decimal, receipt.get('peso') or Decimal('0'))))}</td><th>Teor</th><td>{escape(_format_percent_pt_br(cast(Decimal, receipt.get('teor') or Decimal('0'))))}</td></tr>",
            f"<tr><th>Preco USD/g</th><td>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('preco_usd') or Decimal('0'))))}</td><th>Fechamento</th><td>{escape(_format_grams_pt_br(cast(Decimal, receipt.get('fechamento_gramas') or Decimal('0'))))} ({escape(str(operation.get('fechamento_tipo') or '-'))})</td></tr>",
            f"<tr><th>Em aberto</th><td>{escape(_format_grams_pt_br(cast(Decimal, receipt.get('open_grams') or Decimal('0'))))}</td><th>Alvo fechamento</th><td>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('target_payment_usd') or Decimal('0'))))}</td></tr>",
            f"<tr><th>Pago conferido</th><td>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('total_pago_usd') or Decimal('0'))))}</td><th>Saldo fechamento</th><td>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('payment_gap_abs_usd') or Decimal('0'))))}</td></tr>",
            f"<tr><th>Ouro fino total</th><td>{escape(_format_grams_pt_br(cast(Decimal, receipt.get('fine_gold') or Decimal('0'))))}</td><th>Ouro fino fechado</th><td>{escape(_format_grams_pt_br(cast(Decimal, receipt.get('closed_fine_gold') or Decimal('0'))))}</td></tr>",
            f"<tr><th>Ouro fino em aberto</th><td>{escape(_format_grams_pt_br(cast(Decimal, receipt.get('open_fine_gold') or Decimal('0'))))}</td><th>Status pagamento</th><td>{escape(payment_status_map.get(str(receipt.get('payment_status') or ''), '-'))}</td></tr>",
            f"<tr><th>Conta</th><td colspan='3'>{escape(build_cliente_lookup_meta(cliente) if cliente else '-')}</td></tr>",
            f"<tr><th>Observacoes</th><td colspan='3'>{observacoes}</td></tr>",
        ]
    )

    return f"""
    <html>
        <head>
            <title>Recibo GT-{int(receipt.get('operation_id') or 0)}</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <style>
                :root {{ --ink: #1d1a16; --muted: #6d6658; --line: #ded2bd; --panel: #fffaf2; --accent: #184f3f; --accent-2: #a36a00; }}
                * {{ box-sizing: border-box; }}
                body {{ margin: 0; padding: 14px; background: #efe7d7; color: var(--ink); font-family: 'Segoe UI', sans-serif; }}
                .sheet {{ width: min(720px, 100%); margin: 0 auto; background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 16px; box-shadow: 0 18px 40px rgba(0,0,0,.08); }}
                .head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 12px; }}
                .head h1 {{ margin: 0 0 2px; font-size: 22px; line-height: 1.1; }}
                .head p {{ margin: 0; color: var(--muted); font-size: 12px; }}
                .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
                .btn {{ display: inline-flex; align-items: center; justify-content: center; text-decoration: none; padding: 8px 11px; border-radius: 11px; border: 1px solid var(--line); background: white; color: var(--accent); font-weight: 700; cursor: pointer; font-size: 12px; }}
                .btn.primary {{ background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: white; border: 0; }}
                .grid {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
                .split {{ display: grid; grid-template-columns: 1.2fr .8fr; gap: 10px; }}
                .panel {{ border: 1px solid var(--line); border-radius: 16px; padding: 10px; background: white; }}
                .panel h2 {{ margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
                .notice {{ margin-bottom: 10px; padding: 10px 12px; border-radius: 12px; border: 1px solid var(--line); font-size: 12px; }}
                .notice.warning {{ background: #fff5df; border-color: #e2c78f; color: #7d5a00; }}
                table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
                th, td {{ text-align: left; padding: 5px 4px; border-bottom: 1px solid var(--line); vertical-align: top; }}
                th {{ color: var(--muted); text-transform: uppercase; font-size: 10px; letter-spacing: .06em; white-space: nowrap; }}
                ul {{ margin: 0; padding-left: 15px; font-size: 11px; }}
                li {{ margin: 0 0 3px; }}
                .totals {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 8px; margin-bottom: 10px; }}
                .card {{ border: 1px solid var(--line); border-radius: 12px; padding: 8px; background: #fff; }}
                .card small {{ display: block; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .06em; font-size: 9px; }}
                .card strong {{ font-size: 16px; line-height: 1.1; }}
                .compact td[colspan='3'], .compact td[colspan='4'] {{ font-size: 10px; }}
                @page {{ size: A5 portrait; margin: 6mm; }}
                @media print {{ body {{ background: white; padding: 0; font-size: 10px; }} .sheet {{ box-shadow: none; border: 0; width: 100%; padding: 0; border-radius: 0; }} .actions {{ display: none; }} .panel {{ page-break-inside: avoid; }} .split {{ grid-template-columns: 1fr .8fr; gap: 6px; }} .totals {{ gap: 5px; margin-bottom: 6px; }} .card {{ padding: 6px; }} .card strong {{ font-size: 13px; }} th, td {{ padding: 3px 2px; font-size: 9px; }} .head h1 {{ font-size: 17px; }} .head p, .head span, .head strong {{ font-size: 10px; }} ul {{ font-size: 9px; }} }}
                @media (max-width: 760px) {{ .split, .totals {{ grid-template-columns: 1fr 1fr; }} .head {{ display: grid; }} }}
            </style>
        </head>
        <body>
            <div class='sheet'>
                <div class='head'>
                    <div>
                        <h1>Recibo Operacional</h1>
                        <p>Comprovante detalhado da operacao GT-{int(receipt.get('operation_id') or 0)}</p>
                    </div>
                    <div>
                        <strong>{escape(str(receipt.get('created_at') or '-'))}</strong><br>
                        <span style='color:var(--muted)'>Status: {escape(str(operation.get('status') or 'registrada').upper())}</span>
                    </div>
                </div>
                {payment_warning_html}
                <div class='actions'>
                    <a class='btn primary' href='{escape(pdf_url)}' target='_blank'>Exportar PDF</a>
                    <button class='btn' type='button' onclick='window.print()'>Imprimir</button>
                    <a class='btn' href='https://wa.me/?text={whatsapp_text}' target='_blank'>Enviar por WhatsApp</a>
                    <a class='btn' href='{escape(back_url)}'>Nova operacao</a>
                </div>
                <div class='totals'>
                    <div class='card'><small>Total USD</small><strong>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('total_usd') or Decimal('0'))))}</strong></div>
                    <div class='card'><small>Alvo fechamento</small><strong>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('target_payment_usd') or Decimal('0'))))}</strong></div>
                    <div class='card'><small>Pago conferido</small><strong>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('total_pago_usd') or Decimal('0'))))}</strong></div>
                    <div class='card'><small>Saldo fechamento</small><strong>{escape(_format_usd_pt_br(cast(Decimal, receipt.get('payment_gap_abs_usd') or Decimal('0'))))}</strong></div>
                </div>
                <div class='grid'>
                    <section class='panel'>
                        <h2>Resumo da Operacao</h2>
                        <table class='compact'>
                            <tbody>{compact_details_rows}</tbody>
                        </table>
                    </section>
                </div>
                <div class='split'>
                    <section class='panel'>
                        <h2>Pagamentos</h2>
                        <table>
                            <thead><tr><th>Moeda</th><th>Forma</th><th>Valor moeda</th><th>Valor USD</th></tr></thead>
                            <tbody>{payment_rows}</tbody>
                        </table>
                    </section>
                    <section class='panel'>
                        <h2>Efeito nos Caixas</h2>
                        <ul>{caixa_rows}</ul>
                    </section>
                </div>
                <section class='panel' style='margin-top:10px;'>
                    <h2>Consumo FIFO / Custo</h2>
                    <table>
                        <thead><tr><th>Lote</th><th>Gramas consumidas</th><th>Custo unitario</th><th>Custo consumido</th></tr></thead>
                        <tbody>{consumption_rows}</tbody>
                    </table>
                </section>
            </div>
        </body>
    </html>
    """


def _build_gold_receipt_pdf(
    receipt: Dict[str, Any],
    pdf_url: str,
    *,
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
) -> bytes:
    operation = cast(Dict[str, Any], receipt.get("operation") or {})
    cliente = cast(Optional[Dict[str, Any]], receipt.get("cliente"))
    operador = cast(Optional[Dict[str, Any]], receipt.get("operador"))
    payments = cast(List[Dict[str, Any]], receipt.get("payments") or [])
    consumptions = cast(List[Dict[str, Any]], receipt.get("inventory_consumptions") or [])
    payment_warning = str(receipt.get("payment_audit_warning") or "").strip()
    payment_status_map = _payment_status_message_map(receipt)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A5, leftMargin=8 * mm, rightMargin=8 * mm, topMargin=8 * mm, bottomMargin=8 * mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReceiptTitle", parent=styles["Heading1"], fontSize=13, leading=15, textColor=colors.HexColor("#184f3f"), spaceAfter=2))
    styles.add(ParagraphStyle(name="ReceiptSmall", parent=styles["Normal"], fontSize=7, leading=8.5, textColor=colors.HexColor("#6d6658")))
    story: List[Any] = []

    story.append(Paragraph(f"Recibo Operacional GT-{int(receipt.get('operation_id') or 0)}", styles["ReceiptTitle"]))
    story.append(Paragraph(f"Emitido em {escape(str(receipt.get('created_at') or '-'))}", styles["ReceiptSmall"]))
    if payment_warning:
        story.append(Paragraph(escape(payment_warning), styles["ReceiptSmall"]))
    story.append(Spacer(1, 3 * mm))

    summary_data = [
        ["Cliente", str(operation.get("pessoa") or "-")],
        ["Conta cliente", build_cliente_lookup_meta(cliente) if cliente else "-"],
        ["Operador", str((operador or {}).get("nome") or operation.get("operador_id") or "-")],
        ["Tipo", str(receipt.get("tipo_operacao") or "-").upper()],
        ["Peso", _format_grams_pt_br(cast(Decimal, receipt.get('peso') or Decimal('0')))],
        ["Teor", _format_percent_pt_br(cast(Decimal, receipt.get('teor') or Decimal('0')))],
        ["Ouro fino total", _format_grams_pt_br(cast(Decimal, receipt.get('fine_gold') or Decimal('0')))],
        ["Ouro fino fechado", _format_grams_pt_br(cast(Decimal, receipt.get('closed_fine_gold') or Decimal('0')))],
        ["Ouro fino em aberto", _format_grams_pt_br(cast(Decimal, receipt.get('open_fine_gold') or Decimal('0')))],
        ["Preco USD/g", _format_usd_pt_br(cast(Decimal, receipt.get('preco_usd') or Decimal('0')))],
        ["Total USD", _format_usd_pt_br(cast(Decimal, receipt.get('total_usd') or Decimal('0')))],
        ["Alvo fechamento", _format_usd_pt_br(cast(Decimal, receipt.get('target_payment_usd') or Decimal('0')))],
        ["Pago conferido", _format_usd_pt_br(cast(Decimal, receipt.get('total_pago_usd') or Decimal('0')))],
        ["Saldo fechamento", _format_usd_pt_br(cast(Decimal, receipt.get('payment_gap_abs_usd') or Decimal('0')))],
        ["Fechamento", f"{_format_grams_pt_br(cast(Decimal, receipt.get('fechamento_gramas') or Decimal('0')))} ({operation.get('fechamento_tipo') or '-'})"],
        ["Em aberto", _format_grams_pt_br(cast(Decimal, receipt.get('open_grams') or Decimal('0')))],
        ["Status pagamento", payment_status_map.get(str(receipt.get('payment_status') or ''), '-')],
        ["Observacoes", str(operation.get("observacoes") or "-")],
    ]
    summary_table = Table(summary_data, colWidths=[26 * mm, 88 * mm])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.2),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6d6658")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ded2bd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 3 * mm))

    payment_data = [["Moeda", "Forma", "Valor moeda", "Valor USD"]]
    for item in payments:
        payment_data.append([
            str(item.get("moeda") or "USD").upper(),
            str(item.get("forma_pagamento") or "-"),
            _format_decimal_pt_br(Decimal(str(item.get("valor_moeda") or "0")), 2),
            _format_usd_pt_br(Decimal(str(item.get("valor_usd") or "0"))),
        ])
    if len(payment_data) == 1:
        payment_data.append(["-", "-", "-", "Nenhum pagamento detalhado"])
    payment_table = Table(payment_data, colWidths=[14 * mm, 25 * mm, 32 * mm, 34 * mm])
    payment_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#184f3f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ded2bd")),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(Paragraph("Pagamentos", styles["ReceiptSmall"]))
    story.append(payment_table)
    story.append(Spacer(1, 3 * mm))

    effect_data = [["Efeito nos caixas"]] + [[item] for item in cast(List[str], receipt.get("caixa_effects") or [])]
    effect_table = Table(effect_data, colWidths=[105 * mm])
    effect_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#a36a00")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ded2bd")),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(effect_table)
    story.append(Spacer(1, 3 * mm))

    fifo_data = [["Lote", "Gramas", "Custo unit.", "Custo consumido"]]
    for item in consumptions:
        fifo_data.append([
            str(item.get("lot_id") or "-"),
            _format_grams_pt_br(Decimal(str(item.get("consumed_grams") or "0"))),
            _format_usd_pt_br(Decimal(str(item.get("unit_cost_usd") or "0"))),
            _format_usd_pt_br(Decimal(str(item.get("consumed_cost_usd") or "0"))),
        ])
    if len(fifo_data) == 1:
        fifo_data.append(["-", "-", "-", "Sem consumo FIFO"])
    fifo_table = Table(fifo_data, colWidths=[14 * mm, 24 * mm, 31 * mm, 36 * mm])
    fifo_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#184f3f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ded2bd")),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(Paragraph("FIFO / custo", styles["ReceiptSmall"]))
    story.append(fifo_table)
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(f"PDF de referencia: {pdf_url}", styles["ReceiptSmall"]))

    doc.build(story)
    return buffer.getvalue()