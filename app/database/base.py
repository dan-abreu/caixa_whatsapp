from .common import Any, DatabaseError, Dict, List, Optional, cast, datetime, get_shared_cache, importlib, logger, monotonic, os, timezone


class DatabaseClientBase:
    _USUARIOS_WEB_PIN_SCHEMA_READY: Optional[bool] = None
    _CAIXAS_READY: Optional[bool] = None
    _FX_RATES_SCHEMA_READY: Optional[bool] = None
    _GOLD_PENDING_CLOSURE_SCHEMA_READY: Optional[bool] = None
    _RUNTIME_CACHE_TTL_SECONDS = float(os.getenv("DATABASE_RUNTIME_CACHE_TTL_SECONDS", "15"))
    _RUNTIME_CACHE: Dict[str, Any] = {}

    @classmethod
    def _shared_cache_key(cls, key: str) -> str:
        return f"database:{key}"

    @classmethod
    def _cliente_account_snapshot_cache_key(cls, cliente_id: int) -> str:
        return f"cliente_account_snapshot:{cliente_id}"

    @classmethod
    def _clientes_with_balances_cache_key(cls, limit: int, search: Optional[str] = None) -> str:
        normalized_search = str(search or "").strip().lower() or "default"
        return f"clientes_with_balances:{normalized_search}:{limit}"

    @classmethod
    def _cliente_search_cache_key(cls, query: str, limit: int) -> str:
        normalized_query = str(query or "").strip().lower()
        return f"clientes_search:{normalized_query}:{limit}"

    @classmethod
    def _supplier_account_snapshot_cache_key(cls, fornecedor_id: int) -> str:
        return f"supplier_account_snapshot:{fornecedor_id}"

    @classmethod
    def _fornecedores_with_balances_cache_key(cls, limit: int, search: Optional[str] = None) -> str:
        normalized_search = str(search or "").strip().lower() or "default"
        return f"fornecedores_with_balances:{normalized_search}:{limit}"

    @classmethod
    def _supplier_search_cache_key(cls, query: str, limit: int) -> str:
        normalized_query = str(query or "").strip().lower()
        return f"fornecedores_search:{normalized_query}:{limit}"

    @classmethod
    def _bank_accounts_cache_key(cls, owner_kind: str, owner_id: Optional[int], currency: Optional[str] = None) -> str:
        normalized_owner = str(owner_kind or "").strip().lower() or "unknown"
        normalized_owner_id = int(owner_id or 0) if owner_id is not None else 0
        normalized_currency = str(currency or "").strip().upper() or "ALL"
        return f"saved_bank_accounts:{normalized_owner}:{normalized_owner_id}:{normalized_currency}"

    @classmethod
    def _usuario_web_auth_cache_key(cls, telefone: str) -> str:
        return f"usuario_web_auth:{str(telefone or '').strip()}"

    @classmethod
    def _gold_inventory_status_cache_key(cls, open_only: bool) -> str:
        return f"gold_inventory_status:{'open' if open_only else 'all'}"

    @classmethod
    def _invalidate_cliente_account_snapshot_cache(cls, cliente_id: int) -> None:
        if cliente_id <= 0:
            return
        cls._invalidate_runtime_cache(cls._cliente_account_snapshot_cache_key(cliente_id))

    @classmethod
    def _invalidate_client_list_cache(cls) -> None:
        keys_to_clear = [
            key
            for key in list(cls._RUNTIME_CACHE.keys())
            if str(key).startswith("clientes_with_balances:") or str(key).startswith("clientes_search:")
        ]
        if not keys_to_clear:
            return
        cls._invalidate_runtime_cache(*keys_to_clear)

    @classmethod
    def _invalidate_supplier_account_snapshot_cache(cls, fornecedor_id: int) -> None:
        if fornecedor_id <= 0:
            return
        cls._invalidate_runtime_cache(cls._supplier_account_snapshot_cache_key(fornecedor_id))

    @classmethod
    def _invalidate_supplier_list_cache(cls) -> None:
        keys_to_clear = [
            key
            for key in list(cls._RUNTIME_CACHE.keys())
            if str(key).startswith("fornecedores_with_balances:") or str(key).startswith("fornecedores_search:")
        ]
        if not keys_to_clear:
            return
        cls._invalidate_runtime_cache(*keys_to_clear)

    @classmethod
    def _invalidate_saved_bank_accounts_cache(cls) -> None:
        keys_to_clear = [key for key in list(cls._RUNTIME_CACHE.keys()) if str(key).startswith("saved_bank_accounts:")]
        if not keys_to_clear:
            return
        cls._invalidate_runtime_cache(*keys_to_clear)

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

    @classmethod
    def _get_runtime_cache(cls, key: str) -> Optional[Any]:
        cached = cls._RUNTIME_CACHE.get(key)
        if not cached:
            shared_cache = get_shared_cache()
            if shared_cache is None:
                return None
            shared_value = shared_cache.get_json(cls._shared_cache_key(key))
            if shared_value is None:
                return None
            cls._RUNTIME_CACHE[key] = (monotonic() + cls._RUNTIME_CACHE_TTL_SECONDS, shared_value)
            return shared_value
        expires_at = float(cached[0])
        if expires_at <= monotonic():
            cls._RUNTIME_CACHE.pop(key, None)
            shared_cache = get_shared_cache()
            if shared_cache is None:
                return None
            shared_value = shared_cache.get_json(cls._shared_cache_key(key))
            if shared_value is None:
                return None
            cls._RUNTIME_CACHE[key] = (monotonic() + cls._RUNTIME_CACHE_TTL_SECONDS, shared_value)
            return shared_value
        return cached[1]

    @classmethod
    def _get_local_runtime_cache(cls, key: str) -> Optional[Any]:
        cached = cls._RUNTIME_CACHE.get(key)
        if not cached:
            return None
        expires_at = float(cached[0])
        if expires_at <= monotonic():
            cls._RUNTIME_CACHE.pop(key, None)
            return None
        return cached[1]

    @classmethod
    def _set_runtime_cache(cls, key: str, value: Any) -> Any:
        cls._RUNTIME_CACHE[key] = (monotonic() + cls._RUNTIME_CACHE_TTL_SECONDS, value)
        shared_cache = get_shared_cache()
        if shared_cache is not None:
            shared_cache.set_json(cls._shared_cache_key(key), value, cls._RUNTIME_CACHE_TTL_SECONDS)
        return value

    @classmethod
    def _set_local_runtime_cache(cls, key: str, value: Any) -> Any:
        cls._RUNTIME_CACHE[key] = (monotonic() + cls._RUNTIME_CACHE_TTL_SECONDS, value)
        return value

    @classmethod
    def _invalidate_runtime_cache(cls, *keys: str) -> None:
        if not keys:
            cls._RUNTIME_CACHE.clear()
            return
        for key in keys:
            cls._RUNTIME_CACHE.pop(key, None)
        shared_cache = get_shared_cache()
        if shared_cache is not None:
            shared_cache.delete(*(cls._shared_cache_key(key) for key in keys))

    def _is_missing_usuario_web_pin_schema_error(self, exc: Exception) -> bool:
        message = str(exc or "")
        if "42703" not in message and "does not exist" not in message:
            return False
        return "usuarios.web_pin_hash" in message or "usuarios.web_pin_updated_em" in message

    def _is_missing_fx_rates_schema_error(self, exc: Exception) -> bool:
        message = str(exc or "")
        lowered = message.lower()
        if "fx_rates" not in lowered:
            return False
        return "404" in lowered or "does not exist" in lowered or "pgrst" in lowered or "42p01" in lowered

    def _safe_record_fx_rate(self, base_currency: str, quote_currency: str, rate, source: str = "app_operation") -> None:
        if base_currency.upper() == quote_currency.upper():
            return
        if type(self)._FX_RATES_SCHEMA_READY is False:
            return
        try:
            payload: Dict[str, Any] = {
                "base_currency": base_currency.upper(),
                "quote_currency": quote_currency.upper(),
                "rate": str(rate),
                "source": source,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            self.client.table("fx_rates").insert(payload).execute()
            type(self)._FX_RATES_SCHEMA_READY = True
        except Exception as exc:
            if self._is_missing_fx_rates_schema_error(exc):
                type(self)._FX_RATES_SCHEMA_READY = False
                return
            logger.warning("Falha ao registrar fx_rate %s/%s: %s", base_currency, quote_currency, exc)

    def _safe_record_journal_entry(
        self,
        reference_table: str,
        reference_id: Optional[int],
        description: str,
        source_message_id: Optional[str],
        created_by: Optional[str],
        metadata: Dict[str, Any],
        lines: List[Dict[str, Any]],
    ) -> None:
        if not lines:
            return
        try:
            header_payload: Dict[str, Any] = {
                "reference_table": reference_table,
                "reference_id": reference_id,
                "description": description,
                "source_message_id": source_message_id,
                "created_by": created_by,
                "metadata": metadata,
                "posted_at": datetime.now(timezone.utc).isoformat(),
            }
            header_resp = self.client.table("accounting_journal_entries").insert(header_payload).execute()
            header_data = cast(List[Dict[str, Any]], header_resp.data or [])
            if not header_data:
                return
            entry_id = header_data[0].get("id")
            if not entry_id:
                return
            rows: List[Dict[str, Any]] = []
            for line in lines:
                rows.append(
                    {
                        "journal_entry_id": entry_id,
                        "account_code": line.get("account_code"),
                        "currency_code": line.get("currency_code", "USD"),
                        "debit": str(line.get("debit", "0")),
                        "credit": str(line.get("credit", "0")),
                        "commodity_symbol": line.get("commodity_symbol"),
                        "quantity": str(line["quantity"]) if line.get("quantity") is not None else None,
                    }
                )
            self.client.table("accounting_journal_lines").insert(rows).execute()
        except Exception as exc:
            logger.warning("Falha ao registrar lancamento contabil %s:%s: %s", reference_table, reference_id, exc)
            return