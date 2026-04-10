from io import BytesIO
from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, Optional, cast

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.formatting import _format_decimal_pt_br, _format_grams_pt_br, _format_percent_pt_br, _format_usd_pt_br

from .context import _payment_status_message_map


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