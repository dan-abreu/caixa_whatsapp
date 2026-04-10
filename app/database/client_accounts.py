import logging

from .common import Any, Decimal, Dict, List, Optional, _CLIENT_BALANCE_CURRENCIES, _aggregate_cliente_movements, _aggregate_cliente_movements_by_client, _empty_cliente_balance_snapshot, _hash_web_pin, _safe_decimal, _safe_int, _verify_web_pin, cast, datetime, timezone


logger = logging.getLogger("caixa_whatsapp")


def _safe_cliente_id(value: Any, *, context: str = "cliente.id") -> int:
    return _safe_int(value, context=context)


def _normalize_positive_cliente_ids(values: List[Any], *, context: str) -> List[int]:
    normalized_ids = {client_id for client_id in (_safe_cliente_id(value, context=context) for value in values) if client_id > 0}
    return sorted(normalized_ids)


class ClientAccountsMixin:
    def _cliente_fields(self) -> str:
        return "id,nome,apelido,telefone,documento,observacoes,ativo,criado_em,atualizado_em"

    def _base_cliente_select(self):
        return self.client.table("clientes").select(self._cliente_fields())

    def get_cliente_by_id(self, cliente_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = self._base_cliente_select().eq("id", cliente_id).eq("ativo", True).limit(1).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception as exc:
            logger.warning("Falha ao buscar cliente %s: %s", cliente_id, exc)
            return None

    def search_clientes(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return self.list_clientes(limit=limit)
        cache_key = self._cliente_search_cache_key(normalized_query, limit)
        cached = self._get_local_runtime_cache(cache_key)
        if cached is not None:
            return cast(List[Dict[str, Any]], cached)
        results: Dict[int, Dict[str, Any]] = {}
        filters = [("nome", f"%{normalized_query}%"), ("apelido", f"%{normalized_query}%"), ("telefone", f"%{normalized_query}%"), ("documento", f"%{normalized_query}%")]
        for field, value in filters:
            try:
                response = self._base_cliente_select().eq("ativo", True).ilike(field, value).limit(limit).execute()
                data = cast(List[Dict[str, Any]], response.data or [])
                for row in data:
                    client_id = _safe_cliente_id(row.get("id"), context=f"clientes.{field}.id")
                    if client_id > 0:
                        results[client_id] = dict(row)
            except Exception as exc:
                logger.warning("Falha ao buscar clientes por %s: %s", field, exc)
                continue
        ordered = sorted(results.values(), key=lambda item: (str(item.get("nome") or "").lower() != normalized_query.lower(), str(item.get("nome") or "").lower(), int(item.get("id") or 0)))
        return cast(List[Dict[str, Any]], self._set_local_runtime_cache(cache_key, ordered[:limit]))

    def list_clientes(self, limit: int = 40) -> List[Dict[str, Any]]:
        try:
            response = self._base_cliente_select().eq("ativo", True).order("atualizado_em", desc=True).limit(limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception as exc:
            logger.warning("Falha ao listar clientes: %s", exc)
            return []

    def _insert_cliente_movements(self, rows: List[Dict[str, Any]]) -> None:
        if rows:
            try:
                self.client.table("cliente_movimentacoes").insert(rows).execute()
            except Exception as exc:
                logger.warning("Falha ao inserir movimentos de cliente: %s", exc)
                return

    def create_cliente(self, nome: str, telefone: Optional[str] = None, documento: Optional[str] = None, apelido: Optional[str] = None, observacoes: Optional[str] = None, opening_balances: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        normalized_name = str(nome or "").strip()
        if not normalized_name:
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {"nome": normalized_name, "telefone": str(telefone or "").strip() or None, "documento": str(documento or "").strip() or None, "apelido": str(apelido or "").strip() or None, "observacoes": str(observacoes or "").strip() or None, "ativo": True, "criado_em": now_iso, "atualizado_em": now_iso}
        try:
            response = self.client.table("clientes").insert(payload).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            if not data:
                return None
            cliente = dict(data[0])
            cliente_id = int(cliente.get("id") or 0)
            if cliente_id > 0:
                movement_rows: List[Dict[str, Any]] = []
                for moeda, raw_value in (opening_balances or {}).items():
                    currency = str(moeda or "").upper()
                    if currency not in _CLIENT_BALANCE_CURRENCIES:
                        continue
                    value = _safe_decimal(raw_value, context=f"opening_balances.{currency}")
                    if value != 0:
                        movement_rows.append({"cliente_id": cliente_id, "gold_transaction_id": None, "moeda": currency, "tipo_movimento": "abertura", "valor": str(value), "descricao": "Saldo inicial de cadastro", "metadata": {"origem": "cadastro_cliente"}, "criado_em": now_iso})
                self._insert_cliente_movements(movement_rows)
            self._invalidate_client_list_cache()
            return cliente
        except Exception as exc:
            logger.warning("Falha ao criar cliente %s: %s", normalized_name, exc)
            return None

    def get_cliente_movements(self, cliente_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            response = self.client.table("cliente_movimentacoes").select("id,cliente_id,gold_transaction_id,moeda,tipo_movimento,valor,descricao,metadata,criado_em").eq("cliente_id", cliente_id).order("criado_em", desc=True).limit(limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception as exc:
            logger.warning("Falha ao buscar movimentos do cliente %s: %s", cliente_id, exc)
            return []

    def get_cliente_balance_summary(self, cliente_id: int) -> Dict[str, Decimal]:
        return _aggregate_cliente_movements(self.get_cliente_movements(cliente_id, limit=500))

    def get_cliente_balance_summaries(self, cliente_ids: List[int]) -> Dict[int, Dict[str, Decimal]]:
        normalized_ids = _normalize_positive_cliente_ids(cliente_ids, context="cliente_ids")
        if not normalized_ids:
            return {}
        try:
            response = self.client.table("cliente_movimentacoes").select("cliente_id,moeda,valor").in_("cliente_id", normalized_ids).execute()
            return _aggregate_cliente_movements_by_client(cast(List[Dict[str, Any]], response.data or []))
        except Exception as exc:
            logger.warning("Falha ao consolidar saldos dos clientes %s: %s", normalized_ids, exc)
            return {}

    def get_cliente_recent_transactions(self, cliente_id: int, limit: int = 25) -> List[Dict[str, Any]]:
        try:
            response = self.client.table("gold_transactions").select("id,tipo_operacao,pessoa,peso,preco_usd,total_usd,total_pago_usd,fechamento_gramas,fechamento_tipo,status,criado_em").eq("cliente_id", cliente_id).order("criado_em", desc=True).limit(limit).execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            return [row for row in rows if str(row.get("status") or "registrada").lower() != "cancelada"]
        except Exception as exc:
            logger.warning("Falha ao buscar transacoes recentes do cliente %s: %s", cliente_id, exc)
            return []

    def get_cliente_account_snapshot(self, cliente_id: int) -> Optional[Dict[str, Any]]:
        if cliente_id <= 0:
            return None
        cache_key = self._cliente_account_snapshot_cache_key(cliente_id)
        cached = self._get_runtime_cache(cache_key)
        if cached is not None:
            return cast(Dict[str, Any], cached)
        cliente = self.get_cliente_by_id(cliente_id)
        if not cliente:
            return None
        movement_rows = self.get_cliente_movements(cliente_id, limit=500)
        balances = _aggregate_cliente_movements(movement_rows)
        snapshot: Dict[str, Any] = {"cliente": cliente, "balances": {currency: str(value) for currency, value in balances.items()}, "recent_transactions": self.get_cliente_recent_transactions(cliente_id), "movements": movement_rows[:50]}
        return cast(Dict[str, Any], self._set_runtime_cache(cache_key, snapshot))

    def list_clientes_with_balances(self, search: Optional[str] = None, limit: int = 40) -> List[Dict[str, Any]]:
        normalized_search = str(search or "").strip()
        cache_key = self._clientes_with_balances_cache_key(limit, normalized_search)
        cached = self._get_local_runtime_cache(cache_key) if normalized_search else self._get_runtime_cache(cache_key)
        if cached is not None:
            return cast(List[Dict[str, Any]], cached)
        clientes = self.search_clientes(normalized_search, limit=limit) if normalized_search else self.list_clientes(limit=limit)
        valid_clientes = []
        for cliente in clientes:
            client_id = _safe_cliente_id(cliente.get("id"), context="clientes_with_balances.id")
            if client_id > 0:
                valid_clientes.append((client_id, cliente))
        client_ids = [client_id for client_id, _cliente in valid_clientes]
        balances_by_client = self.get_cliente_balance_summaries(client_ids)
        enriched: List[Dict[str, Any]] = []
        for client_id, cliente in valid_clientes:
            item = dict(cliente)
            item["balances"] = {currency: str(value) for currency, value in balances_by_client.get(client_id, _empty_cliente_balance_snapshot()).items()}
            enriched.append(item)
        return cast(List[Dict[str, Any]], self._set_local_runtime_cache(cache_key, enriched) if normalized_search else self._set_runtime_cache(cache_key, enriched))

    def record_cliente_operation_balance(self, cliente_id: int, gold_transaction_id: int, tipo_operacao: str, pending_grams: Decimal, pessoa: Optional[str] = None, reverse: bool = False) -> None:
        if cliente_id <= 0 or pending_grams <= 0:
            return
        signed_value = pending_grams if str(tipo_operacao).lower() == "compra" else (pending_grams * Decimal("-1"))
        if reverse:
            signed_value *= Decimal("-1")
        self._insert_cliente_movements([{"cliente_id": cliente_id, "gold_transaction_id": gold_transaction_id, "moeda": "XAU", "tipo_movimento": "operacao_pendente_estorno" if reverse else "operacao_pendente", "valor": str(signed_value), "descricao": f"{'Estorno do saldo em ouro da operacao' if reverse else 'Saldo em ouro da operacao'} GT-{gold_transaction_id}", "metadata": {"tipo_operacao": tipo_operacao, "pessoa": pessoa, "pending_grams": str(pending_grams)}, "criado_em": datetime.now(timezone.utc).isoformat()}])
        self._invalidate_cliente_account_snapshot_cache(cliente_id)
        self._invalidate_client_list_cache()

    def verify_usuario_web_pin(self, telefone: str, pin: str) -> Optional[Dict[str, Any]]:
        usuario = self.get_usuario_web_auth(telefone)
        if not usuario:
            return None
        stored_hash = usuario.get("web_pin_hash")
        if stored_hash:
            if not _verify_web_pin(pin, str(stored_hash)):
                return None
            verified = dict(usuario)
            verified["web_pin_bootstrap_required"] = False
            return verified
        digits = "".join(ch for ch in str(telefone) if ch.isdigit())
        bootstrap_pin = digits[-6:] if len(digits) >= 6 else digits
        if not bootstrap_pin or str(pin).strip() != bootstrap_pin:
            return None
        verified = dict(usuario)
        verified["web_pin_bootstrap_required"] = True
        return verified

    def set_usuario_web_pin(self, telefone: str, new_pin: str) -> Optional[Dict[str, Any]]:
        payload = {"web_pin_hash": _hash_web_pin(new_pin), "web_pin_updated_em": datetime.now(timezone.utc).isoformat()}
        if type(self)._USUARIOS_WEB_PIN_SCHEMA_READY is False:
            return {"telefone": telefone, "web_pin_schema_ready": False, "error": "usuarios web pin schema unavailable"}
        try:
            response = self.client.table("usuarios").update(payload).eq("telefone", telefone).eq("ativo", True).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = True
            self._invalidate_runtime_cache(self._usuario_web_auth_cache_key(telefone))
            if data:
                updated = dict(data[0])
                updated["web_pin_schema_ready"] = True
                return updated
            return {"telefone": telefone, "web_pin_schema_ready": True}
        except Exception as exc:
            if self._is_missing_usuario_web_pin_schema_error(exc):
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = False
            return {"telefone": telefone, "web_pin_schema_ready": False, "error": str(exc)}