from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional, Set


def build_guided_flow_entry_helpers() -> SimpleNamespace:
    def handle_entry_states(
        *,
        estado: str,
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
        text: str,
        guided_flow_states: Set[str],
        clear_session: Callable[[Any, str], None],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        format_resumo: Callable[[Dict[str, Any]], str],
        guided_prompt_for_state: Callable[[str, Dict[str, Any]], str],
        sanitize_nome: Callable[[str], str],
        navigation_hint: Callable[[], str],
    ) -> Optional[Dict[str, Any]]:
        if estado == "await_resume_confirmacao":
            if text in {"continuar", "retomar", "sim", "s"}:
                estado_anterior = str(contexto.get("estado_anterior", ""))
                contexto_anterior = dict(contexto.get("contexto_anterior", {}))
                if not estado_anterior or estado_anterior not in guided_flow_states:
                    clear_session(db, remetente)
                    return {
                        "mensagem": "Sessão anterior expirada. Envie 'compra' ou 'venda' para iniciar novamente.",
                        "dados": {"acao": "sessao_expirada"},
                    }

                save_session(db, remetente, estado_anterior, contexto_anterior)
                if estado_anterior == "await_confirmacao":
                    resumo = format_resumo(contexto_anterior)
                    return {
                        "mensagem": f"Retomando de onde parou.\n{resumo}",
                        "dados": {"etapa": estado_anterior, "acao": "retomar_fluxo"},
                    }

                prompt = guided_prompt_for_state(estado_anterior, contexto_anterior)
                return {
                    "mensagem": f"Retomando de onde parou.\n{prompt}",
                    "dados": {"etapa": estado_anterior, "acao": "retomar_fluxo"},
                }

            if text in {"cancelar", "cancela", "cancel", "nao", "não", "n", "parar", "sair"}:
                clear_session(db, remetente)
                return {
                    "mensagem": "Tudo certo, cancelei por aqui. Quando quiser voltar, me diga compra, venda ou escreva a operacao normalmente.",
                    "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
                }

            return {
                "mensagem": "Quer continuar de onde parou ou prefere cancelar? Pode responder: continuar ou cancelar.",
                "dados": {"etapa": "await_resume_confirmacao"},
            }

        if estado == "await_nome_usuario":
            nome = sanitize_nome(mensagem)
            if len(nome) < 2:
                return {
                    "mensagem": "Nome inválido. Digite um nome com pelo menos 2 letras.",
                    "dados": {"etapa": "await_nome_usuario"},
                }

            db.update_usuario_nome(remetente, nome)
            clear_session(db, remetente)
            return {
                "mensagem": (
                    f"Perfeito, {nome}. Seu cadastro ficou completo.\n"
                    "Se quiser, posso te mostrar as opcoes. Basta enviar: menu."
                ),
                "dados": {"acao": "cadastro_nome", "nome": nome},
            }

        if estado == "await_menu_tipo_operacao":
            tipo_escolhido = {"1": "compra", "2": "venda"}.get(text, text)
            if tipo_escolhido not in {"compra", "venda"}:
                return {
                    "mensagem": (
                        "Nao consegui identificar se voce quer compra ou venda.\n"
                        "Responda com uma destas opcoes:\n"
                        "1) compra\n"
                        "2) venda"
                        f"{navigation_hint()}"
                    ),
                    "dados": {"etapa": "await_menu_tipo_operacao"},
                }

            contexto.update(
                {
                    "tipo_operacao": tipo_escolhido,
                    "pagamentos": [],
                    "moedas": [],
                    "moeda_index": 0,
                    "moeda_atual": None,
                }
            )
            save_session(db, remetente, "await_origem", contexto)
            return {
                "mensagem": (
                    f"Operação: {tipo_escolhido}.\n"
                    "Local da operação:\n"
                    "1) balcão\n"
                    "2) fora"
                    f"{navigation_hint()}"
                ),
                "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
            }

        return None

    return SimpleNamespace(handle_entry_states=handle_entry_states)