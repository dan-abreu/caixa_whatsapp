from decimal import Decimal
from typing import Any, Dict, List, Optional, cast

from .models import AgentContext, AgentMessage, BaseAgent
from .utils import _extract_payments, _fmt_decimal, _safe_ratio, _to_decimal, _z_score


class OperationsAgent(BaseAgent):
    name = "operational_guard"
    role = "Rules + Validation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        tx = ctx.request.transaction
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        required = ["tipo_operacao", "peso"]
        missing = [key for key in required if key not in tx]
        if missing:
            alerts.append(f"Campos obrigatorios ausentes: {', '.join(missing)}")
            actions.append("Bloquear confirmacao ate completar campos obrigatorios")
        else:
            insights.append("Campos essenciais de operacao presentes")

        peso = _to_decimal(tx.get("peso"))
        if peso <= 0:
            alerts.append("Peso invalido")

        tipo_operacao = str(tx.get("tipo_operacao", "")).lower()
        if tipo_operacao not in {"compra", "venda", "cambio"}:
            alerts.append("Tipo de operacao invalido")

        pagamentos_list = _extract_payments(tx)
        if tipo_operacao in {"compra", "venda", "cambio"} and not pagamentos_list:
            alerts.append("Pagamentos ausentes para reconciliacao")

        moedas_validas = {"USD", "EUR", "SRD", "BRL"}
        for item in pagamentos_list:
            moeda = str(item.get("moeda", "USD")).upper()
            valor_moeda = _to_decimal(item.get("valor_moeda"))
            valor_usd = _to_decimal(item.get("valor_usd"))
            if moeda not in moedas_validas:
                alerts.append(f"Moeda de pagamento invalida: {moeda}")
            if valor_moeda < 0 or valor_usd < 0:
                alerts.append("Pagamento com valor negativo")

        preco_usd = _to_decimal(tx.get("preco_usd"), default="-1")
        preco_moeda_valor = _to_decimal(tx.get("preco_moeda_valor"), default="-1")
        has_pricing = (preco_usd > 0) or (preco_moeda_valor > 0)
        if not has_pricing:
            alerts.append("Preco da operacao ausente ou invalido")
        if "preco_usd" in tx and preco_usd <= 0:
            alerts.append("Preco USD invalido")
        if "preco_moeda_valor" in tx and preco_moeda_valor <= 0:
            alerts.append("Preco em moeda-base invalido")

        if not alerts:
            actions.append("Permitir continuidade de validacao financeira")

        return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.92, insights=insights, actions=actions, alerts=alerts)


class FinanceAgent(BaseAgent):
    name = "finance_engine"
    role = "Cashbox + FX Reconciliation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        tx = ctx.request.transaction
        live = ctx.request.live_context
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        tipo_operacao = str(tx.get("tipo_operacao", "")).lower()
        operador_id = str(tx.get("operador_id", tx.get("operador", "desconhecido")))
        peso = _to_decimal(tx.get("peso"))
        preco_usd = _to_decimal(tx.get("preco_usd"))
        total_tx = _to_decimal(tx.get("total_usd", tx.get("valor_total", "0")))
        if total_tx <= 0 and peso > 0 and preco_usd > 0:
            total_tx = peso * preco_usd

        total_pago_tx = _to_decimal(tx.get("total_pago_usd", total_tx))
        pagamentos_list = _extract_payments(tx)
        pagamentos_por_moeda: Dict[str, Decimal] = {}
        total_pago_por_pagamentos_usd = Decimal("0")
        for item in pagamentos_list:
            moeda = str(item.get("moeda", "USD")).upper()
            valor_moeda = _to_decimal(item.get("valor_moeda"))
            valor_usd = _to_decimal(item.get("valor_usd"))
            pagamentos_por_moeda[moeda] = pagamentos_por_moeda.get(moeda, Decimal("0")) + valor_moeda
            total_pago_por_pagamentos_usd += valor_usd

        if pagamentos_por_moeda:
            detalhes = ", ".join(f"{moeda} {_fmt_decimal(valor)}" for moeda, valor in sorted(pagamentos_por_moeda.items()))
            insights.append(f"Pagamentos por caixa/moeda: {detalhes}")

        if pagamentos_list:
            insights.append(f"Total pago (pagamentos): USD {_fmt_decimal(total_pago_por_pagamentos_usd)}")
            if abs(total_pago_por_pagamentos_usd - total_pago_tx) > Decimal("0.05"):
                alerts.append("Inconsistencia entre total_pago_usd e soma dos pagamentos")

        diff = total_tx - total_pago_tx
        insights.extend([
            f"Total operacao (referencia): USD {_fmt_decimal(total_tx)}",
            f"Total pago informado: USD {_fmt_decimal(total_pago_tx)}",
            f"Diferenca (referencia): USD {_fmt_decimal(diff)}",
        ])

        limit = _to_decimal(ctx.request.constraints.get("risk_diff_limit_usd", "250"), default="250")
        dynamic_limit = limit
        learning_snapshot = cast(Optional[Dict[str, Any]], live.get("learning_snapshot"))
        if isinstance(learning_snapshot, dict):
            operator_profiles = learning_snapshot.get("operator_profiles")
            if isinstance(operator_profiles, dict):
                profile = cast(Dict[str, Any], operator_profiles).get(operador_id)
                if isinstance(profile, dict):
                    count = int(profile.get("count", 0) or 0)
                    avg_abs_diff = _to_decimal(profile.get("avg_abs_diff_usd", "0"))
                    if count >= 10 and avg_abs_diff > 0:
                        dynamic_limit = max(limit, avg_abs_diff * Decimal("3"))
                        insights.append(f"Limite dinamico aplicado por historico do operador: USD {_fmt_decimal(dynamic_limit)}")

        if abs(diff) > dynamic_limit:
            alerts.append(f"Diferenca acima do limite de risco (USD {_fmt_decimal(dynamic_limit)})")
            actions.append("Exigir dupla confirmacao do supervisor")
        else:
            actions.append("Reconciliacao dentro do limite operacional")

        if total_tx > 0:
            diff_ratio = abs(_safe_ratio(diff, total_tx))
            insights.append(f"Diferenca relativa: {_fmt_decimal(diff_ratio * Decimal('100'))}%")
            if diff_ratio >= Decimal("0.25"):
                alerts.append("Diferenca relativa alta (>= 25%)")

        saldo_caixa = cast(Optional[Dict[str, Any]], live.get("saldo_caixa"))
        if isinstance(saldo_caixa, dict):
            xau_saldo_atual = _to_decimal(saldo_caixa.get("XAU", "0"))
            xau_projetado = xau_saldo_atual + peso if tipo_operacao == "compra" else xau_saldo_atual - peso if tipo_operacao in {"venda", "cambio"} else xau_saldo_atual
            insights.append(f"Caixa XAU atual/projetado: {_fmt_decimal(xau_saldo_atual, '0.000001')} -> {_fmt_decimal(xau_projetado, '0.000001')}")
            if xau_projetado < 0:
                alerts.append("Caixa XAU ficaria negativo apos a operacao")

            for moeda, valor in pagamentos_por_moeda.items():
                saldo_atual = _to_decimal(saldo_caixa.get(moeda, "0"))
                saldo_proj = saldo_atual - valor if tipo_operacao == "compra" else saldo_atual + valor if tipo_operacao in {"venda", "cambio"} else saldo_atual
                insights.append(f"Caixa {moeda} atual/projetado: {_fmt_decimal(saldo_atual)} -> {_fmt_decimal(saldo_proj)}")
                if saldo_proj < 0:
                    alerts.append(f"Caixa {moeda} ficaria negativo apos a operacao")

        daily_summary = cast(Optional[Dict[str, Any]], live.get("daily_summary"))
        if isinstance(daily_summary, dict):
            insights.append(f"Fechamento parcial do dia: {daily_summary.get('total_operacoes', 0)} operacoes e USD {daily_summary.get('total_diferenca_usd', '0')} de diferenca acumulada (metrica derivada)")

        return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.9, insights=insights, actions=actions, alerts=alerts)


class FraudAgent(BaseAgent):
    name = "fraud_sentinel"
    role = "Anomaly Detection"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        tx = ctx.request.transaction
        live = ctx.request.live_context
        alerts: List[str] = []
        insights: List[str] = []
        actions: List[str] = []

        tipo_operacao = str(tx.get("tipo_operacao", "")).lower()
        peso_val = _to_decimal(tx.get("peso"))
        total_val = _to_decimal(tx.get("total_usd", tx.get("valor_total", "0")))
        abs_diff = abs(_to_decimal(tx.get("diferenca_usd", "0")))

        if float(tx.get("peso", 0) or 0) > 1000:
            alerts.append("Peso extremamente alto para operacao padrao")
        if float(tx.get("teor", 0) or 0) > 99.99:
            alerts.append("Teor acima do padrao operacional")

        learning_snapshot = cast(Optional[Dict[str, Any]], live.get("learning_snapshot"))
        if isinstance(learning_snapshot, dict):
            op_stats = cast(Dict[str, Any], learning_snapshot.get("operations") or {}).get(tipo_operacao)
            if isinstance(op_stats, dict):
                count = int(op_stats.get("count", 0) or 0)
                if count >= 20:
                    peso_z = abs(_z_score(peso_val, _to_decimal(op_stats.get("peso_mean", "0")), _to_decimal(op_stats.get("peso_std", "0"))))
                    total_z = abs(_z_score(total_val, _to_decimal(op_stats.get("total_usd_mean", "0")), _to_decimal(op_stats.get("total_usd_std", "0"))))
                    diff_z = abs(_z_score(abs_diff, _to_decimal(op_stats.get("abs_diff_usd_mean", "0")), _to_decimal(op_stats.get("abs_diff_usd_std", "0"))))
                    insights.append(f"Baseline historico ({tipo_operacao}): {count} amostras")
                    insights.append(f"Outlier scores (z): peso={_fmt_decimal(peso_z)}, total={_fmt_decimal(total_z)}, diff={_fmt_decimal(diff_z)}")
                    if peso_z >= Decimal("3"):
                        alerts.append("Peso fora do padrao historico (z >= 3)")
                    if total_z >= Decimal("3"):
                        alerts.append("Total USD fora do padrao historico (z >= 3)")
                    if diff_z >= Decimal("3"):
                        alerts.append("Diferenca USD fora do padrao historico (z >= 3)")

        if alerts:
            actions.extend(["Marcar operacao para revisao de fraude", "Solicitar evidencias (nota, pesagem, origem)"])
        else:
            insights.append("Nenhuma anomalia critica detectada por heuristica")

        risk_alerts = cast(Optional[List[Dict[str, Any]]], live.get("risk_alerts"))
        if isinstance(risk_alerts, list) and risk_alerts:
            insights.append(f"Ja existem {len(risk_alerts)} alertas de risco no dia atual")
            actions.append("Cruzar esta operacao com alertas recentes antes da liquidacao")

        return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.86, insights=insights, actions=actions, alerts=alerts)