import os
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def build_whatsapp_report_helpers() -> SimpleNamespace:
    def build_caixa_response(
        db: Any,
        requested_currency: Optional[str] = None,
        *,
        build_day_range: Callable[[Optional[str]], Dict[str, str]],
        build_gold_caixa_metrics_from_pending_grams: Callable[[Decimal, Decimal], Dict[str, Decimal]],
    ) -> Dict[str, Any]:
        day = build_day_range(None)
        summary = db.get_daily_gold_summary(day["start"], day["end"])
        saldo = db.get_saldo_caixa()

        ops_hoje = int(summary.get("total_operacoes", 0) or 0)
        saldo_xau = Decimal(str(saldo.get("XAU", "0")))
        saldo_eur = Decimal(str(saldo.get("EUR", "0")))
        saldo_usd = Decimal(str(saldo.get("USD", "0")))
        saldo_srd = Decimal(str(saldo.get("SRD", "0")))
        saldo_brl = Decimal(str(saldo.get("BRL", "0")))
        gold_caixa_metrics = build_gold_caixa_metrics_from_pending_grams(saldo_xau, db.get_gold_pending_closure_grams())
        ouro_pendente = gold_caixa_metrics["ouro_pendente"]
        ouro_proprio = gold_caixa_metrics["ouro_proprio"]

        def situacao_txt(val: Decimal) -> str:
            return "entrou mais 💰" if val > 0 else ("nada" if val == 0 else "saiu mais 📉")

        if requested_currency:
            moeda = requested_currency.upper()
            if moeda == "XAU":
                resposta = (
                    f"💰 CAIXA OURO (XAU)\n"
                    f"Data: {day['date']}\n"
                    f"Operações hoje: {ops_hoje}\n"
                    "════════════════════════════════\n"
                    f"Ouro fisico em caixa: {saldo_xau:,.3f} g\n"
                    f"Ouro de terceiros pendente: {ouro_pendente:,.3f} g\n"
                    f"Posicao propria em ouro: {ouro_proprio:,.3f} g\n"
                    f"Situacao: {situacao_txt(saldo_xau)}\n"
                    "════════════════════════════════"
                )
            elif moeda == "EUR":
                resposta = (
                    f"🇪🇺 CAIXA EURO (EUR)\n"
                    f"Data: {day['date']}\n"
                    f"Operações hoje: {ops_hoje}\n"
                    "════════════════════════════════\n"
                    f"Saldo: EUR {saldo_eur:,.2f}\n"
                    f"Situacao: {situacao_txt(saldo_eur)}\n"
                    "════════════════════════════════"
                )
            elif moeda == "USD":
                resposta = (
                    f"🇺🇸 CAIXA DÓLAR (USD)\n"
                    f"Data: {day['date']}\n"
                    f"Operações hoje: {ops_hoje}\n"
                    "════════════════════════════════\n"
                    f"Saldo: $ {saldo_usd:,.2f}\n"
                    f"Situacao: {situacao_txt(saldo_usd)}\n"
                    "════════════════════════════════"
                )
            elif moeda == "SRD":
                resposta = (
                    f"🇸🇷 CAIXA SURINAMÊS (SRD)\n"
                    f"Data: {day['date']}\n"
                    f"Operações hoje: {ops_hoje}\n"
                    "════════════════════════════════\n"
                    f"Saldo: SRD {saldo_srd:,.2f}\n"
                    f"Situacao: {situacao_txt(saldo_srd)}\n"
                    "════════════════════════════════"
                )
            elif moeda == "BRL":
                resposta = (
                    f"🇧🇷 CAIXA REAL (BRL)\n"
                    f"Data: {day['date']}\n"
                    f"Operações hoje: {ops_hoje}\n"
                    "════════════════════════════════\n"
                    f"Saldo: R$ {saldo_brl:,.2f}\n"
                    f"Situacao: {situacao_txt(saldo_brl)}\n"
                    "════════════════════════════════"
                )
            else:
                resposta = f"Moeda {moeda} não reconhecida. Digite: xau, eur, usd, srd ou brl"
        else:
            resposta = (
                f"📊 POSICAO CONSOLIDADA DOS 5 CAIXAS\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════════════════\n"
                f"1) 💰 OURO (XAU):      {saldo_xau:>10,.3f} g\n"
                f"   Situacao: {situacao_txt(saldo_xau)}\n"
                f"   Ouro de terceiros: {ouro_pendente:>8,.3f} g\n"
                f"   Posicao propria:   {ouro_proprio:>8,.3f} g\n"
                "\n"
                f"2) 🇪🇺 EURO (EUR):      EUR {saldo_eur:>10,.2f}\n"
                f"   Situacao: {situacao_txt(saldo_eur)}\n"
                "\n"
                f"3) 🇺🇸 DÓLAR (USD):     $ {saldo_usd:>12,.2f}\n"
                f"   Situacao: {situacao_txt(saldo_usd)}\n"
                "\n"
                f"4) 🇸🇷 SURINAMÊS (SRD): SRD {saldo_srd:>10,.2f}\n"
                f"   Situacao: {situacao_txt(saldo_srd)}\n"
                "\n"
                f"5) 🇧🇷 REAL (BRL):      R$ {saldo_brl:>11,.2f}\n"
                f"   Situacao: {situacao_txt(saldo_brl)}\n"
                "════════════════════════════════════════════\n"
                "Legenda operacional:\n"
                "- 💰 entrou mais: houve incremento liquido neste caixa\n"
                "- 📉 saiu mais: houve reducao liquida neste caixa\n"
                "- nada: movimentacao equilibrada\n"
                "- Ouro de terceiros: ouro de cliente ainda nao liquidado\n"
                "- Posicao propria: ouro fisico em caixa deduzido do saldo de terceiros\n"
                "\nPara detalhar um caixa, responda:\n"
                "1 (ouro) | 2 (euro) | 3 (dólar) | 4 (surinamês) | 5 (real)"
            )

        return {
            "mensagem": resposta,
            "dados": {
                "intencao": "consultar_relatorio",
                "date": day["date"],
                "saldo_xau": str(saldo_xau),
                "ouro_pendente": str(ouro_pendente),
                "ouro_proprio": str(ouro_proprio),
                "saldo_eur": str(saldo_eur),
                "saldo_usd": str(saldo_usd),
                "saldo_srd": str(saldo_srd),
                "saldo_brl": str(saldo_brl),
                "ops_hoje": ops_hoje,
                "summary": summary,
                "requested_currency": requested_currency,
            },
        }

    def build_extrato_response(
        db: Any,
        start_iso: str,
        end_iso: str,
        label_periodo: str,
        transactions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        statement_transactions = transactions if transactions is not None else db.get_extrato_transactions(start_iso, end_iso)
        tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
        moeda_simbolo: Dict[str, str] = {"USD": "$", "EUR": "EUR ", "SRD": "SRD ", "BRL": "R$"}

        linhas: List[str] = [
            "===== EXTRATO =====",
            f"Periodo: {label_periodo}",
            f"Total: {len(statement_transactions)} operac{'oes' if len(statement_transactions) != 1 else 'ao'}",
            "====================",
        ]

        total_compra_g = Decimal("0")
        total_venda_g = Decimal("0")
        total_compra_usd = Decimal("0")
        total_venda_usd = Decimal("0")

        for i, t in enumerate(statement_transactions, 1):
            tipo = str(t.get("tipo_operacao") or "").upper()
            data_hora_raw = str(t.get("criado_em") or "")
            try:
                dt = datetime.fromisoformat(data_hora_raw.replace("Z", "+00:00"))
                dt_local = dt + timedelta(hours=tz_offset_hours)
                data_fmt = dt_local.strftime("%d/%m %H:%M")
            except Exception:
                data_fmt = data_hora_raw[:16]

            peso = Decimal(str(t.get("peso") or "0"))
            preco_usd = Decimal(str(t.get("preco_usd") or "0"))
            total_usd_val = Decimal(str(t.get("total_usd") or "0"))
            total_pago = Decimal(str(t.get("total_pago_usd") or total_usd_val))
            diferenca = Decimal(str(t.get("diferenca_usd") or "0"))
            pessoa = str(t.get("pessoa") or "").strip()
            observacoes = str(t.get("observacoes") or "").strip()
            status = str(t.get("status") or "registrada")
            source = str(t.get("source") or "transacoes")
            tid = t.get("id")
            id_prefixado = f"GT-{tid}" if source == "gold_transactions" else f"T-{tid}"

            linhas.append("--------------------")
            status_tag = f" [{status.upper()}]" if status not in ("registrada", "") else ""
            linhas.append(f"#{i} | {data_fmt} | {tipo}{status_tag}")
            if tid:
                linhas.append(f"ID: {id_prefixado}")
            if peso > 0:
                linhas.append(f"Peso: {peso:,.3f} g")
            if preco_usd > 0:
                linhas.append(f"Preco: ${preco_usd:,.2f}/g")
            linhas.append(f"Total ref: ${total_usd_val:,.2f}")

            pagamentos: List[Dict[str, Any]] = t.get("pagamentos") or []
            if pagamentos:
                for p in pagamentos:
                    moeda = str(p.get("moeda") or "USD").upper()
                    valor_m = Decimal(str(p.get("valor_moeda") or "0"))
                    cambio = Decimal(str(p.get("cambio_para_usd") or "1"))
                    simbolo = moeda_simbolo.get(moeda, f"{moeda} ")
                    if moeda == "USD":
                        linhas.append(f"Pago: {simbolo}{valor_m:,.2f}")
                    else:
                        linhas.append(f"Pago: {simbolo}{valor_m:,.2f} (cambio: {cambio:,.4f})")
            else:
                moeda = str(t.get("moeda") or "USD").upper()
                valor_m_raw = t.get("valor_moeda")
                if valor_m_raw:
                    valor_m = Decimal(str(valor_m_raw))
                    cambio_raw = t.get("cambio_para_usd")
                    cambio = Decimal(str(cambio_raw)) if cambio_raw else Decimal("1")
                    simbolo = moeda_simbolo.get(moeda, f"{moeda} ")
                    if moeda == "USD":
                        linhas.append(f"Pago: {simbolo}{valor_m:,.2f}")
                    else:
                        linhas.append(f"Pago: {simbolo}{valor_m:,.2f} (cambio: {cambio:,.4f})")
                else:
                    linhas.append(f"Pago: ${total_pago:,.2f}")

            if diferenca != 0:
                sinal = "+" if diferenca > 0 else ""
                linhas.append(f"Diferenca: {sinal}${diferenca:,.2f}")
            if pessoa:
                linhas.append(f"Pessoa: {pessoa}")
            if observacoes:
                linhas.append(f"Obs: {observacoes[:60]}")

            if tipo == "COMPRA":
                total_compra_g += peso
                total_compra_usd += total_usd_val
            elif tipo in ("VENDA", "CAMBIO"):
                total_venda_g += peso
                total_venda_usd += total_usd_val

        linhas.append("====================")
        linhas.append("RESUMO:")
        if not statement_transactions:
            linhas.append("Nenhuma operação encontrada.")
        else:
            if total_compra_g > 0:
                n_c = sum(1 for x in statement_transactions if str(x.get("tipo_operacao") or "").upper() == "COMPRA")
                linhas.append(f"Compras: {n_c} op | {total_compra_g:,.3f} g | ${total_compra_usd:,.2f}")
            if total_venda_g > 0:
                n_v = sum(1 for x in statement_transactions if str(x.get("tipo_operacao") or "").upper() in ("VENDA", "CAMBIO"))
                linhas.append(f"Vendas:  {n_v} op | {total_venda_g:,.3f} g | ${total_venda_usd:,.2f}")
            saldo_g = total_compra_g - total_venda_g
            sinal_g = "+" if saldo_g >= 0 else ""
            linhas.append(f"Saldo ouro: {sinal_g}{saldo_g:,.3f} g")
        linhas.append("====================")

        return {
            "mensagem": "\n".join(linhas),
            "dados": {
                "intencao": "extrato",
                "periodo": label_periodo,
                "total_operacoes": len(statement_transactions),
            },
        }

    return SimpleNamespace(
        build_caixa_response=build_caixa_response,
        build_extrato_response=build_extrato_response,
    )