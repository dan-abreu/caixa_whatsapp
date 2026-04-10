import logging
from typing import Any, Callable, Dict, Optional, cast

from pydantic import ValidationError


def handle_pre_ai_message(
    *,
    db: Any,
    usuario: Dict[str, Any],
    remetente: str,
    mensagem: str,
    provider_message_id: Optional[str],
    normalize_text: Callable[[str], str],
    get_session: Callable[[Any, str], Optional[Dict[str, Any]]],
    guided_flow_states: set[str],
    should_reset_guided_session_for_message: Callable[[str], bool],
    clear_session: Callable[[Any, str], None],
    is_guided_session_stale: Callable[[Dict[str, Any]], bool],
    guided_session_idle_minutes: Callable[[Dict[str, Any]], Optional[int]],
    guided_session_idle_limit: int,
    save_session: Callable[..., None],
    process_guided_flow: Callable[[str, str, Any, Dict[str, Any]], Dict[str, Any]],
    start_guided_flow_if_requested: Callable[[str, str, Any, Optional[str]], Optional[Dict[str, Any]]],
    is_greeting: Callable[[str], bool],
    needs_name_onboarding: Callable[[Dict[str, Any]], bool],
    try_handle_whatsapp_commands: Callable[[Any, Dict[str, Any], str, str], Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    mensagem_norm = normalize_text(mensagem)
    session = get_session(db, remetente)
    if session:
        estado = str(session.get("estado", ""))
        if estado in guided_flow_states:
            if should_reset_guided_session_for_message(mensagem):
                clear_session(db, remetente)
                session = None
                estado = ""
            elif estado != "await_resume_confirmacao" and is_guided_session_stale(session):
                if mensagem_norm in {"cancelar", "cancela", "cancel", "parar", "sair"}:
                    clear_session(db, remetente)
                    return {
                        "mensagem": "Certo, parei por aqui. Quando quiser retomar, me diga compra, venda ou descreva a operacao do seu jeito.",
                        "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
                    }

                idle_min = guided_session_idle_minutes(session) or guided_session_idle_limit
                save_session(
                    db,
                    remetente,
                    "await_resume_confirmacao",
                    {
                        "estado_anterior": estado,
                        "contexto_anterior": dict(session.get("contexto", {})),
                    },
                )
                return {
                    "mensagem": (
                        f"Ficamos {idle_min} minutos sem conversar. "
                        "Quer continuar de onde parou ou prefere cancelar esse atendimento? "
                        "Pode responder: continuar ou cancelar."
                    ),
                    "dados": {"etapa": "await_resume_confirmacao", "idle_minutos": idle_min},
                }

            if should_reset_guided_session_for_message(mensagem):
                clear_session(db, remetente)
            else:
                return process_guided_flow(remetente, mensagem, db, cast(Dict[str, Any], session))

    session = get_session(db, remetente)
    if session and str(session.get("estado", "")) in guided_flow_states:
        return process_guided_flow(remetente, mensagem, db, session)

    maybe_start = start_guided_flow_if_requested(remetente, mensagem, db, provider_message_id)
    if maybe_start:
        return maybe_start

    if is_greeting(mensagem) and needs_name_onboarding(usuario):
        save_session(db, remetente, "await_nome_usuario", {"source": "onboarding"})
        return {"mensagem": "Olá. Para começar, informe seu nome.", "dados": {"etapa": "await_nome_usuario"}}

    return try_handle_whatsapp_commands(db, usuario, remetente, mensagem)


def resolve_ai_data(
    *,
    db: Any,
    remetente: str,
    mensagem: str,
    extract_message_data: Callable[[str], Dict[str, Any]],
    ai_extracted_data_cls: Any,
    ai_service_error_cls: type[Exception],
    logger: logging.Logger,
) -> Any:
    raw_ai_data: Dict[str, Any] = {}
    try:
        raw_ai_data = extract_message_data(mensagem)
        return ai_extracted_data_cls.model_validate(raw_ai_data)
    except ai_service_error_cls as exc:
        logger.warning("Falha ao extrair dados da IA; aplicando fallback seguro")
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            erro=str(exc),
        )
        return ai_extracted_data_cls(
            intencao="conversar",
            ativo=None,
            quantidade=None,
            valor_informado=None,
            resposta=(
                "Não foi possível interpretar a mensagem. "
                "Tente: 'compra', 'venda', 'caixa', 'extrato' ou 'taxa ouro 70.00'."
            ),
        )
    except ValidationError as exc:
        logger.warning("Payload da IA inválido; aplicando fallback seguro")
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            contexto={"ia_payload": raw_ai_data},
            erro=str(exc),
        )
        return ai_extracted_data_cls(
            intencao="conversar",
            ativo=None,
            quantidade=None,
            valor_informado=None,
            resposta=(
                "Dados insuficientes. "
                "Informe o ativo e a quantidade, por exemplo: 'venda ouro 3g'."
            ),
        )