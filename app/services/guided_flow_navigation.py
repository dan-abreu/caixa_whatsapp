from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def build_guided_flow_navigation_helpers() -> SimpleNamespace:
    def prompt_for_state(state: str, contexto: Dict[str, Any], build_cambio_prompt: Callable[[str], str]) -> str:
        if state == "await_origem":
            return "Passo 0: local da operação (balcão ou fora)?"
        if state == "await_teor":
            return "Passo 1: qual o teor do ouro em %? Exemplo: 91,6"
        if state == "await_peso":
            return "Passo 2: quantas gramas? Exemplo: 2,5"
        if state == "await_preco_moeda":
            return "Passo 2.5: qual a moeda base da precificação? (USD, EUR, SRD ou BRL)"
        if state == "await_preco_usd":
            return "Passo 3: qual o preço por grama? Exemplo: 115 USD"
        if state == "await_preco_cambio":
            moeda_preco = str(contexto.get("preco_moeda") or "EUR").upper()
            return f"Passo 4: informe o câmbio. Exemplo: {build_cambio_prompt(moeda_preco)}"
        if state == "await_cambio_base_para_total":
            moeda_preco = str(contexto.get("preco_moeda") or "EUR").upper()
            return f"Passo 4.5: para fechar o total em USD, informe o câmbio da moeda-base ({build_cambio_prompt(moeda_preco)})"
        if state == "await_moedas":
            return "Passo 5: em quais moedas foi pago? Use: USD, EUR, SRD, BRL"
        if state == "await_valor_moeda":
            moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
            return f"Passo 6: quanto será pago em {moeda_atual}?"
        if state == "await_cambio_moeda_pre_valor":
            moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
            return f"Passo 6.5: informe o câmbio de {moeda_atual} antes do valor ({build_cambio_prompt(moeda_atual)})"
        if state == "await_cambio_moeda":
            moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
            return f"Passo 7: informe o câmbio ({build_cambio_prompt(moeda_atual)})"
        if state == "await_fechamento_gramas":
            return "Passo 8: quantas gramas foram fechadas? (use quando for venda/câmbio)"
        if state == "await_fechamento_tipo":
            return "Passo 9: fechamento total ou parcial?"
        if state == "await_pessoa":
            return "Passo 10: nome da pessoa?"
        if state == "await_forma_pagamento":
            return "Passo 11: forma de pagamento (dinheiro, transferência, cheque, misto)"
        if state == "await_observacoes":
            return "Passo 12: observações (ou digite 'nenhuma')"
        return "Continue informando os dados solicitados."

    def clear_from_step(contexto: Dict[str, Any], target_state: str) -> Dict[str, Any]:
        cleared = dict(contexto)
        order = [
            "await_teor",
            "await_peso",
            "await_preco_usd",
            "await_preco_cambio",
            "await_cambio_base_para_total",
            "await_moedas",
            "await_valor_moeda",
            "await_cambio_moeda_pre_valor",
            "await_cambio_moeda",
            "await_fechamento_gramas",
            "await_fechamento_tipo",
            "await_pessoa",
            "await_forma_pagamento",
            "await_observacoes",
        ]
        fields_by_step: Dict[str, List[str]] = {
            "await_teor": ["teor"],
            "await_peso": ["peso"],
            "await_preco_usd": ["preco_moeda", "preco_moeda_valor", "total_moeda", "preco_usd", "cambio_preco_moeda", "total_usd"],
            "await_preco_cambio": ["cambio_preco_moeda", "preco_usd", "total_usd"],
            "await_cambio_base_para_total": ["cambio_preco_moeda", "preco_usd", "total_usd"],
            "await_moedas": ["moedas", "moeda_index", "moeda_atual", "pagamentos", "total_pago_usd"],
            "await_valor_moeda": ["pagamentos", "total_pago_usd"],
            "await_cambio_moeda_pre_valor": ["cambio_moeda_atual_pre", "pagamentos", "total_pago_usd"],
            "await_cambio_moeda": ["pagamentos", "total_pago_usd"],
            "await_fechamento_gramas": ["fechamento_gramas", "fechamento_tipo", "pessoa", "forma_pagamento", "observacoes"],
            "await_fechamento_tipo": ["fechamento_tipo", "pessoa", "forma_pagamento", "observacoes"],
            "await_pessoa": ["pessoa", "forma_pagamento", "observacoes"],
            "await_forma_pagamento": ["forma_pagamento", "observacoes"],
            "await_observacoes": ["observacoes"],
        }

        start_clearing = False
        for step in order:
            if step == target_state:
                start_clearing = True
            if start_clearing:
                for field in fields_by_step.get(step, []):
                    cleared.pop(field, None)
        return cleared

    def try_back_command(
        *,
        remetente: str,
        mensagem: str,
        estado: str,
        contexto: Dict[str, Any],
        db: Any,
        normalize_text: Callable[[str], str],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        build_cambio_prompt: Callable[[str], str],
    ) -> Optional[Dict[str, Any]]:
        text = normalize_text(mensagem)
        if not (text.startswith("voltar") or text.startswith("editar") or text.startswith("corrigir")):
            return None

        aliases: Dict[str, str] = {
            "teor": "await_teor",
            "peso": "await_peso",
            "gramas": "await_peso",
            "preco": "await_preco_usd",
            "preco usd": "await_preco_usd",
            "cotacao": "await_preco_usd",
            "cambio preco": "await_preco_cambio",
            "cambio base": "await_cambio_base_para_total",
            "moedas": "await_moedas",
            "moeda": "await_moedas",
            "pagamento": "await_valor_moeda",
            "valor": "await_valor_moeda",
            "cambio": "await_cambio_moeda",
            "cambio moeda": "await_cambio_moeda_pre_valor",
            "fechamento": "await_fechamento_gramas",
            "pessoa": "await_pessoa",
            "nome": "await_pessoa",
            "forma": "await_forma_pagamento",
            "observacoes": "await_observacoes",
            "observacao": "await_observacoes",
        }

        if text in {"voltar", "corrigir", "editar"}:
            tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
            prev_pessoa = "await_moedas" if tipo_operacao == "compra" else "await_fechamento_tipo"
            previous_map: Dict[str, str] = {
                "await_origem": "await_menu_tipo_operacao",
                "await_teor": "await_origem",
                "await_peso": "await_teor",
                "await_preco_moeda": "await_peso",
                "await_preco_usd": "await_peso",
                "await_preco_cambio": "await_preco_usd",
                "await_cambio_base_para_total": "await_moedas",
                "await_moedas": "await_preco_usd",
                "await_valor_moeda": "await_moedas",
                "await_cambio_moeda_pre_valor": "await_moedas",
                "await_cambio_moeda": "await_valor_moeda",
                "await_fechamento_gramas": "await_moedas",
                "await_fechamento_tipo": "await_fechamento_gramas",
                "await_pessoa": prev_pessoa,
                "await_forma_pagamento": "await_pessoa",
                "await_observacoes": "await_forma_pagamento",
                "await_confirmacao": "await_observacoes",
            }
            target_state = previous_map.get(estado)
        else:
            target_state = None
            for key, mapped_state in aliases.items():
                if key in text:
                    target_state = mapped_state
                    break

        if not target_state:
            return {
                "mensagem": "Para corrigir sem cancelar, envie: 'voltar', 'voltar peso', 'voltar preço' ou 'voltar teor'.",
                "dados": {"etapa": estado},
            }

        novo_contexto = clear_from_step(contexto, target_state)
        save_session(db, remetente, target_state, novo_contexto)
        prompt = prompt_for_state(target_state, novo_contexto, build_cambio_prompt)
        return {"mensagem": f"Corrigindo esta etapa.\n{prompt}", "dados": {"etapa": target_state, "acao": "voltar_editar"}}

    return SimpleNamespace(
        prompt_for_state=prompt_for_state,
        clear_from_step=clear_from_step,
        try_back_command=try_back_command,
    )