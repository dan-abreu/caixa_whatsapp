import logging

from .common import Any, Decimal, Dict, List, Optional, _CLIENT_BALANCE_CURRENCIES, _empty_cliente_balance_snapshot, _safe_decimal, _safe_int, cast, datetime, timezone


logger = logging.getLogger("caixa_whatsapp")


def _safe_fornecedor_id(value: Any, *, context: str = "fornecedor.id") -> int:
    return _safe_int(value, context=context)


def _normalize_positive_fornecedor_ids(values: List[Any], *, context: str) -> List[int]:
    normalized_ids = {supplier_id for supplier_id in (_safe_fornecedor_id(value, context=context) for value in values) if supplier_id > 0}
    return sorted(normalized_ids)


def _aggregate_fornecedor_movements(movements: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    balances = _empty_cliente_balance_snapshot()
    for row in movements:
        moeda = str(row.get("moeda") or "").upper()
        if moeda not in balances:
            continue
        balances[moeda] += _safe_decimal(row.get("valor") or "0", context="fornecedor_movimentacoes.valor")
    return balances


def _aggregate_fornecedor_movements_by_supplier(movements: List[Dict[str, Any]]) -> Dict[int, Dict[str, Decimal]]:
    balances_by_supplier: Dict[int, Dict[str, Decimal]] = {}
    for row in movements:
        supplier_id = _safe_fornecedor_id(row.get("fornecedor_id"), context="fornecedor_movimentacoes.fornecedor_id")
        if supplier_id <= 0:
            continue
        balances = balances_by_supplier.setdefault(supplier_id, _empty_cliente_balance_snapshot())
        moeda = str(row.get("moeda") or "").upper()
        if moeda not in balances:
            continue
        balances[moeda] += _safe_decimal(row.get("valor") or "0", context="fornecedor_movimentacoes.valor")
    return balances_by_supplier


class SupplierAccountsMixin:
    def _fornecedor_fields(self) -> str:
        return "id,nome,apelido,telefone,documento,observacoes,ativo,criado_em,atualizado_em"

    def _base_fornecedor_select(self):
        return self.client.table("fornecedores").select(self._fornecedor_fields())

    def get_fornecedor_by_id(self, fornecedor_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = self._base_fornecedor_select().eq("id", fornecedor_id).eq("ativo", True).limit(1).execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            return rows[0] if rows else None
        except Exception as exc:
            logger.warning("Falha ao buscar fornecedor %s: %s", fornecedor_id, exc)
            return None

    def search_fornecedores(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return self.list_fornecedores(limit=limit)
        cache_key = self._supplier_search_cache_key(normalized_query, limit)
        cached = self._get_local_runtime_cache(cache_key)
        if cached is not None:
            return cast(List[Dict[str, Any]], cached)
        results: Dict[int, Dict[str, Any]] = {}
        filters = [("nome", f"%{normalized_query}%"), ("apelido", f"%{normalized_query}%"), ("telefone", f"%{normalized_query}%"), ("documento", f"%{normalized_query}%")]
        for field, value in filters:
            try:
                response = self._base_fornecedor_select().eq("ativo", True).ilike(field, value).limit(limit).execute()
                for row in cast(List[Dict[str, Any]], response.data or []):
                    supplier_id = _safe_fornecedor_id(row.get("id"), context=f"fornecedores.{field}.id")
                    if supplier_id > 0:
                        results[supplier_id] = dict(row)
            except Exception as exc:
                logger.warning("Falha ao buscar fornecedores por %s: %s", field, exc)
        ordered = sorted(results.values(), key=lambda item: (str(item.get("nome") or "").lower() != normalized_query.lower(), str(item.get("nome") or "").lower(), int(item.get("id") or 0)))
        return cast(List[Dict[str, Any]], self._set_local_runtime_cache(cache_key, ordered[:limit]))

    def list_fornecedores(self, limit: int = 40) -> List[Dict[str, Any]]:
        try:
            response = self._base_fornecedor_select().eq("ativo", True).order("atualizado_em", desc=True).limit(limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception as exc:
            logger.warning("Falha ao listar fornecedores: %s", exc)
            return []

    def _insert_fornecedor_movements(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        try:
            self.client.table("fornecedor_movimentacoes").insert(rows).execute()
        except Exception as exc:
            logger.warning("Falha ao inserir movimentos de fornecedor: %s", exc)

    def create_fornecedor(self, nome: str, telefone: Optional[str] = None, documento: Optional[str] = None, apelido: Optional[str] = None, observacoes: Optional[str] = None, opening_balances: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        normalized_name = str(nome or "").strip()
        if not normalized_name:
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {"nome": normalized_name, "telefone": str(telefone or "").strip() or None, "documento": str(documento or "").strip() or None, "apelido": str(apelido or "").strip() or None, "observacoes": str(observacoes or "").strip() or None, "ativo": True, "criado_em": now_iso, "atualizado_em": now_iso}
        try:
            response = self.client.table("fornecedores").insert(payload).execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            if not rows:
                return None
            fornecedor = dict(rows[0])
            supplier_id = _safe_fornecedor_id(fornecedor.get("id"), context="fornecedores.id")
            movement_rows: List[Dict[str, Any]] = []
            for moeda, raw_value in (opening_balances or {}).items():
                currency = str(moeda or "").upper()
                if currency not in _CLIENT_BALANCE_CURRENCIES:
                    continue
                value = _safe_decimal(raw_value, context=f"fornecedor.opening_balances.{currency}")
                if value != 0 and supplier_id > 0:
                    movement_rows.append({"fornecedor_id": supplier_id, "moeda": currency, "tipo_movimento": "abertura", "valor": str(value), "descricao": "Saldo inicial do fornecedor", "metadata": {"origem": "cadastro_fornecedor"}, "criado_em": now_iso})
            self._insert_fornecedor_movements(movement_rows)
            self._invalidate_supplier_list_cache()
            return fornecedor
        except Exception as exc:
            logger.warning("Falha ao criar fornecedor %s: %s", normalized_name, exc)
            return None

    def get_fornecedor_movements(self, fornecedor_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            response = self.client.table("fornecedor_movimentacoes").select("id,fornecedor_id,moeda,tipo_movimento,valor,descricao,metadata,criado_em").eq("fornecedor_id", fornecedor_id).order("criado_em", desc=True).limit(limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception as exc:
            logger.warning("Falha ao buscar movimentos do fornecedor %s: %s", fornecedor_id, exc)
            return []

    def get_fornecedor_balance_summaries(self, fornecedor_ids: List[int]) -> Dict[int, Dict[str, Decimal]]:
        normalized_ids = _normalize_positive_fornecedor_ids(fornecedor_ids, context="fornecedor_ids")
        if not normalized_ids:
            return {}
        try:
            response = self.client.table("fornecedor_movimentacoes").select("fornecedor_id,moeda,valor").in_("fornecedor_id", normalized_ids).execute()
            return _aggregate_fornecedor_movements_by_supplier(cast(List[Dict[str, Any]], response.data or []))
        except Exception as exc:
            logger.warning("Falha ao consolidar saldos dos fornecedores %s: %s", normalized_ids, exc)
            return {}

    def get_fornecedor_account_snapshot(self, fornecedor_id: int) -> Optional[Dict[str, Any]]:
        if fornecedor_id <= 0:
            return None
        cache_key = self._supplier_account_snapshot_cache_key(fornecedor_id)
        cached = self._get_runtime_cache(cache_key)
        if cached is not None:
            return cast(Dict[str, Any], cached)
        fornecedor = self.get_fornecedor_by_id(fornecedor_id)
        if not fornecedor:
            return None
        movement_rows = self.get_fornecedor_movements(fornecedor_id, limit=200)
        balances = _aggregate_fornecedor_movements(movement_rows)
        snapshot: Dict[str, Any] = {"fornecedor": fornecedor, "balances": {currency: str(value) for currency, value in balances.items()}, "movements": movement_rows[:50]}
        return cast(Dict[str, Any], self._set_runtime_cache(cache_key, snapshot))

    def list_fornecedores_with_balances(self, search: Optional[str] = None, limit: int = 40) -> List[Dict[str, Any]]:
        normalized_search = str(search or "").strip()
        cache_key = self._fornecedores_with_balances_cache_key(limit, normalized_search)
        cached = self._get_local_runtime_cache(cache_key) if normalized_search else self._get_runtime_cache(cache_key)
        if cached is not None:
            return cast(List[Dict[str, Any]], cached)
        fornecedores = self.search_fornecedores(normalized_search, limit=limit) if normalized_search else self.list_fornecedores(limit=limit)
        valid_fornecedores = []
        for fornecedor in fornecedores:
            supplier_id = _safe_fornecedor_id(fornecedor.get("id"), context="fornecedores_with_balances.id")
            if supplier_id > 0:
                valid_fornecedores.append((supplier_id, fornecedor))
        balances_by_supplier = self.get_fornecedor_balance_summaries([supplier_id for supplier_id, _fornecedor in valid_fornecedores])
        enriched: List[Dict[str, Any]] = []
        for supplier_id, fornecedor in valid_fornecedores:
            item = dict(fornecedor)
            item["balances"] = {currency: str(value) for currency, value in balances_by_supplier.get(supplier_id, _empty_cliente_balance_snapshot()).items()}
            enriched.append(item)
        return cast(List[Dict[str, Any]], self._set_local_runtime_cache(cache_key, enriched) if normalized_search else self._set_runtime_cache(cache_key, enriched))

    def record_fornecedor_manual_movement(self, fornecedor_id: int, moeda: str, tipo_movimento: str, valor: Decimal, descricao: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> bool:
        if fornecedor_id <= 0:
            return False
        currency = str(moeda or "").upper()
        movement_type = str(tipo_movimento or "").strip().lower()
        if currency not in _CLIENT_BALANCE_CURRENCIES or movement_type not in {"adiantamento", "divida", "ajuste_credito", "ajuste_debito"} or valor == 0:
            return False
        signed_value = valor if movement_type in {"adiantamento", "ajuste_credito"} else valor * Decimal("-1")
        self._insert_fornecedor_movements([{"fornecedor_id": fornecedor_id, "moeda": currency, "tipo_movimento": movement_type, "valor": str(signed_value), "descricao": str(descricao or "").strip() or None, "metadata": metadata or {}, "criado_em": datetime.now(timezone.utc).isoformat()}])
        self._invalidate_supplier_account_snapshot_cache(fornecedor_id)
        self._invalidate_supplier_list_cache()
        return True
