from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_operation_runtime_helpers(
    *,
    whatsapp_caixa_detail_helpers: Any,
    operation_persistence_helpers: Any,
    format_caixa_movement: Callable[[str, Any], str],
    money: Callable[[Any], Any],
    risk_diff_limit_usd: Any,
    attach_sale_profit_reference: Callable[..., Any],
    normalize_gold_type: Callable[[str], str],
    invalidate_operation_related_view_caches: Callable[[], None],
    should_trigger_multi_agent_review: Callable[[Dict[str, Any], bool], bool],
    run_automatic_multi_agent_review: Callable[..., Optional[Dict[str, Any]]],
    save_session: Callable[[Any, str, str, Dict[str, Any]], None],
    build_caixa_response: Callable[..., Dict[str, Any]],
) -> SimpleNamespace:
    def build_caixa_detail_response(
        db: Any,
        currency: str,
        start_iso: str,
        end_iso: str,
        label_periodo: str,
    ) -> Dict[str, Any]:
        return whatsapp_caixa_detail_helpers.build_caixa_detail_response(
            db,
            currency,
            start_iso,
            end_iso,
            label_periodo,
            format_caixa_movement=format_caixa_movement,
            money=money,
        )

    def persist_gold_operation_from_context(
        db: Any,
        remetente: str,
        contexto: Dict[str, Any],
        post_save_session: bool = True,
    ) -> Dict[str, Any]:
        return operation_persistence_helpers.persist_gold_operation_from_context(
            db=db,
            remetente=remetente,
            contexto=contexto,
            post_save_session=post_save_session,
            money=money,
            risk_diff_limit_usd=risk_diff_limit_usd,
            attach_sale_profit_reference=attach_sale_profit_reference,
            normalize_gold_type=normalize_gold_type,
            invalidate_operation_related_view_caches=invalidate_operation_related_view_caches,
            should_trigger_multi_agent_review=should_trigger_multi_agent_review,
            run_automatic_multi_agent_review=run_automatic_multi_agent_review,
            save_session=save_session,
            build_caixa_response=build_caixa_response,
        )

    return SimpleNamespace(
        build_caixa_detail_response=build_caixa_detail_response,
        persist_gold_operation_from_context=persist_gold_operation_from_context,
    )
