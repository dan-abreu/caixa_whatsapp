from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set


def build_guided_flow_orchestrator_helpers(
    *,
    guided_flow_states: Set[str],
    normalize_text: Callable[[str], str],
    clear_session: Callable[[Any, str], None],
    handle_menu_option: Callable[[str, str, Any], Optional[Dict[str, Any]]],
    guided_try_back_command: Callable[[str, str, str, Dict[str, Any], Any], Optional[Dict[str, Any]]],
    guided_flow_entry_helpers: Any,
    guided_flow_setup_helpers: Any,
    guided_flow_payment_helpers: Any,
    guided_flow_confirmation_helpers: Any,
    guided_flow_tail_helpers: Any,
    save_session: Callable[[Any, str, str, Dict[str, Any]], None],
    format_resumo: Callable[[Dict[str, Any]], str],
    guided_prompt_for_state: Callable[[str, Dict[str, Any]], str],
    sanitize_nome: Callable[[str], str],
    navigation_hint: Callable[[], str],
    money: Callable[[Any], Any],
    parse_decimal_from_text: Callable[[str, str], Any],
    parse_origem_choice: Callable[[str], Optional[str]],
    parse_single_currency_choice: Callable[[str], Optional[str]],
    extract_moedas: Callable[[str], List[str]],
    normalize_cambio_para_usd: Callable[[str, Any], Any],
    build_pair_cambio_prompt: Callable[[str, str], str],
    supported_currencies: List[str],
    try_set_total_usd_from_base_rate: Callable[[Dict[str, Any], Any], bool],
    pair_rate_to_payment_per_usd: Callable[..., Any],
    moeda_strength: Any,
    fx_rate: Callable[[Any], Any],
    advance_after_payment_exchange: Callable[[Any, str, Dict[str, Any], List[Dict[str, Any]]], Dict[str, Any]],
    build_cambio_prompt: Callable[[str], str],
    parse_fechamento_tipo_choice: Callable[[str], Optional[str]],
    parse_forma_pagamento_choice: Callable[[str], Optional[str]],
    attach_sale_profit_reference: Callable[[Any, Dict[str, Any]], None],
    extract_confirmacao: Callable[[str], Optional[bool]],
    project_caixa_balances: Callable[[Dict[str, Any], str, Any, List[Dict[str, Any]]], Dict[str, Any]],
    find_negative_caixa_balances: Callable[[Dict[str, Any]], List[Any]],
    format_negative_caixa_lines: Callable[[List[Any]], List[str]],
    persist_gold_operation_from_context: Callable[[Any, str, Dict[str, Any]], Dict[str, Any]],
    extract_caixa_currency: Callable[[str], Optional[str]],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_week_range: Callable[[], Dict[str, str]],
    build_caixa_detail_response: Callable[..., Dict[str, Any]],
    build_extrato_response: Callable[..., Dict[str, Any]],
    parse_date_user_input: Callable[[str], Optional[str]],
    finish_transacao_simples: Callable[[Any, str, str, Dict[str, Any]], Dict[str, Any]],
) -> SimpleNamespace:
    def process_guided_flow(remetente: str, mensagem: str, db: Any, session: Dict[str, Any]) -> Dict[str, Any]:
        estado = str(session.get("estado", ""))
        contexto = dict(session.get("contexto", {}))
        text = normalize_text(mensagem)

        cancelable_states = guided_flow_states - {"await_menu_option", "await_menu_tipo_operacao", "await_nome_usuario"}
        if estado in cancelable_states and text in {"cancelar", "cancela", "cancel", "parar", "sair"}:
            clear_session(db, remetente)
            return {
                "mensagem": "Certo, parei por aqui. Quando quiser retomar, me diga compra, venda ou descreva a operacao do seu jeito.",
                "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
            }

        if estado == "await_menu_option":
            menu_result = handle_menu_option(remetente, mensagem, db)
            if menu_result is not None:
                return menu_result

        back_result = guided_try_back_command(remetente, mensagem, estado, contexto, db)
        if back_result is not None and estado in guided_flow_states:
            return back_result

        responses = [
            guided_flow_entry_helpers.handle_entry_states(
                estado=estado,
                db=db,
                remetente=remetente,
                mensagem=mensagem,
                contexto=contexto,
                text=text,
                guided_flow_states=guided_flow_states,
                clear_session=clear_session,
                save_session=save_session,
                format_resumo=format_resumo,
                guided_prompt_for_state=guided_prompt_for_state,
                sanitize_nome=sanitize_nome,
                navigation_hint=navigation_hint,
            ),
            guided_flow_setup_helpers.handle_setup_states(
                estado=estado,
                db=db,
                remetente=remetente,
                mensagem=mensagem,
                contexto=contexto,
                money=money,
                parse_decimal_from_text=parse_decimal_from_text,
                parse_origem_choice=parse_origem_choice,
                parse_single_currency_choice=parse_single_currency_choice,
                extract_moedas=extract_moedas,
                save_session=save_session,
                navigation_hint=navigation_hint,
                normalize_cambio_para_usd=normalize_cambio_para_usd,
                build_pair_cambio_prompt=build_pair_cambio_prompt,
                supported_currencies=supported_currencies,
            ),
            guided_flow_payment_helpers.handle_payment_states(
                estado=estado,
                db=db,
                remetente=remetente,
                mensagem=mensagem,
                contexto=contexto,
                money=money,
                parse_decimal_from_text=parse_decimal_from_text,
                save_session=save_session,
                clear_session=clear_session,
                normalize_cambio_para_usd=normalize_cambio_para_usd,
                try_set_total_usd_from_base_rate=try_set_total_usd_from_base_rate,
                pair_rate_to_payment_per_usd=pair_rate_to_payment_per_usd,
                moeda_strength=moeda_strength,
                fx_rate=fx_rate,
                advance_after_payment_exchange=advance_after_payment_exchange,
                build_cambio_prompt=build_cambio_prompt,
            ),
            guided_flow_confirmation_helpers.handle_confirmation_states(
                estado=estado,
                db=db,
                remetente=remetente,
                mensagem=mensagem,
                contexto=contexto,
                money=money,
                parse_decimal_from_text=parse_decimal_from_text,
                parse_fechamento_tipo_choice=parse_fechamento_tipo_choice,
                navigation_hint=navigation_hint,
                save_session=save_session,
                parse_forma_pagamento_choice=parse_forma_pagamento_choice,
                normalize_text=normalize_text,
                attach_sale_profit_reference=attach_sale_profit_reference,
                format_resumo=format_resumo,
                extract_confirmacao=extract_confirmacao,
                clear_session=clear_session,
                project_caixa_balances=project_caixa_balances,
                find_negative_caixa_balances=find_negative_caixa_balances,
                format_negative_caixa_lines=format_negative_caixa_lines,
                persist_gold_operation_from_context=persist_gold_operation_from_context,
            ),
            guided_flow_tail_helpers.handle_tail_states(
                estado=estado,
                db=db,
                remetente=remetente,
                mensagem=mensagem,
                contexto=contexto,
                parse_decimal_from_text=parse_decimal_from_text,
                parse_single_currency_choice=parse_single_currency_choice,
                money=money,
                save_session=save_session,
                clear_session=clear_session,
                extract_caixa_currency=extract_caixa_currency,
                build_day_range=build_day_range,
                build_week_range=build_week_range,
                build_caixa_detail_response=build_caixa_detail_response,
                build_extrato_response=build_extrato_response,
                parse_date_user_input=parse_date_user_input,
                finish_transacao_simples=finish_transacao_simples,
                normalize_text=normalize_text,
                navigation_hint=navigation_hint,
            ),
        ]
        for response in responses:
            if response is not None:
                return response

        return {"mensagem": "Não foi possível continuar o fluxo. Inicie novamente: compra ou venda.", "dados": {"etapa": "reiniciar"}}

    return SimpleNamespace(process_guided_flow=process_guided_flow)
