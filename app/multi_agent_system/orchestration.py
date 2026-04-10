from typing import List

from .agents_advisory import BIInsightsAgent, ConversationAgent, MarketAgent, PatternLearningAgent, StrategyAgent
from .agents_operational import FinanceAgent, FraudAgent, OperationsAgent
from .models import AgentContext, AgentMessage, BaseAgent, MultiAgentRequest, MultiAgentResponse


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
                transcript.append(agent.analyze(AgentContext(request=request, transcript=transcript), round_number))
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

    decisions.append("Operacao requer controle reforcado antes da liquidacao" if risks else "Operacao pode seguir com trilha de auditoria padrao")
    if any("fora do padrao historico" in risk.lower() for risk in risks):
        decisions.append("Classificar operacao como anomalia estatistica e exigir revisao manual")
    decisions.append("Aplicar reconciliacao por caixa (XAU/USD/EUR/SRD/BRL) e bloqueio de duplicidade por message-id")
    if request.operation_id is not None:
        decisions.append(f"Vincular parecer multiagente a {request.operation_kind or 'operacao'} #{request.operation_id}")

    return MultiAgentResponse(
        summary=f"Orquestracao multi-agente concluida com {len(transcript)} mensagens internas em {request.rounds} rodada(s).",
        decisions=decisions,
        risks=risks,
        recommendations=recommendations,
        transcript=transcript,
    )