import os
import importlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, cast


class DatabaseError(Exception):
    pass


class DatabaseClient:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

        if not url or not key:
            raise DatabaseError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY devem estar configuradas.")

        try:
            supabase_module = importlib.import_module("supabase")
            create_client = getattr(supabase_module, "create_client")
        except ImportError as exc:
            raise DatabaseError("Biblioteca supabase não instalada. Instale: pip install supabase") from exc

        self.client: Any = create_client(url, key)

    def get_ativo_by_nome(self, nome: str) -> Optional[Dict[str, Any]]:
        response = (
            self.client.table("ativos")
            .select("id,nome,tipo")
            .ilike("nome", nome)
            .limit(1)
            .execute()
        )
        data = cast(List[Dict[str, Any]], response.data or [])
        return data[0] if data else None

    def get_usuario_by_telefone(self, telefone: str) -> Optional[Dict[str, Any]]:
        response = (
            self.client.table("usuarios")
            .select("id,nome,telefone,tipo_usuario,ativo")
            .eq("telefone", telefone)
            .eq("ativo", True)
            .limit(1)
            .execute()
        )
        data = cast(List[Dict[str, Any]], response.data or [])
        return data[0] if data else None

    def insert_taxa_diaria(self, ativo_id: int, preco: Decimal, admin_id: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ativo_id": ativo_id,
            "preco_compra": str(preco),
            "preco_venda": str(preco),
            "admin_id": admin_id,
            "data_atualizacao": datetime.now(timezone.utc).isoformat(),
        }
        response = self.client.table("taxas_diarias").insert(payload).execute()
        data = cast(List[Dict[str, Any]], response.data or [])
        if not data:
            raise DatabaseError("Falha ao inserir taxa diária.")
        return data[0]

    def get_taxa_atual(self, ativo_id: int) -> Optional[Dict[str, Any]]:
        response = (
            self.client.table("taxas_diarias")
            .select("id,ativo_id,preco_compra,preco_venda,data_atualizacao")
            .eq("ativo_id", ativo_id)
            .order("data_atualizacao", desc=True)
            .limit(1)
            .execute()
        )
        data = cast(List[Dict[str, Any]], response.data or [])
        return data[0] if data else None

    def insert_transacao(
        self,
        tipo_operacao: str,
        ativo_id: int,
        quantidade: Decimal,
        cotacao_usada: Decimal,
        valor_total: Decimal,
        operador_id: str,
        source_message_id: Optional[str] = None,
        status: str = "registrada",
        moeda_liquidacao: str = "USD",
        valor_moeda: Optional[Decimal] = None,
        cambio_para_usd: Decimal = Decimal("1.0"),
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "tipo_operacao": tipo_operacao,
            "ativo_id": ativo_id,
            "quantidade": str(quantidade),
            "cotacao_usada": str(cotacao_usada),
            "valor_total": str(valor_total),
            "operador_id": operador_id,
            "source_message_id": source_message_id,
            "status": status,
            "data_hora": datetime.now(timezone.utc).isoformat(),
            "moeda_liquidacao": moeda_liquidacao,
            "cambio_para_usd": str(cambio_para_usd),
        }
        if valor_moeda is not None:
            payload["valor_moeda"] = str(valor_moeda)
        try:
            response = self.client.table("transacoes").insert(payload).execute()
        except Exception:
            # Compatibilidade com schema antigo sem colunas novas.
            payload_fallback = dict(payload)
            for col in ("source_message_id", "moeda_liquidacao", "valor_moeda", "cambio_para_usd"):
                payload_fallback.pop(col, None)
            response = self.client.table("transacoes").insert(payload_fallback).execute()
        data = cast(List[Dict[str, Any]], response.data or [])
        if not data:
            raise DatabaseError("Falha ao inserir transação.")
        return data[0]

    def insert_log(
        self,
        nivel: str,
        remetente: Optional[str] = None,
        mensagem_recebida: Optional[str] = None,
        resposta_enviada: Optional[str] = None,
        contexto: Optional[Dict[str, Any]] = None,
        erro: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "nivel": nivel,
            "remetente": remetente,
            "mensagem_recebida": mensagem_recebida,
            "resposta_enviada": resposta_enviada,
            "contexto": contexto or {},
            "erro": erro,
            "data_hora": datetime.now(timezone.utc).isoformat(),
        }
        self.client.table("logs").insert(payload).execute()

    def get_processed_message(self, provider_message_id: str) -> Optional[Dict[str, Any]]:
        # Soft-fail if the new table is not migrated yet.
        try:
            response = (
                self.client.table("mensagens_processadas")
                .select("id,provider_message_id,resposta_payload,status_code")
                .eq("provider_message_id", provider_message_id)
                .limit(1)
                .execute()
            )
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception:
            return None

    def save_processed_message(
        self,
        provider_message_id: str,
        remetente: str,
        mensagem_recebida: str,
        resposta_payload: Dict[str, Any],
        status_code: int,
    ) -> None:
        # Soft-fail if the new table is not migrated yet.
        try:
            payload: Dict[str, Any] = {
                "provider_message_id": provider_message_id,
                "remetente": remetente,
                "mensagem_recebida": mensagem_recebida,
                "resposta_payload": resposta_payload,
                "status_code": status_code,
                "criado_em": datetime.now(timezone.utc).isoformat(),
            }

            existing = self.get_processed_message(provider_message_id)
            if existing:
                self.client.table("mensagens_processadas").update(payload).eq(
                    "provider_message_id", provider_message_id
                ).execute()
            else:
                self.client.table("mensagens_processadas").insert(payload).execute()
        except Exception:
            return

    def save_conversation_session(self, remetente: str, estado: str, contexto: Dict[str, Any]) -> None:
        # Soft-fail if the new table is not migrated yet.
        try:
            payload: Dict[str, Any] = {
                "remetente": remetente,
                "estado": estado,
                "contexto": contexto,
                "atualizado_em": datetime.now(timezone.utc).isoformat(),
            }

            response = (
                self.client.table("sessoes_conversa")
                .select("id")
                .eq("remetente", remetente)
                .limit(1)
                .execute()
            )
            data = cast(List[Dict[str, Any]], response.data or [])
            if data:
                self.client.table("sessoes_conversa").update(payload).eq("remetente", remetente).execute()
            else:
                self.client.table("sessoes_conversa").insert(payload).execute()
        except Exception:
            return

    def get_conversation_session(self, remetente: str) -> Optional[Dict[str, Any]]:
        # Soft-fail if the new table is not migrated yet.
        try:
            response = (
                self.client.table("sessoes_conversa")
                .select("id,remetente,estado,contexto,atualizado_em")
                .eq("remetente", remetente)
                .limit(1)
                .execute()
            )
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception:
            return None

    def clear_conversation_session(self, remetente: str) -> None:
        # Soft-fail if the new table is not migrated yet.
        try:
            self.client.table("sessoes_conversa").delete().eq("remetente", remetente).execute()
        except Exception:
            return

    def insert_gold_transaction(
        self,
        payload: Dict[str, Any],
        pagamentos: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        # Soft-fail if enterprise tables are not migrated yet.
        try:
            header_response = self.client.table("gold_transactions").insert(payload).execute()
            header_data = cast(List[Dict[str, Any]], header_response.data or [])
            if not header_data:
                return None

            header = header_data[0]
            transaction_id = header.get("id")
            if transaction_id is None:
                return header

            if pagamentos:
                rows: List[Dict[str, Any]] = []
                for pagamento in pagamentos:
                    rows.append(
                        {
                            "gold_transaction_id": transaction_id,
                            "moeda": pagamento.get("moeda"),
                            "valor_moeda": pagamento.get("valor_moeda"),
                            "cambio_para_usd": pagamento.get("cambio_para_usd"),
                            "valor_usd": pagamento.get("valor_usd"),
                            "forma_pagamento": pagamento.get("forma_pagamento"),
                            "criado_em": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                self.client.table("gold_payments").insert(rows).execute()

            return header
        except Exception:
            return None

    def get_daily_gold_summary(self, start_iso: str, end_iso: str) -> Dict[str, Any]:
        """Count operations from BOTH transacoes (simple) and gold_transactions (guided) tables."""
        try:
            # Count simple transacoes (quick flow)
            resp_t = (
                self.client.table("transacoes")
                .select("id,valor_total")
                .gte("data_hora", start_iso)
                .lt("data_hora", end_iso)
                .execute()
            )
            t_rows = cast(List[Dict[str, Any]], resp_t.data or [])
            t_ops = len(t_rows)
            t_usd = sum((Decimal(str(r.get("valor_total", 0))) for r in t_rows), Decimal("0"))

            # Count enterprise gold_transactions (guided flow)
            resp_g = (
                self.client.table("gold_transactions")
                .select("id,total_usd,total_pago_usd,diferenca_usd")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            g_rows = cast(List[Dict[str, Any]], resp_g.data or [])
            g_ops = len(g_rows)
            g_usd = sum((Decimal(str(r.get("total_usd", 0))) for r in g_rows), Decimal("0"))
            g_pago = sum((Decimal(str(r.get("total_pago_usd", 0))) for r in g_rows), Decimal("0"))
            g_diff = sum((Decimal(str(r.get("diferenca_usd", 0))) for r in g_rows), Decimal("0"))

            return {
                "total_operacoes": t_ops + g_ops,
                "total_usd": str(t_usd + g_usd),
                "total_pago_usd": str(g_pago),
                "total_diferenca_usd": str(g_diff),
            }
        except Exception:
            return {
                "total_operacoes": 0,
                "total_usd": "0",
                "total_pago_usd": "0",
                "total_diferenca_usd": "0",
            }

    def get_daily_gold_summary_by_operator(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        # Soft-fail if enterprise tables are not migrated yet.
        try:
            response = (
                self.client.table("gold_transactions")
                .select("operador_id,total_usd,total_pago_usd,diferenca_usd")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], response.data or [])
            grouped: Dict[str, Dict[str, Decimal]] = {}
            for row in rows:
                operador = str(row.get("operador_id", "desconhecido"))
                if operador not in grouped:
                    grouped[operador] = {
                        "total_usd": Decimal("0"),
                        "total_pago_usd": Decimal("0"),
                        "total_diferenca_usd": Decimal("0"),
                        "total_operacoes": Decimal("0"),
                    }
                grouped[operador]["total_usd"] += Decimal(str(row.get("total_usd", 0)))
                grouped[operador]["total_pago_usd"] += Decimal(str(row.get("total_pago_usd", 0)))
                grouped[operador]["total_diferenca_usd"] += Decimal(str(row.get("diferenca_usd", 0)))
                grouped[operador]["total_operacoes"] += Decimal("1")

            result: List[Dict[str, Any]] = []
            for operador, vals in grouped.items():
                result.append(
                    {
                        "operador_id": operador,
                        "total_operacoes": int(vals["total_operacoes"]),
                        "total_usd": str(vals["total_usd"]),
                        "total_pago_usd": str(vals["total_pago_usd"]),
                        "total_diferenca_usd": str(vals["total_diferenca_usd"]),
                    }
                )
            return result
        except Exception:
            return []

    def get_gold_summary_range(self, start_iso: str, end_iso: str) -> Dict[str, Any]:
        return self.get_daily_gold_summary(start_iso, end_iso)

    def get_gold_summary_by_currency(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        # Soft-fail if enterprise tables are not migrated yet.
        try:
            response = (
                self.client.table("gold_payments")
                .select("moeda,valor_moeda,valor_usd")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], response.data or [])
            grouped: Dict[str, Dict[str, Decimal]] = {}
            for row in rows:
                moeda = str(row.get("moeda", "UNK"))
                if moeda not in grouped:
                    grouped[moeda] = {
                        "total_valor_moeda": Decimal("0"),
                        "total_valor_usd": Decimal("0"),
                        "total_pagamentos": Decimal("0"),
                    }
                grouped[moeda]["total_valor_moeda"] += Decimal(str(row.get("valor_moeda", 0)))
                grouped[moeda]["total_valor_usd"] += Decimal(str(row.get("valor_usd", 0)))
                grouped[moeda]["total_pagamentos"] += Decimal("1")

            result: List[Dict[str, Any]] = []
            for moeda, vals in grouped.items():
                result.append(
                    {
                        "moeda": moeda,
                        "total_pagamentos": int(vals["total_pagamentos"]),
                        "total_valor_moeda": str(vals["total_valor_moeda"]),
                        "total_valor_usd": str(vals["total_valor_usd"]),
                    }
                )
            return result
        except Exception:
            return []

    def get_risk_alerts(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        # Works on existing logs table, independent of enterprise migrations.
        try:
            response = (
                self.client.table("logs")
                .select("data_hora,remetente,nivel,contexto,erro")
                .eq("nivel", "warning")
                .gte("data_hora", start_iso)
                .lt("data_hora", end_iso)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], response.data or [])
            alerts: List[Dict[str, Any]] = []
            for row in rows:
                contexto = row.get("contexto")
                if not isinstance(contexto, dict):
                    continue
                contexto_dict: Dict[str, Any] = cast(Dict[str, Any], contexto)
                if contexto_dict.get("tipo") != "diferenca_alta":
                    continue
                alerts.append(
                    {
                        "data_hora": row.get("data_hora"),
                        "remetente": row.get("remetente"),
                        "tipo_operacao": contexto_dict.get("tipo_operacao"),
                        "limite_usd": contexto_dict.get("limite_usd"),
                        "diferenca_usd": contexto_dict.get("diferenca_usd"),
                        "erro": row.get("erro"),
                    }
                )
            return alerts
        except Exception:
            return []

    def get_saldo_caixa(self) -> Dict[str, Any]:
        """Cumulative balance: gold grams in stock + per-currency cash totals.

        Direction rules (caixa = cash drawer perspective):
          compra de ouro  -> gold IN  (+g),  cash OUT (-moeda)
          venda de ouro   -> gold OUT (-g),  cash IN  (+moeda)

        Exchange-rate convention stored in cambio_para_usd:
          "1 USD = X moeda"  (SRD=38, BRL=5.20, EUR=0.877, USD=1.0)
        """
        try:
            ouro = self.get_ativo_by_nome("Ouro 24k")
            ouro_id = int(ouro["id"]) if ouro else None

            t_resp = (
                self.client.table("transacoes")
                .select("tipo_operacao,ativo_id,quantidade,moeda_liquidacao,valor_moeda,valor_total")
                .execute()
            )
            t_rows = cast(List[Dict[str, Any]], t_resp.data or [])

            gold_gramas: Decimal = Decimal("0")
            currency_map: Dict[str, Decimal] = {}

            for row in t_rows:
                tipo = str(row.get("tipo_operacao", ""))
                qty = Decimal(str(row.get("quantidade", "0")))
                aid = int(row.get("ativo_id", 0))

                if aid == ouro_id:
                    if tipo == "compra":
                        gold_gramas += qty
                    elif tipo in ("venda", "cambio"):
                        gold_gramas -= qty

                moeda = str(row.get("moeda_liquidacao") or "USD").upper()
                valor_m_raw = row.get("valor_moeda")
                if valor_m_raw is not None:
                    valor_m = Decimal(str(valor_m_raw))
                else:
                    moeda = "USD"
                    valor_m = Decimal(str(row.get("valor_total", "0")))

                currency_map.setdefault(moeda, Decimal("0"))
                if tipo == "venda":
                    currency_map[moeda] += valor_m
                elif tipo == "compra":
                    currency_map[moeda] -= valor_m

            gt_resp = (
                self.client.table("gold_transactions")
                .select("id,tipo_operacao,peso")
                .execute()
            )
            gt_rows = cast(List[Dict[str, Any]], gt_resp.data or [])
            gt_tipo_map: Dict[int, str] = {}
            for row in gt_rows:
                gid = int(row.get("id", 0))
                tipo = str(row.get("tipo_operacao", ""))
                gt_tipo_map[gid] = tipo
                peso = Decimal(str(row.get("peso", "0")))
                if tipo == "compra":
                    gold_gramas += peso
                elif tipo in ("venda", "cambio"):
                    gold_gramas -= peso

            gp_resp = (
                self.client.table("gold_payments")
                .select("gold_transaction_id,moeda,valor_moeda")
                .execute()
            )
            gp_rows = cast(List[Dict[str, Any]], gp_resp.data or [])
            for row in gp_rows:
                moeda = str(row.get("moeda", "USD")).upper()
                val = Decimal(str(row.get("valor_moeda", "0")))
                gid = int(row.get("gold_transaction_id", 0))
                tipo = gt_tipo_map.get(gid, "compra")

                currency_map.setdefault(moeda, Decimal("0"))
                if tipo == "venda":
                    currency_map[moeda] += val
                elif tipo == "compra":
                    currency_map[moeda] -= val

            return {
                "gold_gramas": str(gold_gramas),
                "moedas": {k: str(v) for k, v in sorted(currency_map.items())},
            }
        except Exception:
            return {"gold_gramas": "0", "moedas": {}}

    def get_top_divergences(self, start_iso: str, end_iso: str, limit: int = 10) -> List[Dict[str, Any]]:
        # Soft-fail if enterprise tables are not migrated yet.
        try:
            response = (
                self.client.table("gold_transactions")
                .select("id,criado_em,tipo_operacao,pessoa,operador_id,total_usd,total_pago_usd,diferenca_usd")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], response.data or [])
            rows.sort(key=lambda r: abs(Decimal(str(r.get("diferenca_usd", 0)))), reverse=True)
            return rows[: max(limit, 1)]
        except Exception:
            return []

    def get_gold_operation_audit(self, operation_id: int) -> Optional[Dict[str, Any]]:
        # Soft-fail if enterprise tables are not migrated yet.
        try:
            header_response = (
                self.client.table("gold_transactions")
                .select("*")
                .eq("id", operation_id)
                .limit(1)
                .execute()
            )
            header_rows = cast(List[Dict[str, Any]], header_response.data or [])
            if not header_rows:
                return None

            header = header_rows[0]
            payments_response = (
                self.client.table("gold_payments")
                .select("*")
                .eq("gold_transaction_id", operation_id)
                .order("id", desc=False)
                .execute()
            )
            payments = cast(List[Dict[str, Any]], payments_response.data or [])

            return {"operation": header, "payments": payments}
        except Exception:
            return None

    def save_multi_agent_run(
        self,
        objective: str,
        request_payload: Dict[str, Any],
        response_payload: Dict[str, Any],
        operation_id: Optional[int] = None,
        operation_kind: Optional[str] = None,
        source_message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "objective": objective,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "source_message_id": source_message_id,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "criado_em": datetime.now(timezone.utc).isoformat(),
        }

        try:
            response = self.client.table("multi_agent_runs").insert(payload).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception:
            pass

        self.insert_log(
            nivel="info",
            mensagem_recebida="MULTI_AGENT_RUN",
            resposta_enviada=response_payload.get("summary"),
            contexto={
                "objective": objective,
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "source_message_id": source_message_id,
                "request": request_payload,
                "response": response_payload,
            },
        )
        return None

    def get_recent_multi_agent_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        safe_limit = max(limit, 1)
        try:
            response = (
                self.client.table("multi_agent_runs")
                .select("id,objective,operation_id,operation_kind,source_message_id,response_payload,criado_em")
                .order("criado_em", desc=True)
                .limit(safe_limit)
                .execute()
            )
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception:
            try:
                response = (
                    self.client.table("logs")
                    .select("id,data_hora,contexto,resposta_enviada")
                    .eq("mensagem_recebida", "MULTI_AGENT_RUN")
                    .order("data_hora", desc=True)
                    .limit(safe_limit)
                    .execute()
                )
                rows = cast(List[Dict[str, Any]], response.data or [])
                items: List[Dict[str, Any]] = []
                for row in rows:
                    contexto = row.get("contexto")
                    contexto_dict = cast(Dict[str, Any], contexto) if isinstance(contexto, dict) else {}
                    items.append(
                        {
                            "id": row.get("id"),
                            "objective": contexto_dict.get("objective"),
                            "operation_id": contexto_dict.get("operation_id"),
                            "operation_kind": contexto_dict.get("operation_kind"),
                            "source_message_id": contexto_dict.get("source_message_id"),
                            "response_payload": contexto_dict.get("response"),
                            "criado_em": row.get("data_hora"),
                        }
                    )
                return items
            except Exception:
                return []

    def build_multi_agent_live_context(self, operation_id: Optional[int] = None) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
        end = now.isoformat()

        context: Dict[str, Any] = {
            "daily_summary": self.get_daily_gold_summary(start, end),
            "risk_alerts": self.get_risk_alerts(start, end),
            "top_divergences": self.get_top_divergences(start, end, limit=3),
            "recent_runs": self.get_recent_multi_agent_runs(limit=3),
        }

        if operation_id is not None:
            context["operation_audit"] = self.get_gold_operation_audit(operation_id)

        return context
