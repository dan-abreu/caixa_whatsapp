from .common import Any, Decimal, Dict, List, Optional, cast, datetime, logger, timezone


class TransferMoneyMixin:
    def insert_transfer_money(self, origem_moeda: str, destino_moeda: str, valor_origem: Decimal, valor_destino: Decimal, cambio_origem_para_usd: Decimal, cambio_destino_para_usd: Decimal, operador_id: str, taxa_servico_origem: Decimal = Decimal("0"), sender_nome: Optional[str] = None, receiver_nome: Optional[str] = None, source_message_id: Optional[str] = None, status: str = "registrada", metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        origem = origem_moeda.upper()
        destino = destino_moeda.upper()
        if valor_origem <= 0 or valor_destino <= 0 or cambio_origem_para_usd <= 0 or cambio_destino_para_usd <= 0:
            return None
        if taxa_servico_origem < 0 or taxa_servico_origem >= valor_origem:
            logger.warning(
                "Transfer money invalido %s->%s: taxa_servico_origem %s incompatível com valor_origem %s",
                origem,
                destino,
                taxa_servico_origem,
                valor_origem,
            )
            return None
        valor_origem_usd = valor_origem / cambio_origem_para_usd
        valor_destino_usd = valor_destino / cambio_destino_para_usd
        fee_usd = taxa_servico_origem / cambio_origem_para_usd if taxa_servico_origem > 0 else Decimal("0")
        payload: Dict[str, Any] = {"data_hora": datetime.now(timezone.utc).isoformat(), "sender_nome": sender_nome, "receiver_nome": receiver_nome, "origem_moeda": origem, "destino_moeda": destino, "valor_origem": str(valor_origem), "cambio_origem_para_usd": str(cambio_origem_para_usd), "cambio_destino_para_usd": str(cambio_destino_para_usd), "taxa_servico_origem": str(taxa_servico_origem), "valor_destino": str(valor_destino), "valor_origem_usd": str(valor_origem_usd), "valor_destino_usd": str(valor_destino_usd), "operador_id": operador_id, "source_message_id": source_message_id, "status": status, "metadata": metadata or {}, "criado_em": datetime.now(timezone.utc).isoformat()}
        try:
            response = self.client.table("transfer_money_transactions").insert(payload).execute()
        except Exception as exc:
            logger.warning("Falha ao inserir transfer money %s->%s: %s", origem, destino, exc)
            return None
        raw_data = response.data or []
        if isinstance(raw_data, list):
            if not raw_data:
                logger.warning("Insert de transfer money %s->%s retornou resposta vazia", origem, destino)
                return None
            created = cast(Dict[str, Any], raw_data[0])
        elif isinstance(raw_data, dict):
            created = cast(Dict[str, Any], raw_data)
        else:
            logger.warning("Insert de transfer money %s->%s retornou payload invalido: %s", origem, destino, type(raw_data).__name__)
            return None
        if origem != "USD":
            self._safe_record_fx_rate("USD", origem, cambio_origem_para_usd, "transfer_money")
        if destino != "USD":
            self._safe_record_fx_rate("USD", destino, cambio_destino_para_usd, "transfer_money")
        transfer_clear_usd = valor_origem_usd - fee_usd
        fx_diff_usd = transfer_clear_usd - valor_destino_usd
        lines: List[Dict[str, Any]] = [
            {"account_code": "CASH_USD_EQUIV", "currency_code": "USD", "debit": valor_origem_usd, "credit": Decimal("0")},
            {"account_code": "TRANSFER_CLEARING", "currency_code": "USD", "debit": Decimal("0"), "credit": transfer_clear_usd},
            {"account_code": "TRANSFER_CLEARING", "currency_code": "USD", "debit": valor_destino_usd, "credit": Decimal("0")},
            {"account_code": "CASH_USD_EQUIV", "currency_code": "USD", "debit": Decimal("0"), "credit": valor_destino_usd},
        ]
        if fee_usd > 0:
            lines.append({"account_code": "TRANSFER_FEE_REVENUE", "currency_code": "USD", "debit": Decimal("0"), "credit": fee_usd})
        if fx_diff_usd > 0:
            lines.append({"account_code": "FX_GAIN_LOSS", "currency_code": "USD", "debit": Decimal("0"), "credit": fx_diff_usd})
        elif fx_diff_usd < 0:
            lines.append({"account_code": "FX_GAIN_LOSS", "currency_code": "USD", "debit": fx_diff_usd * Decimal("-1"), "credit": Decimal("0")})
        created_id = int(str(created.get("id"))) if created.get("id") is not None and str(created.get("id")).isdigit() else None
        self._safe_record_journal_entry("transfer_money_transactions", created_id, f"transfer money {origem}->{destino}", source_message_id, operador_id, {"origem_moeda": origem, "destino_moeda": destino, "valor_origem": str(valor_origem), "valor_destino": str(valor_destino), "valor_origem_usd": str(valor_origem_usd), "valor_destino_usd": str(valor_destino_usd), "fee_usd": str(fee_usd), "fx_diff_usd": str(fx_diff_usd), "status": status}, lines)
        return created