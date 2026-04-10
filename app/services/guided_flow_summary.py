from types import SimpleNamespace
from typing import Any, Dict, List


def build_guided_flow_summary_helpers() -> SimpleNamespace:
    def format_resumo(contexto: Dict[str, Any]) -> str:
        pagamentos = contexto.get("pagamentos", [])
        linhas_pagamento: List[str] = []
        for pagamento in pagamentos:
            moeda = pagamento.get("moeda", "USD")
            valor = pagamento.get("valor_moeda", "0")
            linhas_pagamento.append(f"- {moeda}: {valor}")

        linhas_pagamento_texto = "\n".join(linhas_pagamento) if linhas_pagamento else "- Sem pagamentos informados"
        tipo_operacao = str(contexto.get("tipo_operacao") or "")
        pessoa_label = "Vendedor" if tipo_operacao == "compra" else "Comprador"
        lucro_real_usd = contexto.get("lucro_real_usd")
        custo_fifo_usd = contexto.get("custo_fifo_usd")
        lucro_ref_usd = contexto.get("lucro_ref_usd")
        preco_compra_ref_usd = contexto.get("preco_compra_ref_usd")
        lucro_linha = ""
        observacoes_idx = "10"

        if tipo_operacao == "venda" and lucro_real_usd is not None:
            lucro_linha = f"10) Lucro real (FIFO): USD {lucro_real_usd} (custo: USD {custo_fifo_usd})\n"
            observacoes_idx = "11"
        elif tipo_operacao == "venda" and lucro_ref_usd is not None:
            lucro_linha = f"10) Lucro ref.: USD {lucro_ref_usd} (custo-base: USD {preco_compra_ref_usd}/g)\n"
            observacoes_idx = "11"

        if tipo_operacao == "compra":
            return (
                "📋 RESUMO FINAL - COMPRA\n"
                f"1) Tipo: {contexto.get('tipo_operacao')}\n"
                f"2) Origem: {contexto.get('origem')}\n"
                f"3) Teor: {contexto.get('teor')}%\n"
                f"4) Peso: {contexto.get('peso')}g\n"
                f"5) Preço base: {contexto.get('preco_moeda')} {contexto.get('preco_moeda')} / g\n"
                f"6) {pessoa_label}: {contexto.get('pessoa')}\n"
                f"7) Forma de pagamento: {contexto.get('forma_pagamento')}\n"
                f"8) Pagamentos por moeda:\n{linhas_pagamento_texto}\n"
                f"9) Observações: {contexto.get('observacoes') or '(nenhuma)'}\n"
                "════════════════════════════════\n"
                "Para confirmar o registro, responda: sim\n"
                "Para cancelar a operacao, responda: nao"
            )

        return (
            "📋 RESUMO FINAL - VENDA\n"
            f"1) Tipo: {contexto.get('tipo_operacao')}\n"
            f"2) Origem: {contexto.get('origem')}\n"
            f"3) Teor: {contexto.get('teor')}%\n"
            f"4) Peso: {contexto.get('peso')}g\n"
            f"5) Fechamento: {contexto.get('fechamento_gramas')}g ({contexto.get('fechamento_tipo')})\n"
            f"6) Preço base: {contexto.get('preco_moeda')} / g\n"
            f"7) {pessoa_label}: {contexto.get('pessoa')}\n"
            f"8) Forma de pagamento: {contexto.get('forma_pagamento')}\n"
            f"9) Pagamentos por moeda:\n{linhas_pagamento_texto}\n"
            f"{lucro_linha}"
            f"{observacoes_idx}) Observações: {contexto.get('observacoes') or '(nenhuma)'}\n"
            "════════════════════════════════\n"
            "Para confirmar o registro, responda: sim\n"
            "Para cancelar a operacao, responda: nao"
        )

    return SimpleNamespace(format_resumo=format_resumo)