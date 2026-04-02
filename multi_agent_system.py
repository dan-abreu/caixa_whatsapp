from __future__ import annotations

from dataclasses import dataclass
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


class OperationsAgent(BaseAgent):
    name = "operational_guard"
    role = "Rules + Validation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        tx = ctx.request.transaction
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        required = ["tipo_operacao", "peso", "preco_usd"]
        missing = [k for k in required if k not in tx]
        if missing:
            alerts.append(f"Campos obrigatorios ausentes: {', '.join(missing)}")
            actions.append("Bloquear confirmacao ate completar campos obrigatorios")
        else:
            insights.append("Campos essenciais de operacao presentes")

        if "peso" in tx and float(tx.get("peso", 0)) <= 0:
            alerts.append("Peso invalido")
        if "preco_usd" in tx and float(tx.get("preco_usd", 0)) <= 0:
            alerts.append("Preco USD invalido")

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
    role = "FX + Reconciliation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        tx = ctx.request.transaction
        live = ctx.request.live_context
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        peso = float(tx.get("peso", 0) or 0)
        preco = float(tx.get("preco_usd", 0) or 0)
        total = round(peso * preco, 2)
        total_pago = float(tx.get("total_pago_usd", 0) or 0)
        diff = round(total - total_pago, 2)

        insights.append(f"Total operacao estimado: USD {total}")
        insights.append(f"Total pago informado: USD {round(total_pago, 2)}")
        insights.append(f"Diferenca de caixa: USD {diff}")

        if abs(diff) > 250:
            alerts.append("Diferenca de caixa acima do limite de risco (USD 250)")
            actions.append("Exigir dupla confirmacao do supervisor")
        else:
            actions.append("Reconciliacao dentro do limite operacional")

        daily_summary = cast(Optional[Dict[str, Any]], live.get("daily_summary"))
        if isinstance(daily_summary, dict):
            insights.append(
                "Fechamento parcial do dia: "
                f"{daily_summary.get('total_operacoes', 0)} operacoes e "
                f"USD {daily_summary.get('total_diferenca_usd', '0')} de diferenca acumulada"
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
            "Politica alvo: maximizar lucro USD com risco controlado",
            "Politica alvo: minimizar exposicao cambial SRD",
        ]
        actions = [
            "Aplicar limites dinamicos por operador",
            "Aplicar limite de diferenca por tipo_operacao",
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

        if float(tx.get("peso", 0) or 0) > 1000:
            alerts.append("Peso extremamente alto para operacao padrao")
        if float(tx.get("teor", 0) or 0) > 99.99:
            alerts.append("Teor acima do padrao operacional")

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
            "Gerar relatorio diario por operador e por moeda",
            "Monitorar tendencia de diferenca_usd e alertas de risco",
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

        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.88,
            insights=insights,
            actions=actions,
            alerts=[],
        )


def run_multi_agent_orchestration(request: MultiAgentRequest) -> MultiAgentResponse:
    agents: List[BaseAgent] = [
        OperationsAgent(),
        FinanceAgent(),
        MarketAgent(),
        StrategyAgent(),
        FraudAgent(),
        ConversationAgent(),
        BIInsightsAgent(),
    ]

    transcript: List[AgentMessage] = []
    for round_number in range(1, request.rounds + 1):
        for agent in agents:
            msg = agent.analyze(AgentContext(request=request, transcript=transcript), round_number)
            transcript.append(msg)

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

    decisions.append("Aplicar reconciliacao USD e bloqueio de duplicidade por message-id")

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
