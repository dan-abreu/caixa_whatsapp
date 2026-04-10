import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, cast


def build_whatsapp_caixa_detail_helpers() -> SimpleNamespace:
    def build_caixa_detail_response(
        db: Any,
        currency: str,
        start_iso: str,
        end_iso: str,
        label_periodo: str,
        *,
        format_caixa_movement: Callable[[str, Decimal], str],
        money: Callable[[Decimal], Decimal],
    ) -> Dict[str, Any]:
        currency_up = currency.upper()
        saldo = db.get_saldo_caixa()
        transactions = db.get_extrato_transactions(start_iso, end_iso)
        tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))

        caixa_titles = {
            "XAU": "CAIXA OURO (XAU)",
            "EUR": "CAIXA EURO (EUR)",
            "USD": "CAIXA DOLAR (USD)",
            "SRD": "CAIXA SURINAMES (SRD)",
            "BRL": "CAIXA REAL (BRL)",
        }

        movement_rows: List[Dict[str, Any]] = []
        total_entries = Decimal("0")
        total_exits = Decimal("0")
        total_sale_profit = Decimal("0")

        for tx in transactions:
            tipo = str(tx.get("tipo_operacao") or "").lower()
            if tipo not in {"compra", "venda", "cambio"}:
                continue

            movement = Decimal("0")
            if currency_up == "XAU":
                peso = Decimal(str(tx.get("peso") or "0"))
                if tipo == "compra":
                    movement = peso
                elif tipo in {"venda", "cambio"}:
                    movement = -peso
            else:
                pagamentos_raw = tx.get("pagamentos")
                pagamentos = cast(List[Dict[str, Any]], pagamentos_raw) if isinstance(pagamentos_raw, list) else []
                if pagamentos:
                    for pagamento in pagamentos:
                        moeda = str(pagamento.get("moeda") or "USD").upper()
                        if moeda != currency_up:
                            continue
                        valor_moeda = Decimal(str(pagamento.get("valor_moeda") or "0"))
                        movement += -valor_moeda if tipo == "compra" else valor_moeda
                else:
                    moeda = str(tx.get("moeda") or "USD").upper()
                    if moeda == currency_up:
                        valor_moeda = Decimal(str(tx.get("valor_moeda") or tx.get("total_usd") or "0"))
                        movement = -valor_moeda if tipo == "compra" else valor_moeda

            if movement == 0:
                continue

            raw_dt = str(tx.get("criado_em") or "")
            try:
                dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                dt_local = dt + timedelta(hours=tz_offset_hours)
                data_fmt = dt_local.strftime("%d/%m/%Y %H:%M")
            except Exception:
                data_fmt = raw_dt[:16]

            if movement > 0:
                total_entries += movement
            else:
                total_exits += abs(movement)

            lucro_venda: Optional[Decimal] = None
            if tipo == "venda" and isinstance(tx.get("contexto"), dict):
                ctx_tx = cast(Dict[str, Any], tx.get("contexto") or {})
                lucro_raw = ctx_tx.get("lucro_real_usd")
                if lucro_raw is None:
                    lucro_raw = ctx_tx.get("lucro_ref_usd")
                if lucro_raw is not None:
                    try:
                        lucro_venda = Decimal(str(lucro_raw))
                        total_sale_profit += lucro_venda
                    except (InvalidOperation, TypeError, ValueError):
                        lucro_venda = None

            movement_rows.append(
                {
                    "tx_id": str(tx.get("id") or "-"),
                    "data_fmt": data_fmt,
                    "tipo": tipo.upper(),
                    "movimento": movement,
                    "cliente": str(tx.get("pessoa") or "").strip(),
                    "operador": str(tx.get("operador_id") or "").strip(),
                    "lucro_usd": lucro_venda,
                }
            )

        saldo_atual = Decimal(str(saldo.get(currency_up, "0")))
        lines = [
            f"EXTRATO {caixa_titles.get(currency_up, currency_up)}",
            f"Periodo: {label_periodo}",
            "================================",
        ]

        if movement_rows:
            for index, row in enumerate(movement_rows):
                if index > 0:
                    lines.append("--------------------------------")
                lines.append(f"ID: #{row['tx_id']}  |  {row['data_fmt']}")
                lines.append(f"Tipo:     {row['tipo']}")
                lines.append(f"Cliente:  {row['cliente'][:40] if row['cliente'] else '—'}")
                lines.append(f"Operador: {row['operador'][:40] if row['operador'] else '—'}")
                lines.append(f"Valor:    {format_caixa_movement(currency_up, cast(Decimal, row['movimento']))}")
                lucro_usd = row.get("lucro_usd")
                if isinstance(lucro_usd, Decimal):
                    lines.append(f"Lucro:    USD {money(lucro_usd)}")
        else:
            lines.append("Nenhuma movimentacao neste periodo.")

        lines.extend(
            [
                "================================",
                f"Entradas: {format_caixa_movement(currency_up, total_entries)}",
                f"Saidas:   {format_caixa_movement(currency_up, -total_exits)}",
                f"Saldo:    {format_caixa_movement(currency_up, saldo_atual)}",
            ]
        )
        if movement_rows:
            lines.append(f"Ops:      {len(movement_rows)}")
        if total_sale_profit != 0:
            lines.append(f"Lucro vendas: USD {money(total_sale_profit)}")

        return {
            "mensagem": "\n".join(lines),
            "dados": {
                "intencao": "consultar_relatorio",
                "requested_currency": currency_up,
                "periodo": label_periodo,
                "movimentos": len(movement_rows),
                "saldo_atual": str(saldo_atual),
            },
        }

    return SimpleNamespace(build_caixa_detail_response=build_caixa_detail_response)