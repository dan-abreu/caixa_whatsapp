from decimal import Decimal
from html import escape
from typing import Any, Callable, Dict, List, Optional, cast
from urllib.parse import quote

from app.core.formatting import (
    _format_decimal_pt_br,
    _format_grams_pt_br,
    _format_percent_pt_br,
    _format_usd_pt_br,
)

from .context import _payment_status_message_map


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
                * {{ box-sizing: border-box; }} body {{ margin: 0; padding: 14px; background: #efe7d7; color: var(--ink); font-family: 'Segoe UI', sans-serif; }}
                .sheet {{ width: min(720px, 100%); margin: 0 auto; background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 16px; box-shadow: 0 18px 40px rgba(0,0,0,.08); }}
                .head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 12px; }} .head h1 {{ margin: 0 0 2px; font-size: 22px; line-height: 1.1; }} .head p {{ margin: 0; color: var(--muted); font-size: 12px; }}
                .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }} .btn {{ display: inline-flex; align-items: center; justify-content: center; text-decoration: none; padding: 8px 11px; border-radius: 11px; border: 1px solid var(--line); background: white; color: var(--accent); font-weight: 700; cursor: pointer; font-size: 12px; }} .btn.primary {{ background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: white; border: 0; }}
                .grid {{ display: grid; grid-template-columns: 1fr; gap: 10px; }} .split {{ display: grid; grid-template-columns: 1.2fr .8fr; gap: 10px; }} .panel {{ border: 1px solid var(--line); border-radius: 16px; padding: 10px; background: white; }} .panel h2 {{ margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
                .notice {{ margin-bottom: 10px; padding: 10px 12px; border-radius: 12px; border: 1px solid var(--line); font-size: 12px; }} .notice.warning {{ background: #fff5df; border-color: #e2c78f; color: #7d5a00; }}
                table {{ width: 100%; border-collapse: collapse; font-size: 11px; }} th, td {{ text-align: left; padding: 5px 4px; border-bottom: 1px solid var(--line); vertical-align: top; }} th {{ color: var(--muted); text-transform: uppercase; font-size: 10px; letter-spacing: .06em; white-space: nowrap; }}
                ul {{ margin: 0; padding-left: 15px; font-size: 11px; }} li {{ margin: 0 0 3px; }} .totals {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 8px; margin-bottom: 10px; }} .card {{ border: 1px solid var(--line); border-radius: 12px; padding: 8px; background: #fff; }} .card small {{ display: block; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .06em; font-size: 9px; }} .card strong {{ font-size: 16px; line-height: 1.1; }} .compact td[colspan='3'], .compact td[colspan='4'] {{ font-size: 10px; }}
                @page {{ size: A5 portrait; margin: 6mm; }}
                @media print {{ body {{ background: white; padding: 0; font-size: 10px; }} .sheet {{ box-shadow: none; border: 0; width: 100%; padding: 0; border-radius: 0; }} .actions {{ display: none; }} .panel {{ page-break-inside: avoid; }} .split {{ grid-template-columns: 1fr .8fr; gap: 6px; }} .totals {{ gap: 5px; margin-bottom: 6px; }} .card {{ padding: 6px; }} .card strong {{ font-size: 13px; }} th, td {{ padding: 3px 2px; font-size: 9px; }} .head h1 {{ font-size: 17px; }} .head p, .head span, .head strong {{ font-size: 10px; }} ul {{ font-size: 9px; }} }}
                @media (max-width: 760px) {{ .split, .totals {{ grid-template-columns: 1fr 1fr; }} .head {{ display: grid; }} }}
            </style>
        </head>
        <body>
            <div class='sheet'>
                <div class='head'>
                    <div><h1>Recibo Operacional</h1><p>Comprovante detalhado da operacao GT-{int(receipt.get('operation_id') or 0)}</p></div>
                    <div><strong>{escape(str(receipt.get('created_at') or '-'))}</strong><br><span style='color:var(--muted)'>Status: {escape(str(operation.get('status') or 'registrada').upper())}</span></div>
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
                <div class='grid'><section class='panel'><h2>Resumo da Operacao</h2><table class='compact'><tbody>{compact_details_rows}</tbody></table></section></div>
                <div class='split'>
                    <section class='panel'><h2>Pagamentos</h2><table><thead><tr><th>Moeda</th><th>Forma</th><th>Valor moeda</th><th>Valor USD</th></tr></thead><tbody>{payment_rows}</tbody></table></section>
                    <section class='panel'><h2>Efeito nos Caixas</h2><ul>{caixa_rows}</ul></section>
                </div>
                <section class='panel' style='margin-top:10px;'><h2>Consumo FIFO / Custo</h2><table><thead><tr><th>Lote</th><th>Gramas consumidas</th><th>Custo unitario</th><th>Custo consumido</th></tr></thead><tbody>{consumption_rows}</tbody></table></section>
            </div>
        </body>
    </html>
    """