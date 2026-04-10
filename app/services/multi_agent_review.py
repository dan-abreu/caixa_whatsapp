from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_multi_agent_review_helpers(
    *,
    multi_agent_auto_enabled: bool,
    risk_diff_limit_usd: Decimal,
    multi_agent_auto_min_usd: Decimal,
    multi_agent_auto_min_weight_grams: Decimal,
    money: Callable[[Any], Any],
    multi_agent_request_cls: Any,
    run_multi_agent_orchestration: Callable[[Any], Any],
    logger: Any,
) -> SimpleNamespace:
    def should_trigger_multi_agent_review(transaction: Dict[str, Any], force: bool = False) -> bool:
        if not multi_agent_auto_enabled:
            return False
        if force:
            return True

        total_usd = Decimal(str(transaction.get("total_usd", transaction.get("valor_total", 0)) or 0))
        total_pago_usd = Decimal(str(transaction.get("total_pago_usd", total_usd) or total_usd))
        peso = Decimal(str(transaction.get("peso", transaction.get("quantidade", 0)) or 0))
        diferenca = abs(money(total_usd - total_pago_usd))
        tipo_operacao = str(transaction.get("tipo_operacao", "")).lower()

        return any(
            [
                diferenca >= risk_diff_limit_usd,
                total_usd >= multi_agent_auto_min_usd,
                peso >= multi_agent_auto_min_weight_grams,
                tipo_operacao in {"venda", "cambio"},
            ]
        )

    def run_automatic_multi_agent_review(
        db: Any,
        *,
        objective: str,
        transaction: Dict[str, Any],
        operation_id: Optional[int],
        operation_kind: str,
        source_message_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        try:
            request = multi_agent_request_cls(
                objective=objective,
                operation=transaction.get("tipo_operacao"),
                operation_id=operation_id,
                operation_kind=operation_kind,
                source_message_id=source_message_id,
                transaction=transaction,
                live_context=db.build_multi_agent_live_context(
                    operation_id=operation_id if operation_kind == "gold_transaction" else None
                ),
                constraints={"trigger": "automatic_review"},
                rounds=2,
            )
            response = run_multi_agent_orchestration(request)
            persisted = db.save_multi_agent_run(
                objective=request.objective,
                operation_id=operation_id,
                operation_kind=operation_kind,
                source_message_id=source_message_id,
                request_payload=request.model_dump(mode="json"),
                response_payload=response.model_dump(mode="json"),
            )
            return {
                "run_id": persisted.get("id") if isinstance(persisted, dict) else None,
                "summary": response.summary,
                "decisions": response.decisions,
                "risks": response.risks,
                "recommendations": response.recommendations,
            }
        except Exception as exc:
            logger.exception("Falha na analise multiagente automatica")
            db.insert_log(
                nivel="warning",
                mensagem_recebida="AUTO_MULTI_AGENT_REVIEW_FAILED",
                contexto={
                    "objective": objective,
                    "operation_id": operation_id,
                    "operation_kind": operation_kind,
                    "transaction": transaction,
                },
                erro=str(exc),
            )
            return None

    return SimpleNamespace(
        should_trigger_multi_agent_review=should_trigger_multi_agent_review,
        run_automatic_multi_agent_review=run_automatic_multi_agent_review,
    )