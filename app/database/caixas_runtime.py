from .common import Any, Decimal, Dict, List, Optional, _safe_decimal, _safe_decimal_from_row, cast, datetime, logger, timezone


class CaixasRuntimeMixin:
    def _ensure_caixas_exist(self) -> None:
        if type(self)._CAIXAS_READY is True:
            return
        moedas = ["XAU", "EUR", "USD", "SRD", "BRL"]
        try:
            response = self.client.table("caixas").select("moeda").in_("moeda", moedas).execute()
            existing = {str(row.get("moeda") or "").upper() for row in cast(List[Dict[str, Any]], response.data or [])}
        except Exception as exc:
            logger.warning("Falha ao verificar caixas existentes: %s", exc)
            return
        for moeda in [moeda for moeda in moedas if moeda not in existing]:
            try:
                self.client.table("caixas").insert({"moeda": moeda, "saldo": "0"}).execute()
            except Exception as exc:
                logger.warning("Falha ao criar caixa padrao %s: %s", moeda, exc)
        type(self)._CAIXAS_READY = True

    def _record_caixa_movimentacao(self, caixa_moeda: str, tipo_operacao: str, gold_transaction_id: Optional[int], valor: Decimal, saldo_anterior: Decimal, saldo_posterior: Decimal, descricao: Optional[str] = None, pessoa: Optional[str] = None) -> None:
        try:
            self.client.table("caixas_movimentacoes").insert({"caixa_moeda": caixa_moeda.upper(), "tipo_operacao": tipo_operacao, "gold_transaction_id": gold_transaction_id, "valor": str(valor), "saldo_anterior": str(saldo_anterior), "saldo_posterior": str(saldo_posterior), "descricao": descricao, "pessoa": pessoa, "criado_em": datetime.now(timezone.utc).isoformat()}).execute()
        except Exception as exc:
            logger.warning("Falha ao registrar movimentacao de caixa %s/%s: %s", caixa_moeda, tipo_operacao, exc)
            return

    def update_caixas_from_transaction(self, gold_transaction_id: int, tipo_operacao: str, peso_gramas: Decimal, pagamentos: List[Dict[str, Any]], pessoa: Optional[str] = None) -> None:
        try:
            self._ensure_caixas_exist()
            if peso_gramas > 0:
                movimento_xau = peso_gramas * (Decimal("1") if tipo_operacao == "compra" else Decimal("-1"))
                rows = cast(List[Dict[str, Any]], self.client.table("caixas").select("saldo").eq("moeda", "XAU").execute().data or [])
                saldo_anterior_xau = _safe_decimal(rows[0].get("saldo", 0), context="caixas.XAU.saldo") if rows else Decimal("0")
                saldo_posterior_xau = saldo_anterior_xau + movimento_xau
                self.client.table("caixas").update({"saldo": str(saldo_posterior_xau), "atualizado_em": datetime.now(timezone.utc).isoformat()}).eq("moeda", "XAU").execute()
                self._invalidate_runtime_cache("saldo_caixa")
                self._record_caixa_movimentacao("XAU", tipo_operacao, gold_transaction_id, movimento_xau, saldo_anterior_xau, saldo_posterior_xau, f"{tipo_operacao} ouro", pessoa)
            for pagamento in pagamentos:
                moeda = str(pagamento.get("moeda", "USD")).upper()
                valor_moeda = _safe_decimal(pagamento.get("valor_moeda", "0"), context=f"pagamentos.{moeda}.valor_moeda")
                if valor_moeda == 0:
                    continue
                movimento_moeda = valor_moeda * (Decimal("-1") if tipo_operacao == "compra" else Decimal("1"))
                rows = cast(List[Dict[str, Any]], self.client.table("caixas").select("saldo").eq("moeda", moeda).execute().data or [])
                saldo_anterior_moeda = _safe_decimal(rows[0].get("saldo", 0), context=f"caixas.{moeda}.saldo") if rows else Decimal("0")
                saldo_posterior_moeda = saldo_anterior_moeda + movimento_moeda
                self.client.table("caixas").update({"saldo": str(saldo_posterior_moeda), "atualizado_em": datetime.now(timezone.utc).isoformat()}).eq("moeda", moeda).execute()
                self._invalidate_runtime_cache("saldo_caixa")
                self._record_caixa_movimentacao(moeda, tipo_operacao, gold_transaction_id, movimento_moeda, saldo_anterior_moeda, saldo_posterior_moeda, f"{tipo_operacao} ouro ({moeda})", pessoa)
        except Exception as exc:
            logger.warning("Falha ao atualizar caixas a partir da transacao %s: %s", gold_transaction_id, exc)

    def get_saldo_caixa(self) -> Dict[str, Any]:
        cached = self._get_runtime_cache("saldo_caixa")
        if cached is not None:
            return cast(Dict[str, Any], cached)
        try:
            self._ensure_caixas_exist()
            rows = cast(List[Dict[str, Any]], self.client.table("caixas").select("moeda,saldo").execute().data or [])
            result = {str(row.get("moeda", "")).upper(): str(_safe_decimal_from_row(row, "saldo")) for row in rows}
            for moeda in ["XAU", "EUR", "USD", "SRD", "BRL"]:
                result.setdefault(moeda, "0")
            return self._set_runtime_cache("saldo_caixa", result)
        except Exception as exc:
            logger.warning("Falha ao carregar saldo de caixas: %s", exc)
            return {"XAU": "0", "EUR": "0", "USD": "0", "SRD": "0", "BRL": "0"}