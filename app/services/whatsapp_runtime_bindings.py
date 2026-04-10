from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def build_whatsapp_runtime_binding_helpers(
    *,
    whatsapp_command_helpers: Any,
    whatsapp_report_helpers: Any,
    guided_flow_runtime_helpers: Any,
    whatsapp_transaction_helpers: Any,
    whatsapp_webhook_orchestrator_helpers: Any,
    normalize_text: Callable[[str], str],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_week_range: Callable[[], Dict[str, str]],
    clear_session: Callable[[Any, str], None],
    save_session: Callable[[Any, str, str, Dict[str, Any]], None],
    parse_operation_reference: Callable[[str], Any],
    normalize_edit_field: Callable[[str], Optional[str]],
    parse_decimal_from_text: Callable[[str, str], Any],
    money: Callable[[Any], Any],
    invalidate_operation_related_view_caches: Callable[[], None],
    supported_currencies: List[str],
    build_gold_caixa_metrics_from_pending_grams: Callable[..., Dict[str, Any]],
    build_whatsapp_checklist_menu: Callable[[], str],
    navigation_hint: Callable[[], str],
    build_pair_cambio_prompt: Callable[[str, str], str],
    build_cambio_prompt: Callable[[str], str],
    should_trigger_multi_agent_review: Callable[[Dict[str, Any], bool], bool],
    run_automatic_multi_agent_review: Callable[..., Optional[Dict[str, Any]]],
    guided_flow_states: Any,
    should_reset_guided_session_for_message: Callable[[str, str], bool],
    is_guided_session_stale: Callable[[Dict[str, Any]], bool],
    guided_session_idle_minutes: Callable[[Dict[str, Any]], int],
    guided_session_idle_limit: int,
    process_guided_flow: Callable[[str, str, Any, Dict[str, Any]], Dict[str, Any]],
    is_greeting: Callable[[str], bool],
    needs_name_onboarding: Callable[[Dict[str, Any]], bool],
    handle_pre_ai_message: Callable[..., Optional[Dict[str, Any]]],
    resolve_ai_data: Callable[..., Any],
    extract_message_data: Callable[..., Any],
    ai_extracted_data_cls: Any,
    ai_service_error_cls: Any,
    logger: Any,
    handle_conversation_intent: Callable[..., Dict[str, Any]],
    is_help_menu_request: Callable[[str], bool],
    handle_report_intent: Callable[..., Dict[str, Any]],
    extract_caixa_currency: Callable[[str], Optional[str]],
    build_caixa_detail_response: Callable[..., Dict[str, Any]],
    normalize_ativo_nome: Callable[[str], str],
    handle_register_operation_intent: Callable[..., Dict[str, Any]],
    parse_decimal: Callable[[Any, str], Any],
    infer_tipo_operacao: Callable[[str], str],
    get_session: Callable[[Any, str], Optional[Dict[str, Any]]],
) -> SimpleNamespace:
    def try_handle_whatsapp_commands(
        db: Any,
        usuario: Dict[str, Any],
        remetente: str,
        mensagem: str,
    ) -> Optional[Dict[str, Any]]:
        return whatsapp_command_helpers.try_handle_whatsapp_commands(
            db=db,
            usuario=usuario,
            remetente=remetente,
            mensagem=mensagem,
            normalize_text=normalize_text,
            build_day_range=build_day_range,
            build_week_range=build_week_range,
            clear_session=clear_session,
            save_session=save_session,
            build_extrato_response=build_extrato_response,
            parse_operation_reference=parse_operation_reference,
            normalize_edit_field=normalize_edit_field,
            parse_decimal_from_text=parse_decimal_from_text,
            money=money,
            invalidate_operation_related_view_caches=invalidate_operation_related_view_caches,
            supported_currencies=supported_currencies,
        )

    def build_caixa_response(db: Any, requested_currency: Optional[str] = None) -> Dict[str, Any]:
        return whatsapp_report_helpers.build_caixa_response(
            db,
            requested_currency,
            build_day_range=build_day_range,
            build_gold_caixa_metrics_from_pending_grams=build_gold_caixa_metrics_from_pending_grams,
        )

    def build_extrato_response(
        db: Any,
        start_iso: str,
        end_iso: str,
        label_periodo: str,
        transactions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return whatsapp_report_helpers.build_extrato_response(db, start_iso, end_iso, label_periodo, transactions)

    def handle_menu_option(remetente: str, mensagem: str, db: Any) -> Optional[Dict[str, Any]]:
        return guided_flow_runtime_helpers.handle_menu_option(
            remetente=remetente,
            mensagem=mensagem,
            db=db,
            normalize_text=normalize_text,
            build_whatsapp_checklist_menu=build_whatsapp_checklist_menu,
            save_session=save_session,
            clear_session=clear_session,
            build_caixa_response=build_caixa_response,
        )

    def start_guided_flow_if_requested(
        remetente: str,
        mensagem: str,
        db: Any,
        provider_message_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        return guided_flow_runtime_helpers.start_guided_flow_if_requested(
            remetente=remetente,
            mensagem=mensagem,
            db=db,
            provider_message_id=provider_message_id,
            normalize_text=normalize_text,
            save_session=save_session,
            navigation_hint=navigation_hint,
        )

    def advance_after_payment_exchange(
        db: Any,
        remetente: str,
        contexto: Dict[str, Any],
        pagamentos: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return guided_flow_runtime_helpers.advance_after_payment_exchange(
            db=db,
            remetente=remetente,
            contexto=contexto,
            pagamentos=pagamentos,
            money=money,
            save_session=save_session,
            build_pair_cambio_prompt=build_pair_cambio_prompt,
            build_cambio_prompt=build_cambio_prompt,
        )

    def finish_transacao_simples(
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
    ) -> Dict[str, Any]:
        return whatsapp_transaction_helpers.finish_transacao_simples(
            db=db,
            remetente=remetente,
            mensagem=mensagem,
            contexto=contexto,
            money=money,
            should_trigger_multi_agent_review=should_trigger_multi_agent_review,
            run_automatic_multi_agent_review=run_automatic_multi_agent_review,
            clear_session=clear_session,
        )

    def processar_webhook(
        payload: Any,
        db: Any,
        provider_message_id: Optional[str],
    ) -> Dict[str, Any]:
        return whatsapp_webhook_orchestrator_helpers.processar_webhook(
            payload=payload,
            db=db,
            provider_message_id=provider_message_id,
            normalize_text=normalize_text,
            get_session=get_session,
            guided_flow_states=guided_flow_states,
            should_reset_guided_session_for_message=should_reset_guided_session_for_message,
            clear_session=clear_session,
            is_guided_session_stale=is_guided_session_stale,
            guided_session_idle_minutes=guided_session_idle_minutes,
            guided_session_idle_limit=guided_session_idle_limit,
            save_session=save_session,
            process_guided_flow=process_guided_flow,
            start_guided_flow_if_requested=start_guided_flow_if_requested,
            is_greeting=is_greeting,
            needs_name_onboarding=needs_name_onboarding,
            try_handle_whatsapp_commands=try_handle_whatsapp_commands,
            handle_pre_ai_message=handle_pre_ai_message,
            resolve_ai_data=resolve_ai_data,
            extract_message_data=extract_message_data,
            ai_extracted_data_cls=ai_extracted_data_cls,
            ai_service_error_cls=ai_service_error_cls,
            logger=logger,
            handle_conversation_intent=handle_conversation_intent,
            is_help_menu_request=is_help_menu_request,
            build_whatsapp_checklist_menu=build_whatsapp_checklist_menu,
            handle_report_intent=handle_report_intent,
            extract_caixa_currency=extract_caixa_currency,
            build_day_range=build_day_range,
            build_caixa_detail_response=build_caixa_detail_response,
            build_caixa_response=build_caixa_response,
            normalize_ativo_nome=normalize_ativo_nome,
            handle_register_operation_intent=handle_register_operation_intent,
            parse_decimal=parse_decimal,
            infer_tipo_operacao=infer_tipo_operacao,
            money=money,
        )

    return SimpleNamespace(
        try_handle_whatsapp_commands=try_handle_whatsapp_commands,
        build_caixa_response=build_caixa_response,
        build_extrato_response=build_extrato_response,
        handle_menu_option=handle_menu_option,
        start_guided_flow_if_requested=start_guided_flow_if_requested,
        advance_after_payment_exchange=advance_after_payment_exchange,
        finish_transacao_simples=finish_transacao_simples,
        processar_webhook=processar_webhook,
    )
