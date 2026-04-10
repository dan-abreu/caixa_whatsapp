from .common import Any, Decimal, Dict, List, Optional, cast, datetime, logger, timezone


class LegacyTransactionsMixin:
    def insert_transacao(self, tipo_operacao: str, ativo_id: int, quantidade: Decimal, cotacao_usada: Decimal, valor_total: Decimal, operador_id: str, source_message_id: Optional[str] = None, status: str = "registrada", moeda_liquidacao: str = "USD", valor_moeda: Optional[Decimal] = None, cambio_para_usd: Decimal = Decimal("1.0")) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"tipo_operacao": tipo_operacao, "ativo_id": ativo_id, "quantidade": str(quantidade), "cotacao_usada": str(cotacao_usada), "valor_total": str(valor_total), "operador_id": operador_id, "source_message_id": source_message_id, "status": status, "data_hora": datetime.now(timezone.utc).isoformat(), "moeda_liquidacao": moeda_liquidacao, "cambio_para_usd": str(cambio_para_usd)}
        if valor_moeda is not None:
            payload["valor_moeda"] = str(valor_moeda)
        try:
            response = self.client.table("transacoes").insert(payload).execute()
        except Exception as exc:
            logger.warning("Falha ao inserir transacao com colunas estendidas; tentando payload reduzido: %s", exc)
            payload_fallback = dict(payload)
            for col in ("source_message_id", "moeda_liquidacao", "valor_moeda", "cambio_para_usd"):
                payload_fallback.pop(col, None)
            response = self.client.table("transacoes").insert(payload_fallback).execute()
        data = cast(List[Dict[str, Any]], response.data or [])
        if not data:
            from .common import DatabaseError
            raise DatabaseError("Falha ao inserir transação.")
        created = data[0]
        moeda_liq = moeda_liquidacao.upper()
        if moeda_liq != "USD" and cambio_para_usd > 0:
            self._safe_record_fx_rate("USD", moeda_liq, cambio_para_usd, "transacoes")
        ativo = self.get_ativo_by_id(ativo_id)
        ativo_nome = str((ativo or {}).get("nome", f"ATIVO_{ativo_id}"))
        ativo_tipo = str((ativo or {}).get("tipo", ""))
        asset_code = "INVENTORY_COMMODITIES" if ativo_tipo == "ouro" else "FX_POSITION_ASSET"
        amount_usd = Decimal(str(valor_total))
        settlement_amount = valor_moeda if valor_moeda is not None else amount_usd
        settlement_usd = Decimal(str(settlement_amount)) if moeda_liq == "USD" else (Decimal(str(settlement_amount)) / cambio_para_usd if cambio_para_usd > 0 else amount_usd)
        lines: List[Dict[str, Any]] = []
        if tipo_operacao == "compra":
            lines = [{"account_code": asset_code, "currency_code": "USD", "debit": amount_usd, "credit": Decimal("0"), "commodity_symbol": "XAU" if ativo_tipo == "ouro" else None, "quantity": quantidade}, {"account_code": "CASH_USD_EQUIV", "currency_code": "USD", "debit": Decimal("0"), "credit": settlement_usd}]
            diff = settlement_usd - amount_usd
            if diff > 0:
                lines.append({"account_code": "FX_GAIN_LOSS", "currency_code": "USD", "debit": diff, "credit": Decimal("0")})
            elif diff < 0:
                lines.append({"account_code": "FX_GAIN_LOSS", "currency_code": "USD", "debit": Decimal("0"), "credit": diff * Decimal("-1")})
        elif tipo_operacao in ("venda", "cambio"):
            lines = [{"account_code": "CASH_USD_EQUIV", "currency_code": "USD", "debit": settlement_usd, "credit": Decimal("0")}, {"account_code": asset_code, "currency_code": "USD", "debit": Decimal("0"), "credit": amount_usd, "commodity_symbol": "XAU" if ativo_tipo == "ouro" else None, "quantity": quantidade}]
            diff = settlement_usd - amount_usd
            if diff > 0:
                lines.append({"account_code": "FX_GAIN_LOSS", "currency_code": "USD", "debit": Decimal("0"), "credit": diff})
            elif diff < 0:
                lines.append({"account_code": "FX_GAIN_LOSS", "currency_code": "USD", "debit": diff * Decimal("-1"), "credit": Decimal("0")})
        created_id = int(str(created.get("id"))) if created.get("id") is not None and str(created.get("id")).isdigit() else None
        self._safe_record_journal_entry("transacoes", created_id, f"{tipo_operacao} {ativo_nome}", source_message_id, operador_id, {"ativo_id": ativo_id, "ativo_nome": ativo_nome, "ativo_tipo": ativo_tipo, "quantidade": str(quantidade), "cotacao_usada": str(cotacao_usada), "valor_total_usd": str(valor_total), "settlement_currency": moeda_liq, "settlement_amount": str(settlement_amount), "settlement_usd_equivalent": str(settlement_usd), "realized_fx_diff_usd": str(settlement_usd - amount_usd), "cambio_para_usd": str(cambio_para_usd), "status": status}, lines)
        return created

    def insert_log(self, nivel: str, remetente: Optional[str] = None, mensagem_recebida: Optional[str] = None, resposta_enviada: Optional[str] = None, contexto: Optional[Dict[str, Any]] = None, erro: Optional[str] = None) -> None:
        self.client.table("logs").insert({"nivel": nivel, "remetente": remetente, "mensagem_recebida": mensagem_recebida, "resposta_enviada": resposta_enviada, "contexto": contexto or {}, "erro": erro, "data_hora": datetime.now(timezone.utc).isoformat()}).execute()

    def get_processed_message(self, provider_message_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.table("mensagens_processadas").select("id,provider_message_id,resposta_payload,status_code").eq("provider_message_id", provider_message_id).limit(1).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception as exc:
            logger.warning("Falha ao consultar mensagem processada %s: %s", provider_message_id, exc)
            return None

    def save_processed_message(self, provider_message_id: str, remetente: str, mensagem_recebida: str, resposta_payload: Dict[str, Any], status_code: int) -> None:
        try:
            payload = {"provider_message_id": provider_message_id, "remetente": remetente, "mensagem_recebida": mensagem_recebida, "resposta_payload": resposta_payload, "status_code": status_code, "criado_em": datetime.now(timezone.utc).isoformat()}
            existing = self.get_processed_message(provider_message_id)
            if existing:
                self.client.table("mensagens_processadas").update(payload).eq("provider_message_id", provider_message_id).execute()
            else:
                self.client.table("mensagens_processadas").insert(payload).execute()
        except Exception as exc:
            logger.warning("Falha ao salvar mensagem processada %s: %s", provider_message_id, exc)
            return

    def save_conversation_session(self, remetente: str, estado: str, contexto: Dict[str, Any]) -> None:
        try:
            payload = {"remetente": remetente, "estado": estado, "contexto": contexto, "atualizado_em": datetime.now(timezone.utc).isoformat()}
            response = self.client.table("sessoes_conversa").select("id").eq("remetente", remetente).limit(1).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            if data:
                self.client.table("sessoes_conversa").update(payload).eq("remetente", remetente).execute()
            else:
                self.client.table("sessoes_conversa").insert(payload).execute()
        except Exception as exc:
            logger.warning("Falha ao salvar sessao de conversa %s: %s", remetente, exc)
            return

    def get_conversation_session(self, remetente: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.table("sessoes_conversa").select("id,remetente,estado,contexto,atualizado_em").eq("remetente", remetente).limit(1).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception as exc:
            logger.warning("Falha ao consultar sessao de conversa %s: %s", remetente, exc)
            return None

    def clear_conversation_session(self, remetente: str) -> None:
        try:
            self.client.table("sessoes_conversa").delete().eq("remetente", remetente).execute()
        except Exception as exc:
            logger.warning("Falha ao limpar sessao de conversa %s: %s", remetente, exc)
            return