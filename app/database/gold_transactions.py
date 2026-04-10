from .common import Any, Decimal, Dict, List, Optional, _safe_decimal, cast, datetime, logger, timezone


def _safe_int(value: Any, default: int = 0, *, context: str = "valor") -> int:
    try:
        return int(str(default if value is None else value))
    except (TypeError, ValueError) as exc:
        logger.warning("Falha ao converter inteiro em %s: %s", context, exc)
        return default


class GoldTransactionsMixin:
    def insert_gold_transaction(self, payload: Dict[str, Any], pagamentos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        try:
            payload = dict(payload)
            payload.setdefault("status", "registrada")
            try:
                header_response = self.client.table("gold_transactions").insert(payload).execute()
            except Exception as exc:
                logger.warning("Falha ao inserir gold_transaction com status; tentando payload reduzido: %s", exc)
                payload.pop("status", None)
                header_response = self.client.table("gold_transactions").insert(payload).execute()
            header_data = cast(List[Dict[str, Any]], header_response.data or [])
            if not header_data:
                logger.warning("Insert de gold_transactions retornou resposta vazia")
                return None
            header = header_data[0]
            transaction_id = _safe_int(header.get("id"), context="gold_transactions.id")
            if transaction_id <= 0:
                logger.warning("Gold transaction criada sem id valido; pulando efeitos colaterais")
                return header
            if pagamentos:
                rows: List[Dict[str, Any]] = []
                for pagamento in pagamentos:
                    moeda = str(pagamento.get("moeda", "USD")).upper()
                    cambio = _safe_decimal(pagamento.get("cambio_para_usd", 1), context=f"gold_payments.{moeda}.cambio_para_usd")
                    valor_moeda = _safe_decimal(pagamento.get("valor_moeda", 0), context=f"gold_payments.{moeda}.valor_moeda")
                    valor_usd = _safe_decimal(pagamento.get("valor_usd", 0), context=f"gold_payments.{moeda}.valor_usd")
                    if moeda != "USD" and cambio > 0:
                        self._safe_record_fx_rate("USD", moeda, cambio, "gold_payments")
                    rows.append({"gold_transaction_id": transaction_id, "moeda": moeda, "valor_moeda": str(valor_moeda), "cambio_para_usd": str(cambio), "valor_usd": str(valor_usd), "forma_pagamento": pagamento.get("forma_pagamento"), "criado_em": datetime.now(timezone.utc).isoformat()})
                try:
                    self.client.table("gold_payments").insert(rows).execute()
                except Exception as exc:
                    logger.warning("Falha ao inserir gold_payments com criado_em; tentando payload reduzido: %s", exc)
                    try:
                        self.client.table("gold_payments").insert([{k: v for k, v in row.items() if k != "criado_em"} for row in rows]).execute()
                    except Exception as fallback_exc:
                        logger.warning("Falha ao inserir gold_payments em fallback reduzido: %s", fallback_exc)
            op_kind = str(payload.get("tipo_operacao", "compra"))
            peso = _safe_decimal(payload.get("peso", 0), context="gold_transactions.peso")
            pessoa = str(payload.get("pessoa", "N/A"))
            cliente_id = _safe_int(payload.get("cliente_id"), context="gold_transactions.cliente_id")
            fechamento_gramas = _safe_decimal(payload.get("fechamento_gramas"), str(peso or Decimal("0")), context="gold_transactions.fechamento_gramas")
            pending_grams = max(Decimal("0"), peso - fechamento_gramas)
            self.update_caixas_from_transaction(transaction_id, op_kind, peso, pagamentos, pessoa)
            operador = str(payload.get("operador_id", "N/A"))
            journal_lines: List[Dict[str, Any]] = []
            for pagamento in pagamentos:
                moeda = str(pagamento.get("moeda", "USD")).upper()
                valor_moeda = _safe_decimal(pagamento.get("valor_moeda", 0), context=f"journal.{moeda}.valor_moeda")
                if valor_moeda <= 0:
                    continue
                if op_kind == "compra":
                    journal_lines.extend([
                        {"account_code": "INVENTORY_COMMODITIES", "currency_code": moeda, "debit": valor_moeda, "credit": Decimal("0"), "commodity_symbol": "XAU", "quantity": peso if moeda == list(pagamentos)[0].get("moeda", "USD").upper() else None},
                        {"account_code": "CASH_" + moeda, "currency_code": moeda, "debit": Decimal("0"), "credit": valor_moeda},
                    ])
                else:
                    journal_lines.extend([
                        {"account_code": "CASH_" + moeda, "currency_code": moeda, "debit": valor_moeda, "credit": Decimal("0")},
                        {"account_code": "INVENTORY_COMMODITIES", "currency_code": moeda, "debit": Decimal("0"), "credit": valor_moeda, "commodity_symbol": "XAU", "quantity": peso if moeda == list(pagamentos)[0].get("moeda", "USD").upper() else None},
                    ])
            self._safe_record_journal_entry("gold_transactions", transaction_id, f"{op_kind} ouro - {pessoa}", payload.get("source_message_id"), operador, {"pessoa": pessoa, "tipo_operacao": op_kind, "peso": str(peso), "teor": str(payload.get("teor")), "pagamentos": pagamentos}, journal_lines)
            if cliente_id > 0 and pending_grams > 0:
                self.record_cliente_operation_balance(cliente_id, transaction_id, op_kind, pending_grams, pessoa)
            self.sync_gold_inventory_ledger()
            self._invalidate_runtime_cache("saldo_caixa", "gold_inventory_overview", self._gold_inventory_status_cache_key(open_only=False), self._gold_inventory_status_cache_key(open_only=True), "gold_pending_closure_grams")
            self._invalidate_cliente_account_snapshot_cache(cliente_id)
            self._invalidate_client_list_cache()
            return header
        except Exception as exc:
            logger.warning("Falha ao inserir gold_transaction para %s: %s", payload.get("pessoa") if isinstance(payload, dict) else "N/A", exc)
            return None

    def cancel_gold_transaction(self, operation_id: int, cancelled_by: Optional[str] = None) -> bool:
        try:
            header_response = self.client.table("gold_transactions").select("*").eq("id", operation_id).limit(1).execute()
            header_rows = cast(List[Dict[str, Any]], header_response.data or [])
            if not header_rows:
                return False
            header = header_rows[0]
            if str(header.get("status") or "registrada").lower() == "cancelada":
                return True
            cliente_id = _safe_int(header.get("cliente_id"), context="gold_transactions.cancel.cliente_id")
            peso = _safe_decimal(header.get("peso") or "0", context="gold_transactions.cancel.peso")
            fechamento_gramas = _safe_decimal(header.get("fechamento_gramas"), str(peso or Decimal("0")), context="gold_transactions.cancel.fechamento_gramas")
            pending_grams = max(Decimal("0"), peso - fechamento_gramas)
            movimentacoes_response = self.client.table("caixas_movimentacoes").select("caixa_moeda,valor").eq("gold_transaction_id", operation_id).order("id", desc=False).execute()
            movimentacoes = cast(List[Dict[str, Any]], movimentacoes_response.data or [])
            for movimento in movimentacoes:
                moeda = str(movimento.get("caixa_moeda") or "").upper()
                if not moeda:
                    continue
                valor = _safe_decimal(movimento.get("valor") or "0", context=f"caixas_movimentacoes.{moeda}.valor")
                if valor == 0:
                    continue
                saldo_atual = Decimal(str(self.get_saldo_caixa().get(moeda, "0")))
                saldo_novo = saldo_atual - valor
                self.client.table("caixas").update({"saldo": str(saldo_novo), "atualizado_em": datetime.now(timezone.utc).isoformat()}).eq("moeda", moeda).execute()
                self._invalidate_runtime_cache("saldo_caixa")
                self._record_caixa_movimentacao(moeda, "ajuste", operation_id, valor * Decimal("-1"), saldo_atual, saldo_novo, f"Reversao por cancelamento GT-{operation_id}", str(header.get("pessoa") or cancelled_by or "sistema"))
            if cliente_id > 0 and pending_grams > 0:
                self.record_cliente_operation_balance(cliente_id, operation_id, str(header.get("tipo_operacao") or "compra"), pending_grams, str(header.get("pessoa") or cancelled_by or "sistema"), reverse=True)
            self.client.table("gold_transactions").update({"status": "cancelada"}).eq("id", operation_id).execute()
            self.sync_gold_inventory_ledger()
            self._invalidate_runtime_cache("saldo_caixa", "gold_inventory_overview", self._gold_inventory_status_cache_key(open_only=False), self._gold_inventory_status_cache_key(open_only=True), "gold_pending_closure_grams")
            self._invalidate_cliente_account_snapshot_cache(cliente_id)
            self._invalidate_client_list_cache()
            return True
        except Exception as exc:
            logger.warning("Falha ao cancelar gold_transaction %s: %s", operation_id, exc)
            return False