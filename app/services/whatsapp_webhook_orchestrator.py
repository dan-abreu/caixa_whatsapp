from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException


def build_whatsapp_webhook_orchestrator_helpers() -> SimpleNamespace:
    def processar_webhook(
        *,
        payload: Any,
        db: Any,
        provider_message_id: Optional[str],
        normalize_text: Callable[[str], str],
        get_session: Callable[[Any, str], Optional[Dict[str, Any]]],
        guided_flow_states: Any,
        should_reset_guided_session_for_message: Callable[[str], bool],
        clear_session: Callable[[Any, str], None],
        is_guided_session_stale: Callable[[Dict[str, Any]], bool],
        guided_session_idle_minutes: Callable[[Dict[str, Any]], Optional[int]],
        guided_session_idle_limit: int,
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        process_guided_flow: Callable[[str, str, Any, Dict[str, Any]], Dict[str, Any]],
        start_guided_flow_if_requested: Callable[[Any, str, str], Optional[Dict[str, Any]]],
        is_greeting: Callable[[str], bool],
        needs_name_onboarding: Callable[[Dict[str, Any]], bool],
        try_handle_whatsapp_commands: Callable[[Any, Dict[str, Any], str, str], Optional[Dict[str, Any]]],
        handle_pre_ai_message: Callable[..., Optional[Dict[str, Any]]],
        resolve_ai_data: Callable[..., Any],
        extract_message_data: Callable[..., Any],
        ai_extracted_data_cls: Any,
        ai_service_error_cls: Any,
        logger: Any,
        handle_conversation_intent: Callable[..., Dict[str, Any]],
        is_help_menu_request: Callable[[str], bool],
        build_whatsapp_checklist_menu: Callable[[], str],
        handle_report_intent: Callable[..., Dict[str, Any]],
        extract_caixa_currency: Callable[[str], Optional[str]],
        build_day_range: Callable[[Optional[str]], Dict[str, str]],
        build_caixa_detail_response: Callable[..., Dict[str, Any]],
        build_caixa_response: Callable[..., Dict[str, Any]],
        normalize_ativo_nome: Callable[[str], str],
        handle_register_operation_intent: Callable[..., Dict[str, Any]],
        parse_decimal: Callable[[Any, str], Any],
        infer_tipo_operacao: Callable[[str], str],
        money: Callable[[Any], Any],
    ) -> Dict[str, Any]:
        remetente = payload.remetente.strip()
        mensagem = payload.mensagem.strip()
        usuario = db.get_usuario_by_telefone(remetente)

        if not usuario:
            db.insert_log(
                nivel="warning",
                remetente=remetente,
                mensagem_recebida=mensagem,
                erro="Remetente não autorizado",
            )
            raise HTTPException(status_code=403, detail="Remetente não autorizado.")

        pre_ai_response = handle_pre_ai_message(
            db=db,
            usuario=usuario,
            remetente=remetente,
            mensagem=mensagem,
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
        )
        if pre_ai_response is not None:
            return pre_ai_response

        ai_data = resolve_ai_data(
            db=db,
            remetente=remetente,
            mensagem=mensagem,
            extract_message_data=extract_message_data,
            ai_extracted_data_cls=ai_extracted_data_cls,
            ai_service_error_cls=ai_service_error_cls,
            logger=logger,
        )

        intencao = ai_data.intencao
        ativo_extraido = ai_data.ativo

        if intencao == "conversar":
            return handle_conversation_intent(
                db=db,
                usuario=usuario,
                remetente=remetente,
                mensagem=mensagem,
                intencao=intencao,
                resposta_sugerida=ai_data.resposta,
                is_help_menu_request=is_help_menu_request,
                build_whatsapp_checklist_menu=build_whatsapp_checklist_menu,
                save_session=save_session,
                is_greeting=is_greeting,
            )

        if intencao == "consultar_relatorio":
            return handle_report_intent(
                db=db,
                remetente=remetente,
                mensagem=mensagem,
                intencao=intencao,
                extract_caixa_currency=extract_caixa_currency,
                build_day_range=build_day_range,
                build_caixa_detail_response=build_caixa_detail_response,
                clear_session=clear_session,
                build_caixa_response=build_caixa_response,
                save_session=save_session,
            )

        nome_ativo = normalize_ativo_nome(ativo_extraido or "")
        ativo = db.get_ativo_by_nome(nome_ativo)
        if not ativo:
            raise HTTPException(status_code=404, detail="Ativo não encontrado")

        ativo_id = int(ativo["id"])
        if intencao == "registrar_operacao":
            try:
                return handle_register_operation_intent(
                    db=db,
                    ativo=ativo,
                    ativo_id=ativo_id,
                    remetente=remetente,
                    mensagem=mensagem,
                    provider_message_id=provider_message_id,
                    quantidade_extraida=ai_data.quantidade,
                    valor_informado=ai_data.valor_informado,
                    parse_decimal=parse_decimal,
                    infer_tipo_operacao=infer_tipo_operacao,
                    money=money,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        raise HTTPException(status_code=400, detail=f"Intenção não suportada: {intencao}")

    return SimpleNamespace(processar_webhook=processar_webhook)