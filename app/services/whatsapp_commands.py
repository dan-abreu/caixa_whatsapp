import re
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, cast


def build_whatsapp_command_helpers() -> SimpleNamespace:
    def try_handle_whatsapp_commands(
        *,
        db: Any,
        usuario: Dict[str, Any],
        remetente: str,
        mensagem: str,
        normalize_text: Callable[[str], str],
        build_day_range: Callable[[Optional[str]], Dict[str, str]],
        build_week_range: Callable[[], Dict[str, str]],
        clear_session: Callable[[Any, str], None],
        save_session: Callable[[Any, str, str, Dict[str, Any]], None],
        build_extrato_response: Callable[[Any, str, str, str], Dict[str, Any]],
        parse_operation_reference: Callable[[str], Tuple[str, Optional[int]]],
        normalize_edit_field: Callable[[str], Optional[str]],
        parse_decimal_from_text: Callable[[str, str], Decimal],
        money: Callable[[Decimal], Decimal],
        invalidate_operation_related_view_caches: Callable[[], None],
        supported_currencies: Sequence[str],
    ) -> Optional[Dict[str, Any]]:
        text = mensagem.strip()
        text_norm = normalize_text(text)

        if re.match(r"^extrato\b", text_norm):
            if any(word in text_norm for word in {"hoje", "dia", "agora"}):
                day = build_day_range(None)
                clear_session(db, remetente)
                return build_extrato_response(db, day["start"], day["end"], f"Hoje ({day['date']})")
            if any(word in text_norm for word in {"semana", "week"}):
                week = build_week_range()
                clear_session(db, remetente)
                return build_extrato_response(db, week["start"], week["end"], week["label"])
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

        edit_match = re.match(r"^\s*(editar|edit)\s+(.+?)\s+([\w_çÇãÃâÂáÁéÉíÍóÓúÚ]+)\s+(.+?)\s*$", text, re.IGNORECASE)
        if edit_match:
            op_token = edit_match.group(2)
            field_token = edit_match.group(3)
            value_token = edit_match.group(4)

            op_kind, op_id = parse_operation_reference(op_token)
            if op_id is None:
                return {"mensagem": "ID inválido. Exemplo: editar 123 preco 110", "dados": {"acao": "editar_operacao"}}

            if op_kind == "gold":
                return {
                    "mensagem": "Operações guiadas GT não suportam edição direta. Use cancelar GT-<id> e refaça a operação.",
                    "dados": {"acao": "editar_operacao", "id": op_id, "kind": "gold", "permitido": False},
                }

            transacao_resp = (
                db.client.table("transacoes")
                .select("id,operador_id,quantidade,cotacao_usada,valor_total,moeda_liquidacao,valor_moeda,cambio_para_usd,status")
                .eq("id", op_id)
                .limit(1)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
            if not rows:
                return {"mensagem": f"Operação {op_id} não encontrada.", "dados": {"acao": "editar_operacao"}}

            row = rows[0]
            is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
            if not is_admin and str(row.get("operador_id", "")) != remetente:
                return {
                    "mensagem": "Você não tem permissão para editar esta operação.",
                    "dados": {"acao": "editar_operacao", "permitido": False},
                }

            field = normalize_edit_field(field_token)
            if field is None:
                return {
                    "mensagem": "Campo inválido. Use: preco, quantidade, moeda, valor_moeda ou cambio.",
                    "dados": {"acao": "editar_operacao"},
                }

            update_payload: Dict[str, Any] = {}
            quantidade = Decimal(str(row.get("quantidade", "0")))
            cotacao = Decimal(str(row.get("cotacao_usada", "0")))
            moeda = str(row.get("moeda_liquidacao") or "USD").upper()
            valor_moeda = Decimal(str(row.get("valor_moeda") or row.get("valor_total") or "0"))
            cambio = Decimal(str(row.get("cambio_para_usd") or "1"))

            if field in {"quantidade", "cotacao_usada", "valor_moeda", "cambio_para_usd"}:
                novo = parse_decimal_from_text(value_token, field)
                if field in {"quantidade", "cotacao_usada", "cambio_para_usd"} and novo <= 0:
                    return {"mensagem": f"Valor inválido para {field}.", "dados": {"acao": "editar_operacao"}}
                if field == "valor_moeda" and novo < 0:
                    return {"mensagem": "O valor da moeda não pode ser negativo.", "dados": {"acao": "editar_operacao"}}

                if field == "quantidade":
                    quantidade = novo
                    update_payload["quantidade"] = str(novo)
                elif field == "cotacao_usada":
                    cotacao = novo
                    update_payload["cotacao_usada"] = str(novo)
                elif field == "valor_moeda":
                    valor_moeda = novo
                    update_payload["valor_moeda"] = str(novo)
                elif field == "cambio_para_usd":
                    cambio = novo
                    update_payload["cambio_para_usd"] = str(novo)
            elif field == "moeda_liquidacao":
                nova_moeda = normalize_text(value_token).upper()
                if nova_moeda not in supported_currencies:
                    return {
                        "mensagem": "Moeda inválida. Use: USD, EUR, SRD ou BRL.",
                        "dados": {"acao": "editar_operacao"},
                    }
                moeda = nova_moeda
                update_payload["moeda_liquidacao"] = moeda

            total_usd = money(quantidade * cotacao)
            update_payload["valor_total"] = str(total_usd)

            if moeda == "USD":
                update_payload["moeda_liquidacao"] = "USD"
                update_payload["cambio_para_usd"] = "1"
                update_payload["valor_moeda"] = str(total_usd)
            else:
                if field != "valor_moeda":
                    valor_moeda = money(total_usd * cambio)
                update_payload["valor_moeda"] = str(valor_moeda)
                update_payload["cambio_para_usd"] = str(cambio)

            db.client.table("transacoes").update(update_payload).eq("id", op_id).execute()
            invalidate_operation_related_view_caches()
            return {
                "mensagem": f"✅ Operação {op_id} atualizada com sucesso.",
                "dados": {"acao": "editar_operacao", "id": op_id, "campos": list(update_payload.keys())},
            }

        cancel_match = re.match(r"^\s*(cancelar|cancela|excluir|delete)\s+(.+?)\s*$", text, re.IGNORECASE)
        if cancel_match:
            op_kind, op_id = parse_operation_reference(cancel_match.group(2))
            if op_id is None:
                return {"mensagem": "ID inválido. Exemplo: cancelar 123", "dados": {"acao": "cancelar_operacao"}}

            if op_kind == "gold":
                transacao_resp = db.client.table("gold_transactions").select("*").eq("id", op_id).limit(1).execute()
                rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
                if not rows:
                    return {"mensagem": f"Operação GT-{op_id} não encontrada.", "dados": {"acao": "cancelar_operacao", "kind": "gold"}}

                row = rows[0]
                is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
                if not is_admin and str(row.get("operador_id", "")) != remetente:
                    return {
                        "mensagem": "Você não tem permissão para cancelar esta operação guiada.",
                        "dados": {"acao": "cancelar_operacao", "permitido": False, "kind": "gold"},
                    }

                ok = db.cancel_gold_transaction(op_id, cancelled_by=remetente)
                if not ok:
                    return {"mensagem": "Não consegui cancelar a operação guiada agora.", "dados": {"acao": "cancelar_operacao", "id": op_id, "kind": "gold"}}
                invalidate_operation_related_view_caches()
                return {
                    "mensagem": f"✅ Operação GT-{op_id} cancelada com sucesso.",
                    "dados": {"acao": "cancelar_operacao", "id": op_id, "status": "cancelada", "kind": "gold"},
                }

            transacao_resp = db.client.table("transacoes").select("id,operador_id,status").eq("id", op_id).limit(1).execute()
            rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
            if not rows:
                return {"mensagem": f"Operação {op_id} não encontrada.", "dados": {"acao": "cancelar_operacao"}}

            row = rows[0]
            is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
            if not is_admin and str(row.get("operador_id", "")) != remetente:
                return {
                    "mensagem": "Você não tem permissão para cancelar esta operação.",
                    "dados": {"acao": "cancelar_operacao", "permitido": False},
                }

            db.client.table("transacoes").update({"status": "cancelada"}).eq("id", op_id).execute()
            invalidate_operation_related_view_caches()
            return {
                "mensagem": f"✅ Operação {op_id} cancelada com sucesso.",
                "dados": {"acao": "cancelar_operacao", "id": op_id, "status": "cancelada"},
            }

        return None

    return SimpleNamespace(try_handle_whatsapp_commands=try_handle_whatsapp_commands)