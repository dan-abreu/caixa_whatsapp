from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, cast

from pydantic import BaseModel, Field


class MultiAgentRequest(BaseModel):
    objective: str = Field(..., min_length=3)
    operation: Optional[str] = None
    operation_id: Optional[int] = None
    operation_kind: Optional[str] = None
    source_message_id: Optional[str] = None
    transaction: Dict[str, Any] = Field(default_factory=dict)
    market_snapshot: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    live_context: Dict[str, Any] = Field(default_factory=dict)
    rounds: int = Field(default=2, ge=1, le=4)


class AgentMessage(BaseModel):
    agent: str
    role: str
    round: int
    confidence: float
    insights: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    alerts: List[str] = Field(default_factory=list)


class MultiAgentResponse(BaseModel):
    summary: str
    decisions: List[str]
    risks: List[str]
    recommendations: List[str]
    transcript: List[AgentMessage]


@dataclass
class AgentContext:
    request: MultiAgentRequest
    transcript: List[AgentMessage]


class BaseAgent:
    name = "base"
    role = "generic"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        raise NotImplementedError


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _fmt_decimal(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places)))


def _safe_ratio(numerator: Decimal, denominator: Decimal, default: str = "0") -> Decimal:
    if denominator == 0:
        return Decimal(default)
    return numerator / denominator


def _z_score(value: Decimal, mean: Decimal, std: Decimal) -> Decimal:
    if std <= 0:
        return Decimal("0")
    return (value - mean) / std


def _extract_payments(tx: Dict[str, Any]) -> List[Dict[str, Any]]:
    pagamentos = tx.get("pagamentos")
    out: List[Dict[str, Any]] = []
    if not isinstance(pagamentos, list):
        return out
    for raw_item in cast(List[Any], pagamentos):
        if isinstance(raw_item, dict):
            out.append(cast(Dict[str, Any], raw_item))
    return out


class OperationsAgent(BaseAgent):
    name = "operational_guard"
    role = "Rules + Validation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        tx = ctx.request.transaction
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        required = ["tipo_operacao", "peso"]
        missing = [k for k in required if k not in tx]
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

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.92,
            insights=insights,
            actions=actions,
            alerts=alerts,
        )


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
            detalhes = ", ".join(
                f"{moeda} {_fmt_decimal(valor)}" for moeda, valor in sorted(pagamentos_por_moeda.items())
            )
            insights.append(f"Pagamentos por caixa/moeda: {detalhes}")

        if pagamentos_list:
            insights.append(f"Total pago (pagamentos): USD {_fmt_decimal(total_pago_por_pagamentos_usd)}")
            if abs(total_pago_por_pagamentos_usd - total_pago_tx) > Decimal("0.05"):
                alerts.append("Inconsistencia entre total_pago_usd e soma dos pagamentos")

        diff = total_tx - total_pago_tx
        insights.append(f"Total operacao (referencia): USD {_fmt_decimal(total_tx)}")
        insights.append(f"Total pago informado: USD {_fmt_decimal(total_pago_tx)}")
        insights.append(f"Diferenca (referencia): USD {_fmt_decimal(diff)}")

        limit = _to_decimal(ctx.request.constraints.get("risk_diff_limit_usd", "250"), default="250")
        dynamic_limit = limit
        learning_snapshot = cast(Optional[Dict[str, Any]], live.get("learning_snapshot"))
        if isinstance(learning_snapshot, dict):
            operator_profiles = learning_snapshot.get("operator_profiles")
            if isinstance(operator_profiles, dict):
                operator_profiles_dict = cast(Dict[str, Any], operator_profiles)
                profile = operator_profiles_dict.get(operador_id)
                if isinstance(profile, dict):
                    profile_dict = cast(Dict[str, Any], profile)
                    count = int(profile_dict.get("count", 0) or 0)
                    avg_abs_diff = _to_decimal(profile_dict.get("avg_abs_diff_usd", "0"))
                    if count >= 10 and avg_abs_diff > 0:
                        dynamic_limit = max(limit, avg_abs_diff * Decimal("3"))
                        insights.append(
                            "Limite dinamico aplicado por historico do operador: "
                            f"USD {_fmt_decimal(dynamic_limit)}"
                        )
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
            if tipo_operacao == "compra":
                xau_projetado = xau_saldo_atual + peso
            elif tipo_operacao in {"venda", "cambio"}:
                xau_projetado = xau_saldo_atual - peso
            else:
                xau_projetado = xau_saldo_atual

            insights.append(
                f"Caixa XAU atual/projetado: {_fmt_decimal(xau_saldo_atual, '0.000001')} -> {_fmt_decimal(xau_projetado, '0.000001')}"
            )
            if xau_projetado < 0:
                alerts.append("Caixa XAU ficaria negativo apos a operacao")

            for moeda, valor in pagamentos_por_moeda.items():
                saldo_atual = _to_decimal(saldo_caixa.get(moeda, "0"))
                if tipo_operacao == "compra":
                    saldo_proj = saldo_atual - valor
                elif tipo_operacao in {"venda", "cambio"}:
                    saldo_proj = saldo_atual + valor
                else:
                    saldo_proj = saldo_atual

                insights.append(
                    f"Caixa {moeda} atual/projetado: {_fmt_decimal(saldo_atual)} -> {_fmt_decimal(saldo_proj)}"
                )
                if saldo_proj < 0:
                    alerts.append(f"Caixa {moeda} ficaria negativo apos a operacao")

        daily_summary = cast(Optional[Dict[str, Any]], live.get("daily_summary"))
        if isinstance(daily_summary, dict):
            insights.append(
                "Fechamento parcial do dia: "
                f"{daily_summary.get('total_operacoes', 0)} operacoes e "
                f"USD {daily_summary.get('total_diferenca_usd', '0')} de diferenca acumulada (metrica derivada)"
            )

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.9,
            insights=insights,
            actions=actions,
            alerts=alerts,
        )


class MarketAgent(BaseAgent):
    name = "market_forecast"
    role = "Time-series Strategy"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        market = ctx.request.market_snapshot
        insights: List[str] = []
        actions: List[str] = []

        trend = str(market.get("gold_trend", "neutral")).lower()
        fx_trend = str(market.get("srd_usd_trend", "neutral")).lower()

        insights.append(f"Tendencia ouro: {trend}")
        insights.append(f"Tendencia SRD/USD: {fx_trend}")

        if trend == "up":
            actions.append("Priorizar fechamento rapido de compras com margem protegida")
        elif trend == "down":
            actions.append("Reduzir exposicao de estoque de ouro")
        else:
            actions.append("Manter estrategia neutra de estoque")

        if fx_trend == "up":
            actions.append("Aumentar controle em recebimentos SRD")

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.72,
            insights=insights,
            actions=actions,
            alerts=[],
        )


class StrategyAgent(BaseAgent):
    name = "strategy_optimizer"
    role = "RL-style Policy"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        insights = [
            "Politica alvo: manter caixa por moeda sem saldo negativo",
            "Politica alvo: reduzir dependencia de reconciliacao apenas em USD",
        ]
        actions = [
            "Aplicar limites dinamicos por operador",
            "Aplicar limite de diferenca por tipo_operacao e por moeda de pagamento",
        ]
        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.78,
            insights=insights,
            actions=actions,
            alerts=[],
        )


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
            op_stats_all = learning_snapshot.get("operations")
            if isinstance(op_stats_all, dict):
                op_stats_all_dict = cast(Dict[str, Any], op_stats_all)
                op_stats = op_stats_all_dict.get(tipo_operacao)
                if isinstance(op_stats, dict):
                    op_stats_dict = cast(Dict[str, Any], op_stats)
                    count = int(op_stats_dict.get("count", 0) or 0)
                    if count >= 20:
                        peso_mean = _to_decimal(op_stats_dict.get("peso_mean", "0"))
                        peso_std = _to_decimal(op_stats_dict.get("peso_std", "0"))
                        total_mean = _to_decimal(op_stats_dict.get("total_usd_mean", "0"))
                        total_std = _to_decimal(op_stats_dict.get("total_usd_std", "0"))
                        diff_mean = _to_decimal(op_stats_dict.get("abs_diff_usd_mean", "0"))
                        diff_std = _to_decimal(op_stats_dict.get("abs_diff_usd_std", "0"))

                        peso_z = abs(_z_score(peso_val, peso_mean, peso_std))
                        total_z = abs(_z_score(total_val, total_mean, total_std))
                        diff_z = abs(_z_score(abs_diff, diff_mean, diff_std))

                        insights.append(
                            f"Baseline historico ({tipo_operacao}): {count} amostras"
                        )
                        insights.append(
                            "Outlier scores (z): "
                            f"peso={_fmt_decimal(peso_z)}, total={_fmt_decimal(total_z)}, diff={_fmt_decimal(diff_z)}"
                        )

                        if peso_z >= Decimal("3"):
                            alerts.append("Peso fora do padrao historico (z >= 3)")
                        if total_z >= Decimal("3"):
                            alerts.append("Total USD fora do padrao historico (z >= 3)")
                        if diff_z >= Decimal("3"):
                            alerts.append("Diferenca USD fora do padrao historico (z >= 3)")

        if alerts:
            actions.append("Marcar operacao para revisao de fraude")
            actions.append("Solicitar evidencias (nota, pesagem, origem)")
        else:
            insights.append("Nenhuma anomalia critica detectada por heuristica")

        risk_alerts = cast(Optional[List[Dict[str, Any]]], live.get("risk_alerts"))
        if isinstance(risk_alerts, list) and risk_alerts:
            insights.append(f"Ja existem {len(risk_alerts)} alertas de risco no dia atual")
            actions.append("Cruzar esta operacao com alertas recentes antes da liquidacao")

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.86,
            insights=insights,
            actions=actions,
            alerts=alerts,
        )


class ConversationAgent(BaseAgent):
    name = "conversation_orchestrator"
    role = "LLM UX + Guardrails"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        insights = [
            "Fluxo conversacional deve ser guiado com perguntas unicas por etapa",
            "Confirmacao final obrigatoria antes de gravar no banco",
        ]
        actions = [
            "Responder em linguagem do usuario com resumo claro",
            "Quando faltar dado, pedir apenas o proximo campo obrigatorio",
        ]
        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.93,
            insights=insights,
            actions=actions,
            alerts=[],
        )


class BIInsightsAgent(BaseAgent):
    name = "bi_analyst"
    role = "Business Intelligence"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        live = ctx.request.live_context
        insights = [
            "Gerar relatorio diario por operador e por caixa/moeda",
            "Monitorar tendencia de diferenca_usd como metrica derivada e saldos por caixa",
        ]
        actions = [
            "Acionar dashboard de fechamento com KPI de risco",
            "Emitir resumo semanal de margem e spread",
        ]

        top_divergences = cast(Optional[List[Dict[str, Any]]], live.get("top_divergences"))
        if isinstance(top_divergences, list) and top_divergences:
            insights.append(f"Top divergencias monitoradas: {len(top_divergences)} casos recentes")

        recent_runs = cast(Optional[List[Dict[str, Any]]], live.get("recent_runs"))
        if isinstance(recent_runs, list) and recent_runs:
            insights.append(f"Memoria operacional: {len(recent_runs)} analises multiagente recentes disponiveis")

        learning_snapshot = cast(Optional[Dict[str, Any]], live.get("learning_snapshot"))
        if isinstance(learning_snapshot, dict):
            total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
            if total_samples > 0:
                insights.append(f"Memoria de aprendizado: {total_samples} transacoes historicas agregadas")

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.88,
            insights=insights,
            actions=actions,
            alerts=[],
        )


class PatternLearningAgent(BaseAgent):
    name = "pattern_learner"
    role = "Pattern Learning + Adaptation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        live = ctx.request.live_context
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        learning_snapshot = cast(Optional[Dict[str, Any]], live.get("learning_snapshot"))
        if not isinstance(learning_snapshot, dict):
            alerts.append("Snapshot de aprendizado indisponivel")
            actions.append("Operar com regras conservadoras e ampliar coleta historica")
            return AgentMessage(
                agent=self.name,
                role=self.role,
                round=round_number,
                confidence=0.55,
                insights=insights,
                actions=actions,
                alerts=alerts,
            )

        total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
        insights.append(f"Base historica para aprendizagem: {total_samples} amostras")

        if total_samples < 30:
            alerts.append("Amostragem historica baixa para aprendizagem estatistica robusta")
            actions.append("Manter thresholds conservadores ate aumentar base historica")
        else:
            actions.append("Aplicar validacao por padrao historico nas operacoes de alto valor")

        currency_mix = learning_snapshot.get("currency_mix")
        if isinstance(currency_mix, dict) and currency_mix:
            currency_mix_dict = cast(Dict[str, Any], currency_mix)
            top_currency = max(currency_mix_dict.items(), key=lambda it: int(it[1]))[0]
            insights.append(f"Moeda mais recorrente nos pagamentos: {top_currency}")
            actions.append("Reforcar monitoramento de reconciliacao na moeda dominante")

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.84,
            insights=insights,
            actions=actions,
            alerts=alerts,
        )


def run_multi_agent_orchestration(request: MultiAgentRequest) -> MultiAgentResponse:
    agents: List[BaseAgent] = [
        OperationsAgent(),
        FinanceAgent(),
        MarketAgent(),
        StrategyAgent(),
        FraudAgent(),
        ConversationAgent(),
        PatternLearningAgent(),
        BIInsightsAgent(),
    ]

    transcript: List[AgentMessage] = []
    for round_number in range(1, request.rounds + 1):
        for agent in agents:
            try:
                msg = agent.analyze(AgentContext(request=request, transcript=transcript), round_number)
                transcript.append(msg)
            except Exception as exc:
                transcript.append(
                    AgentMessage(
                        agent=agent.name,
                        role="fail-safe",
                        round=round_number,
                        confidence=0.0,
                        insights=[],
                        actions=["Continuar orquestracao com agentes restantes"],
                        alerts=[f"Falha interna do agente {agent.name}: {str(exc)}"],
                    )
                )

    risks: List[str] = []
    decisions: List[str] = []
    recommendations: List[str] = []

    for msg in transcript:
        for alert in msg.alerts:
            if alert not in risks:
                risks.append(alert)
        for action in msg.actions:
            if action not in recommendations:
                recommendations.append(action)

    if risks:
        decisions.append("Operacao requer controle reforcado antes da liquidacao")
    else:
        decisions.append("Operacao pode seguir com trilha de auditoria padrao")

    if any("fora do padrao historico" in r.lower() for r in risks):
        decisions.append("Classificar operacao como anomalia estatistica e exigir revisao manual")

    decisions.append("Aplicar reconciliacao por caixa (XAU/USD/EUR/SRD/BRL) e bloqueio de duplicidade por message-id")

    if request.operation_id is not None:
        target = request.operation_kind or "operacao"
        decisions.append(f"Vincular parecer multiagente a {target} #{request.operation_id}")

    summary = (
        "Orquestracao multi-agente concluida com "
        f"{len(transcript)} mensagens internas em {request.rounds} rodada(s)."
    )

    return MultiAgentResponse(
        summary=summary,
        decisions=decisions,
        risks=risks,
        recommendations=recommendations,
        transcript=transcript,
    )
