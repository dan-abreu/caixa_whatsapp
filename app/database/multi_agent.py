from .common import Any, Decimal, Dict, List, Optional, _safe_decimal_from_row, cast, datetime, logger, sqrt, timedelta, timezone


class MultiAgentMixin:
    def save_multi_agent_run(self, objective: str, request_payload: Dict[str, Any], response_payload: Dict[str, Any], operation_id: Optional[int] = None, operation_kind: Optional[str] = None, source_message_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        payload = {"objective": objective, "operation_id": operation_id, "operation_kind": operation_kind, "source_message_id": source_message_id, "request_payload": request_payload, "response_payload": response_payload, "criado_em": datetime.now(timezone.utc).isoformat()}
        try:
            response = self.client.table("multi_agent_runs").insert(payload).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception as exc:
            logger.warning("Falha ao persistir multi_agent_run; usando fallback em logs: %s", exc)
            self.insert_log("info", mensagem_recebida="MULTI_AGENT_RUN", resposta_enviada=response_payload.get("summary"), contexto={"objective": objective, "operation_id": operation_id, "operation_kind": operation_kind, "source_message_id": source_message_id, "request": request_payload, "response": response_payload})
            return None

    def get_recent_multi_agent_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        safe_limit = max(limit, 1)
        try:
            response = self.client.table("multi_agent_runs").select("id,objective,operation_id,operation_kind,source_message_id,response_payload,criado_em").order("criado_em", desc=True).limit(safe_limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception as exc:
            logger.warning("Falha ao carregar multi_agent_runs recentes; tentando fallback em logs: %s", exc)
            try:
                response = self.client.table("logs").select("id,data_hora,contexto,resposta_enviada").eq("mensagem_recebida", "MULTI_AGENT_RUN").order("data_hora", desc=True).limit(safe_limit).execute()
                items: List[Dict[str, Any]] = []
                for row in cast(List[Dict[str, Any]], response.data or []):
                    contexto = row.get("contexto")
                    contexto_dict = cast(Dict[str, Any], contexto) if isinstance(contexto, dict) else {}
                    items.append({"id": row.get("id"), "objective": contexto_dict.get("objective"), "operation_id": contexto_dict.get("operation_id"), "operation_kind": contexto_dict.get("operation_kind"), "source_message_id": contexto_dict.get("source_message_id"), "response_payload": contexto_dict.get("response"), "criado_em": row.get("data_hora")})
                return items
            except Exception as fallback_exc:
                logger.warning("Falha ao carregar fallback de multi_agent_runs em logs: %s", fallback_exc)
                return []

    def get_multi_agent_runs_range(self, start_iso: str, end_iso: str, limit: int = 500) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(limit, 2000))
        try:
            response = self.client.table("multi_agent_runs").select("id,objective,operation_id,operation_kind,source_message_id,response_payload,criado_em").gte("criado_em", start_iso).lt("criado_em", end_iso).order("criado_em", desc=True).limit(safe_limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception as exc:
            logger.warning("Falha ao carregar multi_agent_runs por periodo; tentando fallback em logs: %s", exc)
            try:
                response = self.client.table("logs").select("id,data_hora,contexto,resposta_enviada").eq("mensagem_recebida", "MULTI_AGENT_RUN").gte("data_hora", start_iso).lt("data_hora", end_iso).order("data_hora", desc=True).limit(safe_limit).execute()
                items: List[Dict[str, Any]] = []
                for row in cast(List[Dict[str, Any]], response.data or []):
                    contexto = row.get("contexto")
                    contexto_dict = cast(Dict[str, Any], contexto) if isinstance(contexto, dict) else {}
                    items.append({"id": row.get("id"), "objective": contexto_dict.get("objective"), "operation_id": contexto_dict.get("operation_id"), "operation_kind": contexto_dict.get("operation_kind"), "source_message_id": contexto_dict.get("source_message_id"), "response_payload": contexto_dict.get("response"), "criado_em": row.get("data_hora")})
                return items
            except Exception as fallback_exc:
                logger.warning("Falha ao carregar fallback historico de multi_agent_runs em logs: %s", fallback_exc)
                return []

    def get_transaction_learning_snapshot(self, lookback_days: int = 45) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=max(lookback_days, 1))).isoformat()
        def _empty() -> Dict[str, Any]:
            return {"lookback_days": max(lookback_days, 1), "total_samples": 0, "operations": {}, "currency_mix": {}, "operator_profiles": {}}
        try:
            response = self.client.table("gold_transactions").select("*").gte("criado_em", start).execute()
            rows = [row for row in cast(List[Dict[str, Any]], response.data or []) if str(row.get("status") or "registrada").lower() != "cancelada"]
        except Exception as exc:
            logger.warning("Falha ao carregar snapshot de aprendizado transacional: %s", exc)
            return _empty()
        if not rows:
            return _empty()
        op_acc: Dict[str, Dict[str, Decimal]] = {}
        operator_acc: Dict[str, Dict[str, Decimal]] = {}
        ids: List[int] = []
        for row in rows:
            if row.get("id") is not None:
                try:
                    ids.append(int(str(row.get("id"))))
                except (TypeError, ValueError):
                    pass
            op = str(row.get("tipo_operacao", "desconhecida")).lower()
            peso = _safe_decimal_from_row(row, "peso")
            total_usd = _safe_decimal_from_row(row, "total_usd")
            abs_diff = abs(_safe_decimal_from_row(row, "diferenca_usd"))
            operador = str(row.get("operador_id", "desconhecido"))
            op_acc.setdefault(op, {"count": Decimal("0"), "peso_sum": Decimal("0"), "peso_sq_sum": Decimal("0"), "total_sum": Decimal("0"), "total_sq_sum": Decimal("0"), "diff_abs_sum": Decimal("0"), "diff_abs_sq_sum": Decimal("0")})
            op_acc[op]["count"] += Decimal("1")
            op_acc[op]["peso_sum"] += peso
            op_acc[op]["peso_sq_sum"] += peso * peso
            op_acc[op]["total_sum"] += total_usd
            op_acc[op]["total_sq_sum"] += total_usd * total_usd
            op_acc[op]["diff_abs_sum"] += abs_diff
            op_acc[op]["diff_abs_sq_sum"] += abs_diff * abs_diff
            operator_acc.setdefault(operador, {"count": Decimal("0"), "diff_abs_sum": Decimal("0"), "total_sum": Decimal("0")})
            operator_acc[operador]["count"] += Decimal("1")
            operator_acc[operador]["diff_abs_sum"] += abs_diff
            operator_acc[operador]["total_sum"] += total_usd
        currency_mix: Dict[str, int] = {}
        if ids:
            try:
                pay_resp = self.client.table("gold_payments").select("gold_transaction_id,moeda").in_("gold_transaction_id", ids).execute()
                for pay in cast(List[Dict[str, Any]], pay_resp.data or []):
                    moeda = str(pay.get("moeda", "USD")).upper()
                    currency_mix[moeda] = currency_mix.get(moeda, 0) + 1
            except Exception as exc:
                logger.warning("Falha ao carregar currency mix do snapshot transacional: %s", exc)
        def _mean_std(sum_v: Decimal, sq_sum_v: Decimal, n: Decimal) -> Dict[str, str]:
            if n <= 0:
                return {"mean": "0", "std": "0"}
            mean = sum_v / n
            variance = (sq_sum_v / n) - (mean * mean)
            if variance < 0:
                variance = Decimal("0")
            return {"mean": str(mean), "std": str(Decimal(str(sqrt(float(variance)))))}
        operations = {}
        for op, acc in op_acc.items():
            n = acc["count"]
            peso_stats = _mean_std(acc["peso_sum"], acc["peso_sq_sum"], n)
            total_stats = _mean_std(acc["total_sum"], acc["total_sq_sum"], n)
            diff_stats = _mean_std(acc["diff_abs_sum"], acc["diff_abs_sq_sum"], n)
            operations[op] = {"count": int(n), "peso_mean": peso_stats["mean"], "peso_std": peso_stats["std"], "total_usd_mean": total_stats["mean"], "total_usd_std": total_stats["std"], "abs_diff_usd_mean": diff_stats["mean"], "abs_diff_usd_std": diff_stats["std"]}
        operator_profiles = {operador: {"count": int(acc["count"]), "avg_abs_diff_usd": str(acc["diff_abs_sum"] / acc["count"]), "avg_total_usd": str(acc["total_sum"] / acc["count"])} for operador, acc in operator_acc.items() if acc["count"] > 0}
        return {"lookback_days": max(lookback_days, 1), "total_samples": len(rows), "operations": operations, "currency_mix": currency_mix, "operator_profiles": operator_profiles}

    def build_multi_agent_live_context(self, operation_id: Optional[int] = None) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
        context: Dict[str, Any] = {"daily_summary": self.get_daily_gold_summary(start, now.isoformat()), "daily_by_currency": self.get_gold_summary_by_currency(start, now.isoformat()), "saldo_caixa": self.get_saldo_caixa(), "risk_alerts": self.get_risk_alerts(start, now.isoformat()), "top_divergences": self.get_top_divergences(start, now.isoformat(), limit=3), "recent_runs": self.get_recent_multi_agent_runs(limit=3), "learning_snapshot": self.get_transaction_learning_snapshot(lookback_days=45)}
        if operation_id is not None:
            context["operation_audit"] = self.get_gold_operation_audit(operation_id)
        return context