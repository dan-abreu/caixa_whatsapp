from typing import Any, Callable, Dict, Optional


def handle_conversation_intent(
    *,
    db: Any,
    usuario: Dict[str, Any],
    remetente: str,
    mensagem: str,
    intencao: str,
    resposta_sugerida: Optional[str],
    is_help_menu_request: Callable[[str], bool],
    build_whatsapp_checklist_menu: Callable[[], str],
    save_session: Callable[..., None],
    is_greeting: Callable[[str], bool],
) -> Dict[str, Any]:
    nome_usuario = str(usuario.get("nome") or "").strip()
    keep_menu_state = False

    if is_help_menu_request(mensagem):
        resposta = build_whatsapp_checklist_menu()
        save_session(remetente=remetente, db=db, estado="await_menu_option", contexto={"origem": "menu"})
        keep_menu_state = True
    else:
        resposta = resposta_sugerida or (
            "Posso ajudar com operações de ouro, câmbio e consulta de caixa.\n"
            "Digite 'menu' para ver as opções."
        )

    if is_greeting(mensagem) and nome_usuario:
        resposta = f"Olá, {nome_usuario}.\nComo posso ajudar?\nDigite 'menu' para ver as opções."

    response_payload: Dict[str, Any] = {"mensagem": resposta, "dados": {"intencao": intencao}}
    if not keep_menu_state:
        save_session(db=db, remetente=remetente, estado="conversando", contexto={"ultima_mensagem": mensagem, "ultima_intencao": intencao})

    db.insert_log(
        nivel="info",
        remetente=remetente,
        mensagem_recebida=mensagem,
        resposta_enviada=resposta,
        contexto={"intencao": intencao},
    )
    return response_payload


def handle_report_intent(
    *,
    db: Any,
    remetente: str,
    mensagem: str,
    intencao: str,
    extract_caixa_currency: Callable[[str], Optional[str]],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_caixa_detail_response: Callable[[Any, str, str, str, str], Dict[str, Any]],
    clear_session: Callable[[Any, str], None],
    build_caixa_response: Callable[[Any], Dict[str, Any]],
    save_session: Callable[..., None],
) -> Dict[str, Any]:
    requested_currency = extract_caixa_currency(mensagem)
    if requested_currency:
        day = build_day_range(None)
        response_payload = build_caixa_detail_response(
            db,
            requested_currency,
            day["start"],
            day["end"],
            f"Hoje ({day['date']})",
        )
        clear_session(db, remetente)
    else:
        response_payload = build_caixa_response(db, requested_currency=requested_currency)
        save_session(db=db, remetente=remetente, estado="await_caixa_detalhe", contexto={"source": "caixa_summary"})

    db.insert_log(
        nivel="info",
        remetente=remetente,
        mensagem_recebida=mensagem,
        resposta_enviada=response_payload["mensagem"],
        contexto={"intencao": intencao, "date": str(response_payload["dados"].get("date", ""))},
    )
    return response_payload


def handle_register_operation_intent(
    *,
    db: Any,
    ativo: Dict[str, Any],
    ativo_id: int,
    remetente: str,
    mensagem: str,
    provider_message_id: Optional[str],
    quantidade_extraida: Any,
    valor_informado: Optional[float],
    parse_decimal: Callable[[Any, str], Any],
    infer_tipo_operacao: Callable[[str], str],
    money: Callable[[Any], Any],
) -> Dict[str, Any]:
    quantidade = parse_decimal(quantidade_extraida, "quantidade")
    if quantidade <= 0:
        raise ValueError("Quantidade deve ser maior que zero")

    tipo_operacao = infer_tipo_operacao(mensagem)
    contexto: Dict[str, Any] = {
        "ativo_id": ativo_id,
        "nome_ativo": ativo["nome"],
        "quantidade": str(quantidade),
        "tipo_operacao": tipo_operacao,
        "source_message_id": provider_message_id,
    }

    if valor_informado is not None and valor_informado > 0:
        cotacao = parse_decimal(valor_informado, "valor_informado")
        total_usd = money(quantidade * cotacao)
        contexto["cotacao_usd"] = str(cotacao)
        contexto["total_usd"] = str(total_usd)
        db.save_conversation_session(remetente=remetente, estado="await_moeda_simples", contexto=contexto)
        return {"mensagem": "Em qual moeda foi pago?\nUSD / EUR / SRD / BRL", "dados": {"etapa": "await_moeda_simples"}}

    db.save_conversation_session(remetente=remetente, estado="await_preco_simples", contexto=contexto)
    operacao_texto = {"compra": "compra", "venda": "venda", "cambio": "câmbio"}.get(tipo_operacao, "operação")
    return {
        "mensagem": f"Informe o preço por grama em USD ({operacao_texto} de {quantidade}g).",
        "dados": {"etapa": "await_preco_simples"},
    }