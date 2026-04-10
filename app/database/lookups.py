from datetime import datetime, timezone

from .common import Decimal, Dict, List, Optional, _safe_decimal_from_row, cast, logger


class LookupMixin:
    def get_ativo_by_nome(self, nome: str) -> Optional[Dict[str, object]]:
        response = self.client.table("ativos").select("id,nome,tipo").ilike("nome", nome).limit(1).execute()
        data = cast(List[Dict[str, object]], response.data or [])
        if data:
            return data[0]
        if nome.strip().lower() == "ouro":
            fallback = self.client.table("ativos").select("id,nome,tipo").ilike("nome", "%ouro%").limit(1).execute()
            fallback_data = cast(List[Dict[str, object]], fallback.data or [])
            return fallback_data[0] if fallback_data else None
        return None

    def get_ativo_by_id(self, ativo_id: int) -> Optional[Dict[str, object]]:
        try:
            response = self.client.table("ativos").select("id,nome,tipo").eq("id", ativo_id).limit(1).execute()
            data = cast(List[Dict[str, object]], response.data or [])
            return data[0] if data else None
        except Exception as exc:
            logger.warning("Falha ao buscar ativo por id %s: %s", ativo_id, exc)
            return None

    def get_usuario_by_telefone(self, telefone: str) -> Optional[Dict[str, object]]:
        response = self.client.table("usuarios").select("id,nome,telefone,tipo_usuario,ativo").eq("telefone", telefone).eq("ativo", True).limit(1).execute()
        data = cast(List[Dict[str, object]], response.data or [])
        return data[0] if data else None

    def update_usuario_nome(self, telefone: str, nome: str) -> Optional[Dict[str, object]]:
        response = self.client.table("usuarios").update({"nome": nome}).eq("telefone", telefone).eq("ativo", True).execute()
        data = cast(List[Dict[str, object]], response.data or [])
        self._invalidate_runtime_cache(self._usuario_web_auth_cache_key(telefone))
        return data[0] if data else None

    def get_usuario_web_auth(self, telefone: str) -> Optional[Dict[str, object]]:
        cache_key = self._usuario_web_auth_cache_key(telefone)
        cached = self._get_local_runtime_cache(cache_key)
        if cached is not None:
            return cast(Optional[Dict[str, object]], cached)
        try:
            response = self.client.table("usuarios").select("*").eq("telefone", telefone).eq("ativo", True).limit(1).execute()
            data = cast(List[Dict[str, object]], response.data or [])
            enriched = dict(data[0]) if data else None
            if enriched is not None:
                schema_ready = "web_pin_hash" in enriched and "web_pin_updated_em" in enriched
                enriched.setdefault("web_pin_hash", None)
                enriched.setdefault("web_pin_updated_em", None)
                enriched["web_pin_schema_ready"] = schema_ready
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = schema_ready
                if schema_ready:
                    return cast(Optional[Dict[str, object]], self._set_local_runtime_cache(cache_key, enriched))
            return enriched
        except Exception as exc:
            if self._is_missing_usuario_web_pin_schema_error(exc):
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = False
            else:
                logger.warning("Falha ao carregar autenticacao web do usuario %s; usando fallback: %s", telefone, exc)
            usuario = self.get_usuario_by_telefone(telefone)
            if not usuario:
                return None
            fallback = dict(usuario)
            fallback["web_pin_hash"] = None
            fallback["web_pin_updated_em"] = None
            fallback["web_pin_schema_ready"] = False
            return fallback

    def get_last_cambio_para_usd(self, moeda: str) -> Optional[Decimal]:
        moeda_up = moeda.upper()
        if moeda_up == "USD":
            return Decimal("1")
        snapshot = self.get_last_cambio_para_usd_map([moeda_up])
        return snapshot.get(moeda_up)

    def get_last_cambio_para_usd_map(self, moedas: List[str]) -> Dict[str, Decimal]:
        requested = [str(moeda or "").upper() for moeda in moedas if str(moeda or "").strip()]
        targets = sorted({moeda for moeda in requested if moeda != "USD"})
        result: Dict[str, Decimal] = {"USD": Decimal("1")}
        if not targets:
            return result
        try:
            legacy_response = self.client.table("transacoes").select("moeda_liquidacao,cambio_para_usd,data_hora").in_("moeda_liquidacao", targets).not_.is_("cambio_para_usd", "null").order("data_hora", desc=True).execute()
            for row in cast(List[Dict[str, object]], legacy_response.data or []):
                moeda = str(row.get("moeda_liquidacao") or "").upper()
                if moeda in result:
                    continue
                val = _safe_decimal_from_row(cast(Dict[str, object], row), "cambio_para_usd")
                if val > 0:
                    result[moeda] = val
            pending = [moeda for moeda in targets if moeda not in result]
            if pending:
                gp_resp = self.client.table("gold_payments").select("moeda,cambio_para_usd,id").in_("moeda", pending).not_.is_("cambio_para_usd", "null").order("id", desc=True).execute()
                for row in cast(List[Dict[str, object]], gp_resp.data or []):
                    moeda = str(row.get("moeda") or "").upper()
                    if moeda in result:
                        continue
                    val = _safe_decimal_from_row(cast(Dict[str, object], row), "cambio_para_usd")
                    if val > 0:
                        result[moeda] = val
            return result
        except Exception as exc:
            if self._is_missing_fx_rates_schema_error(exc):
                type(self)._FX_RATES_SCHEMA_READY = False
            else:
                logger.warning("Falha ao buscar mapa de cambio para USD; retornando snapshot parcial: %s", exc)
            return {moeda: valor for moeda, valor in result.items() if moeda in {"USD", *targets}}

    def insert_taxa_diaria(self, ativo_id: int, preco: Decimal, admin_id: str) -> Dict[str, object]:
        payload = {
            "ativo_id": ativo_id,
            "preco_compra": str(preco),
            "preco_venda": str(preco),
            "admin_id": admin_id,
            "data_atualizacao": datetime.now(timezone.utc).isoformat(),
        }
        response = self.client.table("taxas_diarias").insert(payload).execute()
        data = cast(List[Dict[str, object]], response.data or [])
        if not data:
            from .common import DatabaseError
            raise DatabaseError("Falha ao inserir taxa diária.")
        return data[0]

    def get_taxa_atual(self, ativo_id: int) -> Optional[Dict[str, object]]:
        response = self.client.table("taxas_diarias").select("id,ativo_id,preco_compra,preco_venda,data_atualizacao").eq("ativo_id", ativo_id).order("data_atualizacao", desc=True).limit(1).execute()
        data = cast(List[Dict[str, object]], response.data or [])
        return data[0] if data else None