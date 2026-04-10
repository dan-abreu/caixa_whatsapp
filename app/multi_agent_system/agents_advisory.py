from typing import Any, Dict, List, Optional, cast

from .models import AgentContext, AgentMessage, BaseAgent


class MarketAgent(BaseAgent):
    name = "market_forecast"
    role = "Time-series Strategy"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        market = ctx.request.market_snapshot
        insights = [
            f"Tendencia ouro: {str(market.get('gold_trend', 'neutral')).lower()}",
            f"Tendencia SRD/USD: {str(market.get('srd_usd_trend', 'neutral')).lower()}",
        ]
        actions: List[str] = []

        trend = str(market.get("gold_trend", "neutral")).lower()
        fx_trend = str(market.get("srd_usd_trend", "neutral")).lower()
        if trend == "up":
            actions.append("Priorizar fechamento rapido de compras com margem protegida")
        elif trend == "down":
            actions.append("Reduzir exposicao de estoque de ouro")
        else:
            actions.append("Manter estrategia neutra de estoque")
        if fx_trend == "up":
            actions.append("Aumentar controle em recebimentos SRD")

        return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.72, insights=insights, actions=actions, alerts=[])


class StrategyAgent(BaseAgent):
    name = "strategy_optimizer"
    role = "RL-style Policy"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.78,
            insights=[
                "Politica alvo: manter caixa por moeda sem saldo negativo",
                "Politica alvo: reduzir dependencia de reconciliacao apenas em USD",
            ],
            actions=[
                "Aplicar limites dinamicos por operador",
                "Aplicar limite de diferenca por tipo_operacao e por moeda de pagamento",
            ],
            alerts=[],
        )


class ConversationAgent(BaseAgent):
    name = "conversation_orchestrator"
    role = "LLM UX + Guardrails"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        return AgentMessage(
            agent=self.name,
            role=self.role,
            round=round_number,
            confidence=0.93,
            insights=[
                "Fluxo conversacional deve ser guiado com perguntas unicas por etapa",
                "Confirmacao final obrigatoria antes de gravar no banco",
            ],
            actions=[
                "Responder em linguagem do usuario com resumo claro",
                "Quando faltar dado, pedir apenas o proximo campo obrigatorio",
            ],
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

        return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.88, insights=insights, actions=actions, alerts=[])


class PatternLearningAgent(BaseAgent):
    name = "pattern_learner"
    role = "Pattern Learning + Adaptation"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        live = ctx.request.live_context
        learning_snapshot = cast(Optional[Dict[str, Any]], live.get("learning_snapshot"))
        insights: List[str] = []
        actions: List[str] = []
        alerts: List[str] = []

        if not isinstance(learning_snapshot, dict):
            alerts.append("Snapshot de aprendizado indisponivel")
            actions.append("Operar com regras conservadoras e ampliar coleta historica")
            return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.55, insights=insights, actions=actions, alerts=alerts)

        total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
        insights.append(f"Base historica para aprendizagem: {total_samples} amostras")
        if total_samples < 30:
            alerts.append("Amostragem historica baixa para aprendizagem estatistica robusta")
            actions.append("Manter thresholds conservadores ate aumentar base historica")
        else:
            actions.append("Aplicar validacao por padrao historico nas operacoes de alto valor")

        currency_mix = learning_snapshot.get("currency_mix")
        if isinstance(currency_mix, dict) and currency_mix:
            top_currency = max(cast(Dict[str, Any], currency_mix).items(), key=lambda item: int(item[1]))[0]
            insights.append(f"Moeda mais recorrente nos pagamentos: {top_currency}")
            actions.append("Reforcar monitoramento de reconciliacao na moeda dominante")

        return AgentMessage(agent=self.name, role=self.role, round=round_number, confidence=0.84, insights=insights, actions=actions, alerts=alerts)