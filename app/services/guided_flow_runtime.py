from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def build_guided_flow_runtime_helpers() -> SimpleNamespace:
    def handle_menu_option(
        *,
        remetente: str,
        mensagem: str,
        db: Any,
        normalize_text: Callable[[str], str],
        build_whatsapp_checklist_menu: Callable[[], str],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        clear_session: Callable[[Any, str], None],
        build_caixa_response: Callable[[Any, Optional[str]], Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        option = normalize_text(mensagem)
        if option not in {"1", "2", "3", "4", "5"}:
            return {
                "mensagem": (
                    "Opção inválida. Escolha um número de 1 a 5.\n\n"
                    f"{build_whatsapp_checklist_menu()}"
                ),
                "dados": {"etapa": "await_menu_option"},
            }

        if option == "1":
            save_session(db, remetente, "await_menu_tipo_operacao", {"source": "menu", "source_message_id": None})
            return {
                "mensagem": "Registrar operação.\nInforme o tipo: compra ou venda.",
                "dados": {"acao": "registrar_operacao"},
            }

        if option == "2":
            response = build_caixa_response(db, None)
            save_session(db, remetente, "await_caixa_detalhe", {"source": "menu_caixa"})
            return response

        if option == "3":
            clear_session(db, remetente)
            save_session(db, remetente, "await_extrato_periodo", {})
            return {
                "mensagem": (
                    "EXTRATO OPERACIONAL - selecione o periodo de consulta:\n"
                    "1) Hoje\n"
                    "2) Esta semana\n"
                    "3) Informar intervalo de datas"
                ),
                "dados": {"etapa": "await_extrato_periodo"},
            }

        if option == "4":
            clear_session(db, remetente)
            return {
                "mensagem": (
                    "Editar operação.\n"
                    "Formato: editar ID campo valor\n\n"
                    "Campos: preço | quantidade | moeda | valor_moeda | câmbio\n"
                    "Exemplos:\n"
                    "- editar 123 preco 110\n"
                    "- editar 123 quantidade 2.5"
                ),
                "dados": {"acao": "editar_operacao"},
            }

        clear_session(db, remetente)
        return {"mensagem": "Cancelar operação.\n", "dados": {"acao": "cancelar_operacao"}}

    def start_guided_flow_if_requested(
        *,
        remetente: str,
        mensagem: str,
        db: Any,
        provider_message_id: Optional[str],
        normalize_text: Callable[[str], str],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        navigation_hint: Callable[[], str],
    ) -> Optional[Dict[str, Any]]:
        text = normalize_text(mensagem)
        if any(token in text for token in {"compra", "comprei", "comprar", "buy", "bought"}):
            tipo = "compra"
        elif any(token in text for token in {"venda", "vendi", "vender", "sell", "sold"}):
            tipo = "venda"
        else:
            return None

        contexto: Dict[str, Any] = {
            "tipo_operacao": tipo,
            "pagamentos": [],
            "moedas": [],
            "moeda_index": 0,
            "moeda_atual": None,
            "source_message_id": provider_message_id,
        }
        save_session(db, remetente, "await_origem", contexto)
        return {
            "mensagem": (
                f"Iniciando registro de {tipo}.\n"
                "Local da operação:\n"
                "1) balcão\n"
                "2) fora"
                f"{navigation_hint()}"
            ),
            "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
        }

    def advance_after_payment_exchange(
        *,
        db: Any,
        remetente: str,
        contexto: Dict[str, Any],
        pagamentos: List[Dict[str, Any]],
        money: Callable[[Decimal], Decimal],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        build_pair_cambio_prompt: Callable[[str, str], str],
        build_cambio_prompt: Callable[[str], str],
    ) -> Dict[str, Any]:
        moedas = list(contexto.get("moedas", []))
        idx = int(contexto.get("moeda_index", 0)) + 1
        total_operacao = Decimal(str(contexto.get("total_usd", "0")))
        total_pago_parcial = sum((Decimal(str(pagamento["valor_usd"])) for pagamento in pagamentos), Decimal("0"))

        if total_operacao <= 0:
            if idx < len(moedas):
                contexto["moeda_index"] = idx
                contexto["moeda_atual"] = moedas[idx]
                proxima_moeda = str(moedas[idx]).upper()
                preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
                if proxima_moeda != preco_moeda:
                    save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
                    cambio_prompt = build_pair_cambio_prompt(preco_moeda, proxima_moeda)
                    return {
                        "mensagem": "Pagamento registrado.\n" f"Câmbio {preco_moeda}/{proxima_moeda}: {cambio_prompt}",
                        "dados": {"etapa": "await_cambio_moeda_pre_valor"},
                    }

                save_session(db, remetente, "await_valor_moeda", contexto)
                return {
                    "mensagem": (
                        "Pagamento registrado.\n"
                        "Ainda falta o câmbio da moeda-base para calcular o total em USD.\n"
                        f"Valor em {moedas[idx]}?"
                    ),
                    "dados": {"etapa": "await_valor_moeda"},
                }

            save_session(db, remetente, "await_cambio_base_para_total", contexto)
            moeda_preco = str(contexto.get("preco_moeda", "EUR")).upper()
            return {
                "mensagem": (
                    "Para fechar o total da operação em USD, informe o câmbio da moeda-base.\n"
                    f"{build_cambio_prompt(moeda_preco)}"
                ),
                "dados": {"etapa": "await_cambio_base_para_total"},
            }

        restante = money(total_operacao - total_pago_parcial)
        if idx < len(moedas):
            contexto["moeda_index"] = idx
            contexto["moeda_atual"] = moedas[idx]
            proxima_moeda = str(moedas[idx]).upper()
            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if proxima_moeda != preco_moeda:
                save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
                cambio_prompt = build_pair_cambio_prompt(preco_moeda, proxima_moeda)
                return {
                    "mensagem": (
                        f"Pago até agora: {money(total_pago_parcial)} USD. Restante: {restante} USD.\n"
                        f"Câmbio {preco_moeda}/{proxima_moeda}: {cambio_prompt}"
                    ),
                    "dados": {"etapa": "await_cambio_moeda_pre_valor"},
                }

            save_session(db, remetente, "await_valor_moeda", contexto)
            return {
                "mensagem": (
                    f"Pago até agora: {money(total_pago_parcial)} USD. Restante: {restante} USD.\n"
                    f"Valor em {moedas[idx]}?"
                ),
                "dados": {"etapa": "await_valor_moeda"},
            }

        total_pago = sum((Decimal(str(pagamento["valor_usd"])) for pagamento in pagamentos), Decimal("0"))
        contexto["total_pago_usd"] = str(money(total_pago))
        tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
        fx_notice = "\nObs: referência em USD estimada (sem câmbio explícito informado)." if contexto.get("fx_auto_assumido") else ""
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        total_moeda = Decimal(str(contexto.get("total_moeda", "0")))
        all_in_preco_moeda = (
            preco_moeda != "USD"
            and total_moeda > 0
            and all(str(pagamento.get("moeda", "")).upper() == preco_moeda for pagamento in pagamentos)
        )
        if all_in_preco_moeda:
            display_pago = sum((Decimal(str(pagamento["valor_moeda"])) for pagamento in pagamentos), Decimal("0"))
            display_diferenca = total_moeda - display_pago
            display_moeda = preco_moeda
        else:
            display_pago = total_pago
            display_diferenca = total_operacao - total_pago
            display_moeda = "USD"

        if tipo_operacao == "compra":
            peso_ctx = Decimal(str(contexto.get("peso", "0")))
            contexto["fechamento_gramas"] = str(money(peso_ctx))
            contexto["fechamento_tipo"] = "total"
            save_session(db, remetente, "await_pessoa", contexto)
            return {
                "mensagem": (
                    f"Total pago: {money(display_pago)} {display_moeda}.\n"
                    f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                    f"Nome do vendedor (de quem você comprou)?{fx_notice}"
                ),
                "dados": {"etapa": "await_pessoa"},
            }

        peso_ctx = Decimal(str(contexto.get("peso", "0")))
        if money(display_diferenca) == Decimal("0.00") and peso_ctx > 0:
            contexto["fechamento_gramas"] = str(money(peso_ctx))
            contexto["fechamento_tipo"] = "total"
            save_session(db, remetente, "await_pessoa", contexto)
            return {
                "mensagem": (
                    f"Total pago: {money(display_pago)} {display_moeda}.\n"
                    f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                    "Venda fechada integralmente.\n"
                    f"Nome do comprador?{fx_notice}"
                ),
                "dados": {"etapa": "await_pessoa"},
            }

        save_session(db, remetente, "await_fechamento_gramas", contexto)
        return {
            "mensagem": (
                f"Total pago: {money(display_pago)} {display_moeda}.\n"
                f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                f"Informe as gramas fechadas.{fx_notice}"
            ),
            "dados": {"etapa": "await_fechamento_gramas"},
        }

    return SimpleNamespace(
        handle_menu_option=handle_menu_option,
        start_guided_flow_if_requested=start_guided_flow_if_requested,
        advance_after_payment_exchange=advance_after_payment_exchange,
    )