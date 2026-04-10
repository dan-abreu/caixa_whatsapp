import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_whatsapp_transaction_helpers() -> SimpleNamespace:
    def finish_transacao_simples(
        *,
        db: Any,
        remetente: str,
        mensagem: str,
        contexto: Dict[str, Any],
        money: Callable[[Decimal], Decimal],
        should_trigger_multi_agent_review: Callable[[Dict[str, Any]], bool],
        run_automatic_multi_agent_review: Callable[..., Dict[str, Any]],
        clear_session: Callable[[Any, str], None],
    ) -> Dict[str, Any]:
        ativo_id_ctx = int(contexto["ativo_id"])
        quantidade = Decimal(str(contexto["quantidade"]))
        tipo_operacao = str(contexto["tipo_operacao"])
        nome_ativo = str(contexto.get("nome_ativo", ""))
        nome_ativo_display = "Ouro" if "ouro" in nome_ativo.lower() else nome_ativo
        source_msg_id = contexto.get("source_message_id")
        cotacao = Decimal(str(contexto["cotacao_usd"]))
        total_usd = money(Decimal(str(contexto["total_usd"])))
        moeda = str(contexto.get("moeda_liquidacao", "USD")).upper()
        cambio = Decimal(str(contexto.get("cambio_para_usd", "1.0")))
        valor_moeda = money(total_usd * cambio)

        transacao = db.insert_transacao(
            tipo_operacao=tipo_operacao,
            ativo_id=ativo_id_ctx,
            quantidade=quantidade,
            cotacao_usada=cotacao,
            valor_total=total_usd,
            operador_id=remetente,
            source_message_id=source_msg_id,
            status="registrada",
            moeda_liquidacao=moeda,
            valor_moeda=valor_moeda,
            cambio_para_usd=cambio,
        )

        transacao_id = transacao.get("id")
        tz_offset = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
        data_agora = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
        data_str = data_agora.strftime("%Y%m%d")
        op_id = f"OP-{data_str}-{transacao_id:05d}" if transacao_id else "OP-UNKNOWN"

        review_payload: Optional[Dict[str, Any]] = None
        review_transaction: Dict[str, Any] = {
            "tipo_operacao": tipo_operacao,
            "ativo": nome_ativo_display,
            "quantidade": str(quantidade),
            "peso": str(quantidade),
            "preco_usd": str(money(cotacao)),
            "valor_total": str(total_usd),
            "total_usd": str(total_usd),
            "total_pago_usd": str(total_usd),
            "pagamentos": [
                {
                    "moeda": moeda,
                    "valor_moeda": str(valor_moeda),
                    "cambio_para_usd": str(cambio),
                    "valor_usd": str(total_usd),
                }
            ],
        }
        if should_trigger_multi_agent_review(review_transaction):
            review_payload = run_automatic_multi_agent_review(
                db,
                objective="avaliacao automatica de operacao via webhook",
                transaction=review_transaction,
                operation_id=transacao.get("id"),
                operation_kind="transacao",
                source_message_id=source_msg_id,
            )

        operacao_texto = {
            "compra": "Compra registrada",
            "venda": "Venda registrada",
            "cambio": "Câmbio registrado",
        }.get(tipo_operacao, "Operação registrada")

        clear_session(db, remetente)

        if moeda == "USD":
            moeda_linha = f"${total_usd} USD"
        else:
            moeda_linha = f"{valor_moeda} {moeda} (câmbio: 1 USD = {cambio} {moeda})"

        data_hora = datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "-3")))
        data_fmt = data_hora.strftime("%d/%m/%Y %H:%M:%S")

        response_payload: Dict[str, Any] = {
            "mensagem": (
                f"✅ {operacao_texto}\n"
                f"ID: {op_id}\n"
                f"Data: {data_fmt}\n"
                f"Tipo: {tipo_operacao}\n"
                f"Ativo: {nome_ativo_display}\n"
                f"Quantidade: {quantidade}g\n"
                f"Preço: ${money(cotacao)}/g\n"
                f"Total USD: ${total_usd}\n"
                f"Pagamento: {moeda_linha}\n"
                "Operação registrada com sucesso."
            ),
            "dados": {
                "intencao": "registrar_operacao",
                "tipo_operacao": tipo_operacao,
                "ativo": nome_ativo_display,
                "operacao_id": op_id,
                "quantidade": str(quantidade),
                "cotacao_usada": str(money(cotacao)),
                "valor_total_usd": str(total_usd),
                "moeda_liquidacao": moeda,
                "valor_moeda": str(valor_moeda),
                "cambio_para_usd": str(cambio),
            },
        }
        if review_payload:
            response_payload["dados"]["analise_multiagente"] = review_payload
        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida=mensagem,
            resposta_enviada=response_payload["mensagem"],
            contexto=response_payload["dados"],
        )
        return response_payload

    return SimpleNamespace(finish_transacao_simples=finish_transacao_simples)