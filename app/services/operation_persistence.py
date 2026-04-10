from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException


def build_operation_persistence_helpers() -> SimpleNamespace:
    def persist_gold_operation_from_context(
        *,
        db: Any,
        remetente: str,
        contexto: Dict[str, Any],
        post_save_session: bool,
        money: Callable[[Decimal], Decimal],
        risk_diff_limit_usd: Decimal,
        attach_sale_profit_reference: Callable[[Any, Dict[str, Any]], None],
        normalize_gold_type: Callable[[Any], str],
        invalidate_operation_related_view_caches: Callable[[], None],
        should_trigger_multi_agent_review: Callable[[Dict[str, Any], bool], bool],
        run_automatic_multi_agent_review: Callable[..., Dict[str, Any]],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        build_caixa_response: Callable[[Any], Dict[str, Any]],
    ) -> Dict[str, Any]:
        ativo = db.get_ativo_by_nome("Ouro")
        if not ativo:
            ativo = db.get_ativo_by_nome("Ouro 24k")
        if not ativo:
            raise HTTPException(status_code=404, detail="Ativo não encontrado")

        ativo_id = int(ativo["id"])
        peso = Decimal(str(contexto.get("peso")))
        preco = Decimal(str(contexto.get("preco_usd")))
        total = money(peso * preco)
        total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
        diferenca = money(total - total_pago)
        risco_diferenca = abs(diferenca) >= risk_diff_limit_usd
        tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
        if tipo_operacao == "venda":
            attach_sale_profit_reference(db, contexto)

        pagamentos = list(contexto.get("pagamentos", []))
        gold_type = normalize_gold_type(contexto.get("gold_type"))
        quebra_raw = contexto.get("quebra")
        quebra = money(Decimal(str(quebra_raw))) if quebra_raw not in {None, "", "None"} else None
        header_payload: Dict[str, Any] = {
            "cliente_id": contexto.get("cliente_id"),
            "tipo_operacao": tipo_operacao,
            "origem": str(contexto.get("origem", "balcao")),
            "gold_type": gold_type,
            "quebra": str(quebra) if quebra is not None else None,
            "teor": contexto.get("teor"),
            "peso": str(peso),
            "preco_usd": str(money(preco)),
            "total_usd": str(total),
            "total_pago_usd": str(money(total_pago)),
            "diferenca_usd": str(diferenca),
            "fechamento_gramas": contexto.get("fechamento_gramas"),
            "fechamento_tipo": str(contexto.get("fechamento_tipo", "parcial")),
            "pessoa": str(contexto.get("pessoa", "")),
            "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
            "observacoes": contexto.get("observacoes", ""),
            "operador_id": remetente,
            "source_message_id": contexto.get("source_message_id"),
            "contexto": contexto,
            "criado_em": datetime.now(timezone.utc).isoformat(),
        }

        gold_transaction = db.insert_gold_transaction(payload=header_payload, pagamentos=pagamentos)
        transacao = db.insert_transacao(
            tipo_operacao=tipo_operacao,
            ativo_id=ativo_id,
            quantidade=peso,
            cotacao_usada=preco,
            valor_total=total,
            operador_id=remetente,
            source_message_id=contexto.get("source_message_id"),
            status="registrada",
        )
        invalidate_operation_related_view_caches()

        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida="CONFIRMACAO_FLUXO_GUIADO",
            resposta_enviada="Fluxo guiado confirmado",
            contexto=contexto,
        )
        if risco_diferenca:
            db.insert_log(
                nivel="warning",
                remetente=remetente,
                mensagem_recebida="ALERTA_RISCO_DIFERENCA",
                contexto={
                    "intencao": "alerta_risco",
                    "tipo": "diferenca_alta",
                    "limite_usd": str(risk_diff_limit_usd),
                    "diferenca_usd": str(diferenca),
                    "tipo_operacao": contexto.get("tipo_operacao"),
                },
                erro="Diferença de caixa acima do limite",
            )

        review_payload: Optional[Dict[str, Any]] = None
        review_transaction: Dict[str, Any] = {
            "tipo_operacao": tipo_operacao,
            "origem": str(contexto.get("origem", "balcao")),
            "teor": contexto.get("teor"),
            "peso": str(peso),
            "preco_usd": str(money(preco)),
            "total_usd": str(total),
            "total_pago_usd": str(money(total_pago)),
            "diferenca_usd": str(diferenca),
            "fechamento_gramas": contexto.get("fechamento_gramas"),
            "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
            "pagamentos": pagamentos,
            "transacao_id": transacao.get("id"),
        }
        if should_trigger_multi_agent_review(review_transaction, risco_diferenca):
            review_payload = run_automatic_multi_agent_review(
                db,
                objective="avaliacao automatica de operacao enterprise",
                transaction=review_transaction,
                operation_id=gold_transaction.get("id") if isinstance(gold_transaction, dict) else None,
                operation_kind="gold_transaction",
                source_message_id=contexto.get("source_message_id"),
            )

        if post_save_session:
            save_session(db, remetente, "await_caixa_detalhe", {"source": "post_operacao"})

        alerta = "" if not risco_diferenca else " ⚠️ Atenção: verificar diferença."
        gt_id = gold_transaction.get("id") if isinstance(gold_transaction, dict) else None
        tx_id = transacao.get("id")
        if gt_id:
            id_linha = f"ID: GT-{gt_id}\n"
        elif tx_id:
            id_linha = f"ID: T-{tx_id}\n"
        else:
            id_linha = ""

        caixa_resp = build_caixa_response(db)
        caixa_msg = str(caixa_resp.get("mensagem", ""))
        direcao_txt = "Saiu" if tipo_operacao == "compra" else "Entrou"
        direcao_ouro_txt = "Entrou" if tipo_operacao == "compra" else "Saiu"
        mov_linhas: List[str] = [f"- {direcao_ouro_txt} ouro: {peso:,.3f}g"]
        for pagamento in pagamentos:
            moeda_pg = str(pagamento.get("moeda", "USD")).upper()
            valor_moeda_pg = Decimal(str(pagamento.get("valor_moeda", "0")))
            mov_linhas.append(f"- {direcao_txt} {moeda_pg}: {money(valor_moeda_pg)}")
        mov_txt = "\n".join(mov_linhas) if mov_linhas else "- Nenhuma movimentacao registrada"

        response_payload: Dict[str, Any] = {
            "mensagem": (
                f"✅ Operacao registrada com sucesso.\n"
                f"{id_linha}"
                f"Tipo: {tipo_operacao}\n"
                f"Peso: {peso:,.3f}g\n"
                "Movimentacao consolidada dos 5 caixas:\n"
                f"{mov_txt}{alerta}\n"
                "════════════════════════════════\n"
                f"{caixa_msg}"
            ),
            "dados": {
                "intencao": "fluxo_guiado_confirmado",
                "tipo_operacao": contexto.get("tipo_operacao"),
                "peso": str(peso),
                "pagamentos": pagamentos,
                "gold_transaction_id": gt_id,
                "transacao_id": tx_id,
            },
        }
        if review_payload:
            response_payload["dados"]["analise_multiagente"] = review_payload
        return response_payload

    return SimpleNamespace(persist_gold_operation_from_context=persist_gold_operation_from_context)