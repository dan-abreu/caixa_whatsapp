from .common import Any, Decimal, Dict, List, Optional, _safe_decimal_from_row, _safe_int, cast, logger


class ReportingMixin:
    def get_daily_gold_summary(self, start_iso: str, end_iso: str) -> Dict[str, Any]:
        try:
            resp_t = self.client.table("transacoes").select("id,valor_total,status").gte("data_hora", start_iso).lt("data_hora", end_iso).execute()
            t_rows = [row for row in cast(List[Dict[str, Any]], resp_t.data or []) if str(row.get("status") or "registrada").lower() != "cancelada"]
            resp_g = self.client.table("gold_transactions").select("*").gte("criado_em", start_iso).lt("criado_em", end_iso).execute()
            g_rows = [row for row in cast(List[Dict[str, Any]], resp_g.data or []) if str(row.get("status") or "registrada").lower() != "cancelada"]
            return {"total_operacoes": len(t_rows) + len(g_rows), "total_usd": str(sum((_safe_decimal_from_row(r, "valor_total") for r in t_rows), Decimal("0")) + sum((_safe_decimal_from_row(r, "total_usd") for r in g_rows), Decimal("0"))), "total_pago_usd": str(sum((_safe_decimal_from_row(r, "total_pago_usd") for r in g_rows), Decimal("0"))), "total_diferenca_usd": str(sum((_safe_decimal_from_row(r, "diferenca_usd") for r in g_rows), Decimal("0")))}
        except Exception as exc:
            logger.warning("Falha ao montar resumo diario de ouro: %s", exc)
            return {"total_operacoes": 0, "total_usd": "0", "total_pago_usd": "0", "total_diferenca_usd": "0"}

    def get_daily_gold_summary_by_operator(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        try:
            response = self.client.table("gold_transactions").select("*").gte("criado_em", start_iso).lt("criado_em", end_iso).execute()
            rows = [row for row in cast(List[Dict[str, Any]], response.data or []) if str(row.get("status") or "registrada").lower() != "cancelada"]
            grouped: Dict[str, Dict[str, Decimal]] = {}
            for row in rows:
                operador = str(row.get("operador_id", "desconhecido"))
                grouped.setdefault(operador, {"total_usd": Decimal("0"), "total_pago_usd": Decimal("0"), "total_diferenca_usd": Decimal("0"), "total_operacoes": Decimal("0")})
                grouped[operador]["total_usd"] += _safe_decimal_from_row(row, "total_usd")
                grouped[operador]["total_pago_usd"] += _safe_decimal_from_row(row, "total_pago_usd")
                grouped[operador]["total_diferenca_usd"] += _safe_decimal_from_row(row, "diferenca_usd")
                grouped[operador]["total_operacoes"] += Decimal("1")
            return [{"operador_id": operador, "total_operacoes": int(vals["total_operacoes"]), "total_usd": str(vals["total_usd"]), "total_pago_usd": str(vals["total_pago_usd"]), "total_diferenca_usd": str(vals["total_diferenca_usd"])} for operador, vals in grouped.items()]
        except Exception as exc:
            logger.warning("Falha ao agrupar resumo diario por operador: %s", exc)
            return []

    def get_gold_summary_range(self, start_iso: str, end_iso: str) -> Dict[str, Any]:
        return self.get_daily_gold_summary(start_iso, end_iso)

    def get_gold_summary_by_currency(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        try:
            gt_response = self.client.table("gold_transactions").select("*").gte("criado_em", start_iso).lt("criado_em", end_iso).execute()
            valid_ids = [transaction_id for transaction_id in (_safe_int(row.get("id"), context="gold_transactions.summary.id") for row in cast(List[Dict[str, Any]], gt_response.data or []) if str(row.get("status") or "registrada").lower() != "cancelada") if transaction_id > 0]
            if not valid_ids:
                return []
            response = self.client.table("gold_payments").select("moeda,valor_moeda,valor_usd").in_("gold_transaction_id", valid_ids).execute()
            grouped: Dict[str, Dict[str, Decimal]] = {}
            for row in cast(List[Dict[str, Any]], response.data or []):
                moeda = str(row.get("moeda", "UNK"))
                grouped.setdefault(moeda, {"total_valor_moeda": Decimal("0"), "total_valor_usd": Decimal("0"), "total_pagamentos": Decimal("0")})
                grouped[moeda]["total_valor_moeda"] += _safe_decimal_from_row(row, "valor_moeda")
                grouped[moeda]["total_valor_usd"] += _safe_decimal_from_row(row, "valor_usd")
                grouped[moeda]["total_pagamentos"] += Decimal("1")
            return [{"moeda": moeda, "total_pagamentos": int(vals["total_pagamentos"]), "total_valor_moeda": str(vals["total_valor_moeda"]), "total_valor_usd": str(vals["total_valor_usd"])} for moeda, vals in grouped.items()]
        except Exception as exc:
            logger.warning("Falha ao resumir pagamentos por moeda: %s", exc)
            return []

    def get_extrato_transactions(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        from datetime import datetime as _dt
        result: List[Dict[str, Any]] = []
        gt_timestamps: List[Dict[str, str]] = []
        try:
            gt_resp = self.client.table("gold_transactions").select("*").gte("criado_em", start_iso).lt("criado_em", end_iso).order("criado_em", desc=False).execute()
            gt_rows = cast(List[Dict[str, Any]], gt_resp.data or [])
            gt_id_list = [transaction_id for transaction_id in (_safe_int(row.get("id"), context="gold_transactions.extrato.id") for row in gt_rows) if transaction_id > 0]
            payments_by_tx: Dict[int, List[Dict[str, Any]]] = {}
            if gt_id_list:
                gp_resp = self.client.table("gold_payments").select("gold_transaction_id,moeda,valor_moeda,cambio_para_usd,valor_usd,forma_pagamento").in_("gold_transaction_id", gt_id_list).execute()
                for p in cast(List[Dict[str, Any]], gp_resp.data or []):
                    tid = _safe_int(p.get("gold_transaction_id"), context="gold_payments.gold_transaction_id")
                    if tid > 0:
                        payments_by_tx.setdefault(tid, []).append(p)
            for row in gt_rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                transaction_id = row.get("id")
                tid_int = _safe_int(transaction_id, context="gold_transactions.extrato.id")
                criado_em = str(row.get("criado_em") or "")
                gt_timestamps.append({"ts": criado_em, "op": str(row.get("operador_id") or "")})
                result.append({"source": "gold_transactions", "id": transaction_id, "cliente_id": row.get("cliente_id"), "tipo_operacao": row.get("tipo_operacao"), "origem": row.get("origem"), "teor": row.get("teor"), "peso": row.get("peso"), "preco_usd": row.get("preco_usd"), "total_usd": row.get("total_usd"), "total_pago_usd": row.get("total_pago_usd"), "diferenca_usd": row.get("diferenca_usd"), "pessoa": row.get("pessoa"), "operador_id": str(row.get("operador_id") or ""), "forma_pagamento": row.get("forma_pagamento"), "observacoes": row.get("observacoes"), "contexto": row.get("contexto") if isinstance(row.get("contexto"), dict) else {}, "criado_em": criado_em, "pagamentos": payments_by_tx.get(tid_int, [])})
        except Exception as exc:
            logger.warning("Falha ao carregar extrato de gold_transactions: %s", exc)
        try:
            t_resp = self.client.table("transacoes").select("id,tipo_operacao,quantidade,cotacao_usada,valor_total,moeda_liquidacao,valor_moeda,cambio_para_usd,operador_id,status,data_hora").gte("data_hora", start_iso).lt("data_hora", end_iso).order("data_hora", desc=False).execute()
            for row in cast(List[Dict[str, Any]], t_resp.data or []):
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                op = str(row.get("operador_id") or "")
                ts = str(row.get("data_hora") or "")
                is_guided_duplicate = False
                try:
                    t_time = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                    for gt_meta in gt_timestamps:
                        if gt_meta["op"] == op:
                            gt_time = _dt.fromisoformat(gt_meta["ts"].replace("Z", "+00:00"))
                            if abs((t_time - gt_time).total_seconds()) <= 10:
                                is_guided_duplicate = True
                                break
                except Exception as exc:
                    logger.warning("Falha ao comparar timestamps de extrato guiado: %s", exc)
                if not is_guided_duplicate:
                    result.append({"source": "transacoes", "id": row.get("id"), "tipo_operacao": row.get("tipo_operacao"), "peso": row.get("quantidade"), "preco_usd": row.get("cotacao_usada"), "total_usd": row.get("valor_total"), "total_pago_usd": row.get("valor_total"), "diferenca_usd": "0", "moeda": row.get("moeda_liquidacao"), "valor_moeda": row.get("valor_moeda"), "cambio_para_usd": row.get("cambio_para_usd"), "operador_id": str(row.get("operador_id") or ""), "status": row.get("status"), "criado_em": ts, "pagamentos": []})
        except Exception as exc:
            logger.warning("Falha ao carregar extrato de transacoes legadas: %s", exc)
        result.sort(key=lambda r: str(r.get("criado_em") or ""))
        return result

    def get_risk_alerts(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        try:
            response = self.client.table("logs").select("data_hora,remetente,nivel,contexto,erro").eq("nivel", "warning").gte("data_hora", start_iso).lt("data_hora", end_iso).execute()
            alerts: List[Dict[str, Any]] = []
            for row in cast(List[Dict[str, Any]], response.data or []):
                contexto = row.get("contexto")
                if isinstance(contexto, dict) and cast(Dict[str, Any], contexto).get("tipo") == "diferenca_alta":
                    contexto_dict = cast(Dict[str, Any], contexto)
                    alerts.append({"data_hora": row.get("data_hora"), "remetente": row.get("remetente"), "tipo_operacao": contexto_dict.get("tipo_operacao"), "limite_usd": contexto_dict.get("limite_usd"), "diferenca_usd": contexto_dict.get("diferenca_usd"), "erro": row.get("erro")})
            return alerts
        except Exception as exc:
            logger.warning("Falha ao carregar alertas de risco: %s", exc)
            return []

    def get_top_divergences(self, start_iso: str, end_iso: str, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            response = self.client.table("gold_transactions").select("*").gte("criado_em", start_iso).lt("criado_em", end_iso).execute()
            rows = [row for row in cast(List[Dict[str, Any]], response.data or []) if str(row.get("status") or "registrada").lower() != "cancelada"]
            rows.sort(key=lambda r: abs(_safe_decimal_from_row(r, "diferenca_usd")), reverse=True)
            return rows[: max(limit, 1)]
        except Exception as exc:
            logger.warning("Falha ao carregar top divergencias: %s", exc)
            return []

    def get_gold_operation_audit(self, operation_id: int) -> Optional[Dict[str, Any]]:
        try:
            header_response = self.client.table("gold_transactions").select("*").eq("id", operation_id).limit(1).execute()
            header_rows = cast(List[Dict[str, Any]], header_response.data or [])
            if not header_rows:
                return None
            payments_response = self.client.table("gold_payments").select("*").eq("gold_transaction_id", operation_id).order("id", desc=False).execute()
            consumptions_response = self.client.table("gold_inventory_consumptions").select("id,sale_transaction_id,lot_id,consumed_grams,unit_cost_usd,consumed_cost_usd,created_at_sale,metadata").eq("sale_transaction_id", operation_id).order("id", desc=False).execute()
            return {"operation": header_rows[0], "payments": cast(List[Dict[str, Any]], payments_response.data or []), "inventory_consumptions": cast(List[Dict[str, Any]], consumptions_response.data or [])}
        except Exception as exc:
            logger.warning("Falha ao montar auditoria da operacao %s: %s", operation_id, exc)
            return None