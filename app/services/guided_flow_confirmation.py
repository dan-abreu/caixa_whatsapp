from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def build_guided_flow_confirmation_helpers() -> SimpleNamespace:
    def handle_confirmation_states(
        *,
        estado: str,
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
        money: Callable[[Decimal], Decimal],
        parse_decimal_from_text: Callable[[str, str], Decimal],
        parse_fechamento_tipo_choice: Callable[[str], Optional[str]],
        navigation_hint: Callable[[], str],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        parse_forma_pagamento_choice: Callable[[str], Optional[str]],
        normalize_text: Callable[[str], str],
        attach_sale_profit_reference: Callable[[Any, Dict[str, Any]], None],
        format_resumo: Callable[[Dict[str, Any]], str],
        extract_confirmacao: Callable[[str], Optional[bool]],
        clear_session: Callable[[Any, str], None],
        project_caixa_balances: Callable[[Dict[str, Any], str, Decimal, List[Dict[str, Any]]], Dict[str, Decimal]],
        find_negative_caixa_balances: Callable[[Dict[str, Decimal]], Dict[str, Decimal]],
        format_negative_caixa_lines: Callable[[Dict[str, Decimal]], List[str]],
        persist_gold_operation_from_context: Callable[[Any, str, Dict[str, Any], bool], Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if estado == "await_fechamento_gramas":
            fechamento = parse_decimal_from_text(mensagem, "fechamento_gramas")
            if fechamento < 0:
                return {
                    "mensagem": "Fechamento em gramas não pode ser negativo.",
                    "dados": {"etapa": estado},
                }
            contexto["fechamento_gramas"] = str(money(fechamento))
            save_session(db, remetente, "await_fechamento_tipo", contexto)
            return {
                "mensagem": "Fechamento total ou parcial?",
                "dados": {"etapa": "await_fechamento_tipo"},
            }

        if estado == "await_fechamento_tipo":
            fechamento_tipo = parse_fechamento_tipo_choice(mensagem)
            if fechamento_tipo is None:
                return {
                    "mensagem": (
                        "Escolha o tipo de fechamento:\n"
                        "1) total\n"
                        "2) parcial"
                        f"{navigation_hint()}"
                    ),
                    "dados": {"etapa": estado},
                }
            contexto["fechamento_tipo"] = fechamento_tipo
            save_session(db, remetente, "await_pessoa", contexto)
            tipo_op = str(contexto.get("tipo_operacao", "compra"))
            pergunta_pessoa = "Nome do vendedor (de quem você comprou)?" if tipo_op == "compra" else "Nome do comprador?"
            return {"mensagem": pergunta_pessoa, "dados": {"etapa": "await_pessoa"}}

        if estado == "await_pessoa":
            if len(mensagem.strip()) < 2:
                return {"mensagem": "Informe um nome válido.", "dados": {"etapa": estado}}
            contexto["pessoa"] = mensagem.strip()
            save_session(db, remetente, "await_forma_pagamento", contexto)
            return {
                "mensagem": (
                    "Como foi o pagamento?\n"
                    "1) dinheiro\n"
                    "2) transferência\n"
                    "3) cheque\n"
                    "4) misto"
                    f"{navigation_hint()}"
                ),
                "dados": {"etapa": "await_forma_pagamento"},
            }

        if estado == "await_forma_pagamento":
            forma = parse_forma_pagamento_choice(mensagem)
            if forma is None:
                return {
                    "mensagem": (
                        "Forma inválida. Escolha uma opção:\n"
                        "1) dinheiro\n"
                        "2) transferência\n"
                        "3) cheque\n"
                        "4) misto"
                        f"{navigation_hint()}"
                    ),
                    "dados": {"etapa": estado},
                }
            contexto["forma_pagamento"] = forma
            pagamentos = list(contexto.get("pagamentos", []))
            for pagamento in pagamentos:
                pagamento["forma_pagamento"] = forma
            contexto["pagamentos"] = pagamentos
            save_session(db, remetente, "await_observacoes", contexto)
            return {
                "mensagem": "Quer adicionar observações? (ou digite 'nenhuma')",
                "dados": {"etapa": "await_observacoes"},
            }

        if estado == "await_observacoes":
            contexto["observacoes"] = "" if normalize_text(mensagem) in {"nenhuma", "nao", "não"} else mensagem.strip()
            attach_sale_profit_reference(db, contexto)
            resumo = format_resumo(contexto)
            save_session(db, remetente, "await_confirmacao", contexto)
            return {"mensagem": resumo, "dados": {"etapa": "await_confirmacao", "preview": contexto}}

        if estado == "await_confirmacao":
            text_confirm = normalize_text(mensagem)
            confirm: Optional[bool]
            if contexto.get("risk_override_pending") and text_confirm in {"autorizar risco", "autorizar", "override"}:
                contexto["risk_override_approved"] = True
                contexto.pop("risk_override_pending", None)
                save_session(db, remetente, "await_confirmacao", contexto)
                confirm = True
            else:
                confirm = extract_confirmacao(mensagem)

            if confirm is None:
                if contexto.get("risk_override_pending"):
                    return {
                        "mensagem": "Responda: autorizar risco, não ou voltar.",
                        "dados": {"etapa": estado, "risk_override_pending": True},
                    }
                return {"mensagem": "Digite apenas: sim ou não.", "dados": {"etapa": estado}}

            if not confirm:
                clear_session(db, remetente)
                return {"mensagem": "Operação cancelada com sucesso.", "dados": {"intencao": "fluxo_guiado_cancelado"}}

            peso = Decimal(str(contexto.get("peso")))
            preco = Decimal(str(contexto.get("preco_usd")))
            total = money(peso * preco)
            tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
            if tipo_operacao == "venda":
                attach_sale_profit_reference(db, contexto)

            pagamentos = list(contexto.get("pagamentos", []))
            projected = project_caixa_balances(db.get_saldo_caixa(), tipo_operacao, peso, pagamentos)
            negative_balances = find_negative_caixa_balances(projected)
            fifo_shortfall = Decimal(str(contexto.get("fifo_shortfall_grams", "0")))
            risk_lines: List[str] = []
            if negative_balances:
                risk_lines.append("Saldos projetados negativos:")
                risk_lines.extend(format_negative_caixa_lines(negative_balances))
            if fifo_shortfall > 0:
                risk_lines.append(f"- Estoque FIFO insuficiente: faltam {fifo_shortfall} g")

            if risk_lines and not contexto.get("risk_override_approved"):
                usuario_confirm = db.get_usuario_by_telefone(remetente) or {}
                is_admin_confirm = str(usuario_confirm.get("tipo_usuario", "")).lower() == "admin"
                contexto["risk_override_pending"] = True
                save_session(db, remetente, "await_confirmacao", contexto)
                if is_admin_confirm:
                    return {
                        "mensagem": "⛔ Bloqueio de risco.\n" + "\n".join(risk_lines) + "\nResponda: autorizar risco, não ou voltar.",
                        "dados": {"etapa": estado, "risk_override_pending": True, "risk_blocked": True},
                    }
                return {
                    "mensagem": "⛔ Bloqueio de risco.\n" + "\n".join(risk_lines) + "\nSomente admin pode autorizar override. Use voltar ou cancelar.",
                    "dados": {"etapa": estado, "risk_blocked": True},
                }

            _ = total
            return persist_gold_operation_from_context(db, remetente, contexto, True)

        return None

    return SimpleNamespace(handle_confirmation_states=handle_confirmation_states)