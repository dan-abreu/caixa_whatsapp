from .common import Any, Decimal, Dict, List, _safe_decimal, cast, datetime, logger, timezone


class CaixasRebuildMixin:
    @staticmethod
    def _safe_int(value: Any, default: int = 0, *, context: str = "valor") -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            logger.warning("Falha ao converter inteiro em %s: %s", context, exc)
            return default

    def _calculate_caixas_from_history(self) -> Dict[str, Decimal]:
        saldos: Dict[str, Decimal] = {"XAU": Decimal("0"), "EUR": Decimal("0"), "USD": Decimal("0"), "SRD": Decimal("0"), "BRL": Decimal("0")}
        ouro = self.get_ativo_by_nome("Ouro") or self.get_ativo_by_nome("Ouro 24k")
        ouro_id = int(ouro["id"]) if ouro else None
        try:
            t_resp = self.client.table("transacoes").select("tipo_operacao,ativo_id,quantidade,moeda_liquidacao,valor_moeda,valor_total,status").execute()
            transacao_rows = cast(List[Dict[str, Any]], t_resp.data or [])
        except Exception as exc:
            logger.warning("Falha ao carregar historico de transacoes para recalculo de caixas: %s", exc)
            transacao_rows = []
        for row in transacao_rows:
            if str(row.get("status") or "registrada").lower() == "cancelada":
                continue
            tipo = str(row.get("tipo_operacao") or "").lower()
            aid = self._safe_int(row.get("ativo_id", 0), context="transacoes.ativo_id")
            qty = _safe_decimal(row.get("quantidade", "0"), context="transacoes.quantidade")
            if ouro_id is not None and aid == ouro_id:
                if tipo == "compra":
                    saldos["XAU"] += qty
                elif tipo in ("venda", "cambio"):
                    saldos["XAU"] -= qty
            moeda = str(row.get("moeda_liquidacao") or "USD").upper()
            if row.get("valor_moeda") is not None:
                valor_m = _safe_decimal(row.get("valor_moeda"), context=f"transacoes.{moeda}.valor_moeda")
            else:
                valor_m = _safe_decimal(row.get("valor_total", "0"), context="transacoes.valor_total")
                moeda = "USD"
            if moeda in saldos:
                saldos[moeda] += valor_m if tipo == "venda" else -valor_m if tipo == "compra" else Decimal("0")
        gt_tipo_map: Dict[int, str] = {}
        gt_context_pagamentos: Dict[int, List[Dict[str, Any]]] = {}
        try:
            gt_resp = self.client.table("gold_transactions").select("*").execute()
            gt_rows = cast(List[Dict[str, Any]], gt_resp.data or [])
            for row in gt_rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                gid = self._safe_int(row.get("id", 0), context="gold_transactions.id")
                if gid <= 0:
                    continue
                tipo = str(row.get("tipo_operacao") or "").lower()
                gt_tipo_map[gid] = tipo
                peso = _safe_decimal(row.get("peso", "0"), context=f"gold_transactions.{gid}.peso")
                if tipo == "compra":
                    saldos["XAU"] += peso
                elif tipo in ("venda", "cambio"):
                    saldos["XAU"] -= peso
                contexto_raw = row.get("contexto")
                if isinstance(contexto_raw, dict):
                    pagamentos_ctx = cast(Dict[str, Any], contexto_raw).get("pagamentos")
                    if isinstance(pagamentos_ctx, list):
                        gt_context_pagamentos[gid] = [cast(Dict[str, Any], raw_pagamento) for raw_pagamento in pagamentos_ctx if isinstance(raw_pagamento, dict)]
        except Exception as exc:
            logger.warning("Falha ao carregar gold_transactions para recalculo de caixas: %s", exc)
            gt_tipo_map = {}
            gt_context_pagamentos = {}
        gp_tx_ids: set[int] = set()
        try:
            gp_resp = self.client.table("gold_payments").select("gold_transaction_id,moeda,valor_moeda").execute()
            for row in cast(List[Dict[str, Any]], gp_resp.data or []):
                gid = self._safe_int(row.get("gold_transaction_id", 0), context="gold_payments.gold_transaction_id")
                if gid <= 0:
                    continue
                tipo = gt_tipo_map.get(gid, "compra")
                moeda = str(row.get("moeda", "USD")).upper()
                val = _safe_decimal(row.get("valor_moeda", "0"), context=f"gold_payments.{gid}.{moeda}.valor_moeda")
                gp_tx_ids.add(gid)
                if moeda in saldos:
                    saldos[moeda] += val if tipo == "venda" else -val if tipo == "compra" else Decimal("0")
        except Exception as exc:
            logger.warning("Falha ao carregar gold_payments para recalculo de caixas: %s", exc)
            gp_tx_ids = set()
        for gid, pagamentos in gt_context_pagamentos.items():
            if gid in gp_tx_ids:
                continue
            tipo = gt_tipo_map.get(gid, "compra")
            for pagamento in pagamentos:
                moeda = str(pagamento.get("moeda", "USD")).upper()
                val = _safe_decimal(pagamento.get("valor_moeda", "0"), context=f"gold_transactions.{gid}.{moeda}.valor_moeda")
                if moeda in saldos:
                    saldos[moeda] += val if tipo == "venda" else -val if tipo == "compra" else Decimal("0")
        return saldos

    def backfill_caixas_from_history(self, clear_movements: bool = False) -> Dict[str, Any]:
        self._ensure_caixas_exist()
        current = self.get_saldo_caixa()
        recalculated = self._calculate_caixas_from_history()
        failed_updates: List[str] = []
        if clear_movements:
            try:
                self.client.table("caixas_movimentacoes").delete().neq("id", 0).execute()
            except Exception as exc:
                logger.warning("Falha ao limpar caixas_movimentacoes no backfill: %s", exc)
        now_iso = datetime.now(timezone.utc).isoformat()
        for moeda in ["XAU", "EUR", "USD", "SRD", "BRL"]:
            saldo_anterior = Decimal(str(current.get(moeda, "0")))
            saldo_novo = recalculated.get(moeda, Decimal("0"))
            try:
                self.client.table("caixas").update({"saldo": str(saldo_novo), "atualizado_em": now_iso}).eq("moeda", moeda).execute()
            except Exception as exc:
                logger.warning("Falha ao atualizar caixa %s durante backfill: %s", moeda, exc)
                failed_updates.append(moeda)
                continue
            if saldo_anterior != saldo_novo:
                self._record_caixa_movimentacao(moeda, "ajuste", None, saldo_novo - saldo_anterior, saldo_anterior, saldo_novo, "Backfill histórico para novo sistema de 5 caixas", "sistema")
        self._invalidate_runtime_cache("saldo_caixa")
        return {"before": current, "after": {k: str(v) for k, v in recalculated.items()}, "failed_updates": failed_updates}