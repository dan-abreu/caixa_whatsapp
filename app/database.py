import os
import hmac
import hashlib
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import sqrt
from time import monotonic
from typing import Any, Dict, List, Optional, cast

from app.shared_cache import get_shared_cache


class DatabaseError(Exception):
    pass


_WEB_PIN_HASH_PREFIX = "pbkdf2_sha256"
_WEB_PIN_HASH_ITERATIONS = 260000
_CLIENT_BALANCE_CURRENCIES = ("XAU", "USD", "EUR", "SRD", "BRL")


def _hash_web_pin(pin: str, salt: Optional[str] = None) -> str:
    normalized_pin = str(pin or "").strip()
    if not normalized_pin:
        raise ValueError("PIN vazio")
    salt_value = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized_pin.encode("utf-8"),
        salt_value.encode("utf-8"),
        _WEB_PIN_HASH_ITERATIONS,
    ).hex()
    return f"{_WEB_PIN_HASH_PREFIX}${_WEB_PIN_HASH_ITERATIONS}${salt_value}${digest}"


def _verify_web_pin(pin: str, stored_hash: Optional[str]) -> bool:
    normalized_pin = str(pin or "").strip()
    if not normalized_pin or not stored_hash:
        return False
    try:
        algorithm, iterations_raw, salt_value, expected_digest = str(stored_hash).split("$", 3)
        if algorithm != _WEB_PIN_HASH_PREFIX:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            normalized_pin.encode("utf-8"),
            salt_value.encode("utf-8"),
            int(iterations_raw),
        ).hex()
        return hmac.compare_digest(digest, expected_digest)
    except Exception:
        return False


def _empty_cliente_balance_snapshot() -> Dict[str, Decimal]:
    return {currency: Decimal("0") for currency in _CLIENT_BALANCE_CURRENCIES}


def _aggregate_cliente_movements(movements: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    balances = _empty_cliente_balance_snapshot()
    for row in movements:
        moeda = str(row.get("moeda") or "").upper()
        if moeda not in balances:
            continue
        try:
            balances[moeda] += Decimal(str(row.get("valor") or "0"))
        except Exception:
            continue
    return balances


def _aggregate_cliente_movements_by_client(movements: List[Dict[str, Any]]) -> Dict[int, Dict[str, Decimal]]:
    balances_by_client: Dict[int, Dict[str, Decimal]] = {}
    for row in movements:
        try:
            cliente_id = int(str(row.get("cliente_id") or 0))
        except Exception:
            continue
        if cliente_id <= 0:
            continue
        balances = balances_by_client.setdefault(cliente_id, _empty_cliente_balance_snapshot())
        moeda = str(row.get("moeda") or "").upper()
        if moeda not in balances:
            continue
        try:
            balances[moeda] += Decimal(str(row.get("valor") or "0"))
        except Exception:
            continue
    return balances_by_client


class DatabaseClient:
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

    def _safe_record_fx_rate(
        self,
        base_currency: str,
        quote_currency: str,
        rate: Decimal,
        source: str = "app_operation",
    ) -> None:
        """Best-effort FX snapshot for audit; no-op if table is not migrated yet."""
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
        """Best-effort immutable accounting write; no-op if journal tables are absent."""
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
                        "debit": str(line.get("debit", Decimal("0"))),
                        "credit": str(line.get("credit", Decimal("0"))),
                        "commodity_symbol": line.get("commodity_symbol"),
                        "quantity": str(line["quantity"]) if line.get("quantity") is not None else None,
                    }
                )

            self.client.table("accounting_journal_lines").insert(rows).execute()
        except Exception:
            return

    def get_ativo_by_nome(self, nome: str) -> Optional[Dict[str, Any]]:
        response = (
            self.client.table("ativos")
            .select("id,nome,tipo")
            .ilike("nome", nome)
            .limit(1)
            .execute()
        )
        data = cast(List[Dict[str, Any]], response.data or [])
        if data:
            return data[0]

        # Compat: if app requests generic "Ouro", try matching legacy names like "Ouro 24k".
        if nome.strip().lower() == "ouro":
            fallback = (
                self.client.table("ativos")
                .select("id,nome,tipo")
                .ilike("nome", "%ouro%")
                .limit(1)
                .execute()
            )
            fallback_data = cast(List[Dict[str, Any]], fallback.data or [])
            return fallback_data[0] if fallback_data else None

        return None

    def get_ativo_by_id(self, ativo_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = (
                self.client.table("ativos")
                .select("id,nome,tipo")
                .eq("id", ativo_id)
                .limit(1)
                .execute()
            )
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception:
            return None

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

    def update_usuario_nome(self, telefone: str, nome: str) -> Optional[Dict[str, Any]]:
        response = (
            self.client.table("usuarios")
            .update({"nome": nome})
            .eq("telefone", telefone)
            .eq("ativo", True)
            .execute()
        )
        data = cast(List[Dict[str, Any]], response.data or [])
        self._invalidate_runtime_cache(self._usuario_web_auth_cache_key(telefone))
        return data[0] if data else None

    def get_usuario_web_auth(self, telefone: str) -> Optional[Dict[str, Any]]:
        cache_key = self._usuario_web_auth_cache_key(telefone)
        cached = self._get_local_runtime_cache(cache_key)
        if cached is not None:
            return cast(Optional[Dict[str, Any]], cached)
        try:
            response = (
                self.client.table("usuarios")
                .select("*")
                .eq("telefone", telefone)
                .eq("ativo", True)
                .limit(1)
                .execute()
            )
            data = cast(List[Dict[str, Any]], response.data or [])
            enriched = dict(data[0]) if data else None
            if enriched is not None:
                schema_ready = "web_pin_hash" in enriched and "web_pin_updated_em" in enriched
                enriched.setdefault("web_pin_hash", None)
                enriched.setdefault("web_pin_updated_em", None)
                enriched["web_pin_schema_ready"] = schema_ready
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = schema_ready
                if schema_ready:
                    return cast(Optional[Dict[str, Any]], self._set_local_runtime_cache(cache_key, enriched))
            return enriched
        except Exception as exc:
            if self._is_missing_usuario_web_pin_schema_error(exc):
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = False
            usuario = self.get_usuario_by_telefone(telefone)
            if not usuario:
                return None
            fallback = dict(usuario)
            fallback["web_pin_hash"] = None
            fallback["web_pin_updated_em"] = None
            fallback["web_pin_schema_ready"] = False
            return fallback

    def _cliente_fields(self) -> str:
        return "id,nome,apelido,telefone,documento,observacoes,ativo,criado_em,atualizado_em"

    def _base_cliente_select(self):
        return self.client.table("clientes").select(self._cliente_fields())

    def get_cliente_by_id(self, cliente_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = self._base_cliente_select().eq("id", cliente_id).eq("ativo", True).limit(1).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            return data[0] if data else None
        except Exception:
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
        filters = [
            ("nome", f"%{normalized_query}%"),
            ("apelido", f"%{normalized_query}%"),
            ("telefone", f"%{normalized_query}%"),
            ("documento", f"%{normalized_query}%"),
        ]
        for field, value in filters:
            try:
                response = self._base_cliente_select().eq("ativo", True).ilike(field, value).limit(limit).execute()
                data = cast(List[Dict[str, Any]], response.data or [])
                for row in data:
                    client_id = int(row.get("id") or 0)
                    if client_id <= 0:
                        continue
                    results[client_id] = dict(row)
            except Exception:
                continue

        ordered = sorted(
            results.values(),
            key=lambda item: (
                str(item.get("nome") or "").lower() != normalized_query.lower(),
                str(item.get("nome") or "").lower(),
                int(item.get("id") or 0),
            ),
        )
        return cast(List[Dict[str, Any]], self._set_local_runtime_cache(cache_key, ordered[:limit]))

    def list_clientes(self, limit: int = 40) -> List[Dict[str, Any]]:
        try:
            response = self._base_cliente_select().eq("ativo", True).order("atualizado_em", desc=True).limit(limit).execute()
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception:
            return []

    def _insert_cliente_movements(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        try:
            self.client.table("cliente_movimentacoes").insert(rows).execute()
        except Exception:
            return

    def create_cliente(
        self,
        nome: str,
        telefone: Optional[str] = None,
        documento: Optional[str] = None,
        apelido: Optional[str] = None,
        observacoes: Optional[str] = None,
        opening_balances: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_name = str(nome or "").strip()
        if not normalized_name:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "nome": normalized_name,
            "telefone": str(telefone or "").strip() or None,
            "documento": str(documento or "").strip() or None,
            "apelido": str(apelido or "").strip() or None,
            "observacoes": str(observacoes or "").strip() or None,
            "ativo": True,
            "criado_em": now_iso,
            "atualizado_em": now_iso,
        }
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
                    value = Decimal(str(raw_value or "0"))
                    if value == 0:
                        continue
                    movement_rows.append(
                        {
                            "cliente_id": cliente_id,
                            "gold_transaction_id": None,
                            "moeda": currency,
                            "tipo_movimento": "abertura",
                            "valor": str(value),
                            "descricao": "Saldo inicial de cadastro",
                            "metadata": {"origem": "cadastro_cliente"},
                            "criado_em": now_iso,
                        }
                    )
                self._insert_cliente_movements(movement_rows)
            self._invalidate_client_list_cache()
            return cliente
        except Exception:
            return None

    def get_cliente_movements(self, cliente_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            response = (
                self.client.table("cliente_movimentacoes")
                .select("id,cliente_id,gold_transaction_id,moeda,tipo_movimento,valor,descricao,metadata,criado_em")
                .eq("cliente_id", cliente_id)
                .order("criado_em", desc=True)
                .limit(limit)
                .execute()
            )
            return cast(List[Dict[str, Any]], response.data or [])
        except Exception:
            return []

    def get_cliente_balance_summary(self, cliente_id: int) -> Dict[str, Decimal]:
        movements = self.get_cliente_movements(cliente_id, limit=500)
        return _aggregate_cliente_movements(movements)

    def get_cliente_balance_summaries(self, cliente_ids: List[int]) -> Dict[int, Dict[str, Decimal]]:
        normalized_ids: List[int] = []
        for cliente_id in cliente_ids:
            try:
                parsed_id = int(cliente_id)
            except Exception:
                continue
            if parsed_id > 0:
                normalized_ids.append(parsed_id)
        normalized_ids = sorted(set(normalized_ids))
        if not normalized_ids:
            return {}
        try:
            response = (
                self.client.table("cliente_movimentacoes")
                .select("cliente_id,moeda,valor")
                .in_("cliente_id", normalized_ids)
                .execute()
            )
            movements = cast(List[Dict[str, Any]], response.data or [])
            return _aggregate_cliente_movements_by_client(movements)
        except Exception:
            return {}

    def get_cliente_recent_transactions(self, cliente_id: int, limit: int = 25) -> List[Dict[str, Any]]:
        try:
            response = (
                self.client.table("gold_transactions")
                .select("id,tipo_operacao,pessoa,peso,preco_usd,total_usd,total_pago_usd,fechamento_gramas,fechamento_tipo,status,criado_em")
                .eq("cliente_id", cliente_id)
                .order("criado_em", desc=True)
                .limit(limit)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], response.data or [])
            return [row for row in rows if str(row.get("status") or "registrada").lower() != "cancelada"]
        except Exception:
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
        recent_transactions = self.get_cliente_recent_transactions(cliente_id)
        snapshot: Dict[str, Any] = {
            "cliente": cliente,
            "balances": {currency: str(value) for currency, value in balances.items()},
            "recent_transactions": recent_transactions,
            "movements": movement_rows[:50],
        }
        return cast(Dict[str, Any], self._set_runtime_cache(cache_key, snapshot))

    def list_clientes_with_balances(self, search: Optional[str] = None, limit: int = 40) -> List[Dict[str, Any]]:
        normalized_search = str(search or "").strip()
        cache_key = self._clientes_with_balances_cache_key(limit, normalized_search)
        cached = self._get_local_runtime_cache(cache_key) if normalized_search else self._get_runtime_cache(cache_key)
        if cached is not None:
            return cast(List[Dict[str, Any]], cached)

        clientes = self.search_clientes(normalized_search, limit=limit) if normalized_search else self.list_clientes(limit=limit)
        client_ids = [int(cliente.get("id") or 0) for cliente in clientes if int(cliente.get("id") or 0) > 0]
        balances_by_client = self.get_cliente_balance_summaries(client_ids)
        enriched: List[Dict[str, Any]] = []
        for cliente in clientes:
            client_id = int(cliente.get("id") or 0)
            if client_id <= 0:
                continue
            balances = balances_by_client.get(client_id, _empty_cliente_balance_snapshot())
            item = dict(cliente)
            item["balances"] = {currency: str(value) for currency, value in balances.items()}
            enriched.append(item)
        if normalized_search:
            return cast(List[Dict[str, Any]], self._set_local_runtime_cache(cache_key, enriched))
        return cast(List[Dict[str, Any]], self._set_runtime_cache(cache_key, enriched))

    def record_cliente_operation_balance(
        self,
        cliente_id: int,
        gold_transaction_id: int,
        tipo_operacao: str,
        pending_grams: Decimal,
        pessoa: Optional[str] = None,
        reverse: bool = False,
    ) -> None:
        if cliente_id <= 0 or pending_grams <= 0:
            return
        signed_value = pending_grams if str(tipo_operacao).lower() == "compra" else (pending_grams * Decimal("-1"))
        if reverse:
            signed_value *= Decimal("-1")
        now_iso = datetime.now(timezone.utc).isoformat()
        movement_type = "operacao_pendente_estorno" if reverse else "operacao_pendente"
        description = f"Saldo em ouro da operacao GT-{gold_transaction_id}"
        if reverse:
            description = f"Estorno do saldo em ouro da operacao GT-{gold_transaction_id}"
        self._insert_cliente_movements(
            [
                {
                    "cliente_id": cliente_id,
                    "gold_transaction_id": gold_transaction_id,
                    "moeda": "XAU",
                    "tipo_movimento": movement_type,
                    "valor": str(signed_value),
                    "descricao": description,
                    "metadata": {
                        "tipo_operacao": tipo_operacao,
                        "pessoa": pessoa,
                        "pending_grams": str(pending_grams),
                    },
                    "criado_em": now_iso,
                }
            ]
        )
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
        payload = {
            "web_pin_hash": _hash_web_pin(new_pin),
            "web_pin_updated_em": datetime.now(timezone.utc).isoformat(),
        }
        if type(self)._USUARIOS_WEB_PIN_SCHEMA_READY is False:
            return {
                "telefone": telefone,
                "web_pin_schema_ready": False,
                "error": "usuarios web pin schema unavailable",
            }
        try:
            response = (
                self.client.table("usuarios")
                .update(payload)
                .eq("telefone", telefone)
                .eq("ativo", True)
                .execute()
            )
            data = cast(List[Dict[str, Any]], response.data or [])
            if data:
                updated = dict(data[0])
                updated["web_pin_schema_ready"] = True
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = True
                self._invalidate_runtime_cache(self._usuario_web_auth_cache_key(telefone))
                return updated
            type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = True
            self._invalidate_runtime_cache(self._usuario_web_auth_cache_key(telefone))
            return {"telefone": telefone, "web_pin_schema_ready": True}
        except Exception as exc:
            if self._is_missing_usuario_web_pin_schema_error(exc):
                type(self)._USUARIOS_WEB_PIN_SCHEMA_READY = False
            return {
                "telefone": telefone,
                "web_pin_schema_ready": False,
                "error": str(exc),
            }

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
            legacy_response = (
                self.client.table("transacoes")
                .select("moeda_liquidacao,cambio_para_usd,data_hora")
                .in_("moeda_liquidacao", targets)
                .not_.is_("cambio_para_usd", "null")
                .order("data_hora", desc=True)
                .execute()
            )
            for row in cast(List[Dict[str, Any]], legacy_response.data or []):
                moeda = str(row.get("moeda_liquidacao") or "").upper()
                if moeda in result:
                    continue
                val = Decimal(str(row.get("cambio_para_usd", "0")))
                if val > 0:
                    result[moeda] = val

            pending = [moeda for moeda in targets if moeda not in result]
            if pending:
                gp_resp = (
                self.client.table("gold_payments")
                    .select("moeda,cambio_para_usd,id")
                    .in_("moeda", pending)
                    .not_.is_("cambio_para_usd", "null")
                    .order("id", desc=True)
                    .execute()
                )
                for row in cast(List[Dict[str, Any]], gp_resp.data or []):
                    moeda = str(row.get("moeda") or "").upper()
                    if moeda in result:
                        continue
                    val = Decimal(str(row.get("cambio_para_usd", "0")))
                    if val > 0:
                        result[moeda] = val

            return result
        except Exception as exc:
            if self._is_missing_fx_rates_schema_error(exc):
                type(self)._FX_RATES_SCHEMA_READY = False
            return {moeda: valor for moeda, valor in result.items() if moeda in {"USD", *targets}}

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

    def get_gold_inventory_transactions(self, end_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            query = (
                self.client.table("gold_transactions")
                .select("*")
                .order("criado_em", desc=False)
            )
            if end_iso:
                query = query.lt("criado_em", end_iso)
            response = query.execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            return [row for row in rows if str(row.get("status") or "registrada").lower() != "cancelada"]
        except Exception:
            return []

    def sync_gold_inventory_ledger(self) -> Dict[str, Any]:
        transactions = self.get_gold_inventory_transactions()
        lots_state: List[Dict[str, Any]] = []
        lot_rows: List[Dict[str, Any]] = []
        consumption_rows: List[Dict[str, Any]] = []

        for tx in transactions:
            try:
                tx_id = int(tx.get("id") or 0)
                tipo = str(tx.get("tipo_operacao") or "").lower()
                peso = Decimal(str(tx.get("peso") or "0"))
                preco_usd = Decimal(str(tx.get("preco_usd") or "0"))
                criado_em = str(tx.get("criado_em") or datetime.now(timezone.utc).isoformat())
            except Exception:
                continue

            if tx_id <= 0 or peso <= 0 or tipo not in {"compra", "venda"}:
                continue

            if tipo == "compra":
                lot_rows.append(
                    {
                        "source_transaction_id": tx_id,
                        "origem_tipo": tipo,
                        "created_at_tx": criado_em,
                        "initial_grams": str(peso),
                        "remaining_grams": str(peso),
                        "unit_cost_usd": str(preco_usd),
                        "total_cost_usd": str(peso * preco_usd),
                        "status": "open",
                        "metadata": {
                            "source": "sync",
                            "tx_id": tx_id,
                            "teor": str(tx.get("teor") or ""),
                            "gold_type": str(tx.get("gold_type") or ""),
                            "quebra": str(tx.get("quebra") or ""),
                            "pessoa": str(tx.get("pessoa") or ""),
                        },
                    }
                )
                lots_state.append(
                    {
                        "source_transaction_id": tx_id,
                        "created_at_tx": criado_em,
                        "remaining_grams": peso,
                        "unit_cost_usd": preco_usd,
                        "teor": str(tx.get("teor") or ""),
                        "gold_type": str(tx.get("gold_type") or ""),
                        "quebra": str(tx.get("quebra") or ""),
                        "pessoa": str(tx.get("pessoa") or ""),
                    }
                )
                continue

            remaining_sale = peso
            for lot in lots_state:
                if remaining_sale <= 0:
                    break
                lot_remaining = Decimal(str(lot.get("remaining_grams") or "0"))
                if lot_remaining <= 0:
                    continue
                consumed = min(lot_remaining, remaining_sale)
                unit_cost = Decimal(str(lot.get("unit_cost_usd") or "0"))
                consumption_rows.append(
                    {
                        "sale_transaction_id": tx_id,
                        "lot_source_transaction_id": int(lot.get("source_transaction_id") or 0),
                        "consumed_grams": str(consumed),
                        "unit_cost_usd": str(unit_cost),
                        "consumed_cost_usd": str(consumed * unit_cost),
                        "created_at_sale": criado_em,
                        "metadata": {"source": "sync", "sale_tx_id": tx_id},
                    }
                )
                lot["remaining_grams"] = lot_remaining - consumed
                remaining_sale -= consumed

        try:
            self.client.table("gold_inventory_consumptions").delete().neq("id", 0).execute()
        except Exception:
            pass
        try:
            self.client.table("gold_inventory_lots").delete().neq("id", 0).execute()
        except Exception:
            pass

        persisted_lots: Dict[int, int] = {}
        for row in lot_rows:
            source_tx_id = int(row["source_transaction_id"])
            remaining = next(
                (
                    Decimal(str(lot.get("remaining_grams") or "0"))
                    for lot in lots_state
                    if int(lot.get("source_transaction_id") or 0) == source_tx_id
                ),
                Decimal("0"),
            )
            row["remaining_grams"] = str(remaining)
            row["status"] = "open" if remaining > 0 else "consumed"
            try:
                resp = self.client.table("gold_inventory_lots").insert(row).execute()
                data = cast(List[Dict[str, Any]], resp.data or [])
                if data:
                    persisted_lots[source_tx_id] = int(data[0].get("id") or 0)
            except Exception:
                continue

        for row in consumption_rows:
            lot_id = persisted_lots.get(int(row.pop("lot_source_transaction_id", 0) or 0))
            if not lot_id:
                continue
            row["lot_id"] = lot_id
            try:
                self.client.table("gold_inventory_consumptions").insert(row).execute()
            except Exception:
                continue

        open_grams = sum(
            (Decimal(str(lot.get("remaining_grams") or "0")) for lot in lots_state),
            Decimal("0"),
        )
        self._invalidate_runtime_cache(
            "gold_inventory_overview",
            self._gold_inventory_status_cache_key(open_only=False),
            self._gold_inventory_status_cache_key(open_only=True),
            "gold_pending_closure_grams",
        )
        return {
            "lots": len(lot_rows),
            "consumptions": len(consumption_rows),
            "open_grams": str(open_grams),
        }

    def get_gold_inventory_status(self, inventory_transactions: Optional[List[Dict[str, Any]]] = None, *, open_only: bool = False) -> Dict[str, Any]:
        cache_key = self._gold_inventory_status_cache_key(open_only)
        if inventory_transactions is None:
            cached = self._get_runtime_cache(cache_key)
            if cached is not None:
                return cast(Dict[str, Any], cached)
        try:
            lots_query = (
                self.client.table("gold_inventory_lots")
                .select("id,source_transaction_id,created_at_tx,initial_grams,remaining_grams,unit_cost_usd,total_cost_usd,status,metadata")
                .order("created_at_tx", desc=False)
            )
            if open_only:
                lots_query = lots_query.eq("status", "open")
            lots_resp = lots_query.execute()
            lots = cast(List[Dict[str, Any]], lots_resp.data or [])
            has_any_lots = bool(lots)
            if open_only and not has_any_lots:
                any_lot_resp = self.client.table("gold_inventory_lots").select("id").limit(1).execute()
                has_any_lots = bool(cast(List[Dict[str, Any]], any_lot_resp.data or []))
            needs_transaction_fallback = False
            for lot in lots:
                if str(lot.get("status") or "") != "open":
                    continue
                metadata = cast(Dict[str, Any], lot.get("metadata") or {})
                if any(field not in metadata for field in ("teor", "gold_type", "quebra", "pessoa")):
                    needs_transaction_fallback = True
                    break

            tx_lookup: Dict[int, Dict[str, Any]] = {}
            if needs_transaction_fallback:
                tx_lookup = {
                    int(tx.get("id") or 0): tx
                    for tx in (inventory_transactions if inventory_transactions is not None else self.get_gold_inventory_transactions())
                }

            open_lots: List[Dict[str, Any]] = []
            for lot in lots:
                if str(lot.get("status") or "") != "open":
                    continue
                metadata = cast(Dict[str, Any], lot.get("metadata") or {})
                source_tx = tx_lookup.get(int(lot.get("source_transaction_id") or 0)) or {}
                open_lots.append(
                    {
                        **lot,
                        "teor": metadata.get("teor") or source_tx.get("teor"),
                        "gold_type": metadata.get("gold_type") or source_tx.get("gold_type"),
                        "quebra": metadata.get("quebra") or source_tx.get("quebra"),
                        "pessoa": metadata.get("pessoa") or source_tx.get("pessoa"),
                    }
                )
            available_grams = sum((Decimal(str(lot.get("remaining_grams") or "0")) for lot in open_lots), Decimal("0"))
            open_cost = sum(
                (
                    Decimal(str(lot.get("remaining_grams") or "0"))
                    * Decimal(str(lot.get("unit_cost_usd") or "0"))
                    for lot in open_lots
                ),
                Decimal("0"),
            )
            avg_cost = (open_cost / available_grams) if available_grams > 0 else Decimal("0")
            result: Dict[str, Any] = {
                "lots": lots,
                "open_lots": open_lots,
                "available_grams": str(available_grams),
                "inventory_cost_usd": str(open_cost.quantize(Decimal("0.01"))),
                "avg_cost_usd_per_gram": str(avg_cost.quantize(Decimal("0.01"))),
                "has_any_lots": has_any_lots,
            }
            if inventory_transactions is None:
                return cast(Dict[str, Any], self._set_runtime_cache(cache_key, result))
            return result
        except Exception:
            return {
                "lots": [],
                "open_lots": [],
                "available_grams": "0",
                "inventory_cost_usd": "0.00",
                "avg_cost_usd_per_gram": "0.00",
                "has_any_lots": False,
            }

    def get_gold_pending_closure_grams(self) -> Decimal:
        cached = self._get_runtime_cache("gold_pending_closure_grams")
        if cached is not None:
            return Decimal(str(cached))
        try:
            select_fields = "peso,fechamento_gramas,fechamento_tipo,status"
            if type(self)._GOLD_PENDING_CLOSURE_SCHEMA_READY is False:
                select_fields = "peso,fechamento_gramas"
            try:
                response = self.client.table("gold_transactions").select(select_fields).execute()
                type(self)._GOLD_PENDING_CLOSURE_SCHEMA_READY = select_fields != "peso,fechamento_gramas"
            except Exception:
                response = self.client.table("gold_transactions").select("peso,fechamento_gramas").execute()
                type(self)._GOLD_PENDING_CLOSURE_SCHEMA_READY = False
            rows = cast(List[Dict[str, Any]], response.data or [])
            pending_total = Decimal("0")
            for row in rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                try:
                    peso = Decimal(str(row.get("peso") or "0"))
                    fechamento = Decimal(str(row.get("fechamento_gramas") or peso or "0"))
                except Exception:
                    continue
                if peso <= 0:
                    continue
                if fechamento <= 0:
                    fechamento = peso
                aberto = max(Decimal("0"), peso - min(fechamento, peso))
                fechamento_tipo = str(row.get("fechamento_tipo") or "total").lower()
                if (fechamento_tipo == "parcial" or aberto > 0) and aberto > 0:
                    pending_total += aberto
            self._set_runtime_cache("gold_pending_closure_grams", str(pending_total))
            return pending_total
        except Exception:
            self._set_runtime_cache("gold_pending_closure_grams", "0")
            return Decimal("0")

    def get_gold_inventory_overview(self) -> Dict[str, Any]:
        cached = self._get_runtime_cache("gold_inventory_overview")
        if cached is not None:
            return cast(Dict[str, Any], cached)
        try:
            lots_resp = (
                self.client.table("gold_inventory_lots")
                .select("remaining_grams,unit_cost_usd,status")
                .eq("status", "open")
                .execute()
            )
            open_lots = cast(List[Dict[str, Any]], lots_resp.data or [])
            available_grams = sum((Decimal(str(lot.get("remaining_grams") or "0")) for lot in open_lots), Decimal("0"))
            open_cost = sum(
                (
                    Decimal(str(lot.get("remaining_grams") or "0"))
                    * Decimal(str(lot.get("unit_cost_usd") or "0"))
                    for lot in open_lots
                ),
                Decimal("0"),
            )
            avg_cost = (open_cost / available_grams) if available_grams > 0 else Decimal("0")
            return self._set_runtime_cache(
                "gold_inventory_overview",
                {
                "lots": open_lots,
                "open_lots": open_lots,
                "available_grams": str(available_grams),
                "inventory_cost_usd": str(open_cost.quantize(Decimal("0.01"))),
                "avg_cost_usd_per_gram": str(avg_cost.quantize(Decimal("0.01"))),
                },
            )
        except Exception:
            return {
                "lots": [],
                "open_lots": [],
                "available_grams": "0",
                "inventory_cost_usd": "0.00",
                "avg_cost_usd_per_gram": "0.00",
            }

    def update_gold_inventory_lot_monitor(
        self,
        lot_id: int,
        monitor_payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            current_resp = (
                self.client.table("gold_inventory_lots")
                .select("id,metadata")
                .eq("id", lot_id)
                .limit(1)
                .execute()
            )
            current_rows = cast(List[Dict[str, Any]], current_resp.data or [])
            if not current_rows:
                return None
            row = current_rows[0]
            metadata = cast(Dict[str, Any], row.get("metadata") or {})
            metadata["monitor"] = monitor_payload
            update_resp = (
                self.client.table("gold_inventory_lots")
                .update({"metadata": metadata})
                .eq("id", lot_id)
                .execute()
            )
            data = cast(List[Dict[str, Any]], update_resp.data or [])
            self._invalidate_runtime_cache(
                self._gold_inventory_status_cache_key(open_only=False),
                self._gold_inventory_status_cache_key(open_only=True),
            )
            return data[0] if data else row
        except Exception:
            return None

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

        created = data[0]

        # Best-effort FX audit snapshot (1 USD = X moeda).
        moeda_liq = moeda_liquidacao.upper()
        if moeda_liq != "USD" and cambio_para_usd > 0:
            self._safe_record_fx_rate(
                base_currency="USD",
                quote_currency=moeda_liq,
                rate=cambio_para_usd,
                source="transacoes",
            )

        # Best-effort immutable journal posting in USD equivalent.
        ativo = self.get_ativo_by_id(ativo_id)
        ativo_nome = str((ativo or {}).get("nome", f"ATIVO_{ativo_id}"))
        ativo_tipo = str((ativo or {}).get("tipo", ""))
        if ativo_tipo == "ouro":
            asset_code = "INVENTORY_COMMODITIES"
        elif ativo_tipo == "moeda":
            asset_code = "FX_POSITION_ASSET"
        else:
            # Keep scope lean: unknown asset types default to FX position accounting.
            asset_code = "FX_POSITION_ASSET"

        amount_usd = Decimal(str(valor_total))
        settlement_amount = valor_moeda if valor_moeda is not None else amount_usd
        settlement_usd = amount_usd
        if moeda_liq == "USD":
            settlement_usd = Decimal(str(settlement_amount))
        elif cambio_para_usd > 0:
            settlement_usd = Decimal(str(settlement_amount)) / cambio_para_usd

        lines: List[Dict[str, Any]] = []
        if tipo_operacao == "compra":
            lines = [
                {
                    "account_code": asset_code,
                    "currency_code": "USD",
                    "debit": amount_usd,
                    "credit": Decimal("0"),
                    "commodity_symbol": "XAU" if ativo_tipo == "ouro" else None,
                    "quantity": quantidade,
                },
                {
                    "account_code": "CASH_USD_EQUIV",
                    "currency_code": "USD",
                    "debit": Decimal("0"),
                    "credit": settlement_usd,
                },
            ]

            diff = settlement_usd - amount_usd
            if diff > 0:
                lines.append(
                    {
                        "account_code": "FX_GAIN_LOSS",
                        "currency_code": "USD",
                        "debit": diff,
                        "credit": Decimal("0"),
                    }
                )
            elif diff < 0:
                lines.append(
                    {
                        "account_code": "FX_GAIN_LOSS",
                        "currency_code": "USD",
                        "debit": Decimal("0"),
                        "credit": (diff * Decimal("-1")),
                    }
                )
        elif tipo_operacao in ("venda", "cambio"):
            lines = [
                {
                    "account_code": "CASH_USD_EQUIV",
                    "currency_code": "USD",
                    "debit": settlement_usd,
                    "credit": Decimal("0"),
                },
                {
                    "account_code": asset_code,
                    "currency_code": "USD",
                    "debit": Decimal("0"),
                    "credit": amount_usd,
                    "commodity_symbol": "XAU" if ativo_tipo == "ouro" else None,
                    "quantity": quantidade,
                },
            ]

            diff = settlement_usd - amount_usd
            if diff > 0:
                lines.append(
                    {
                        "account_code": "FX_GAIN_LOSS",
                        "currency_code": "USD",
                        "debit": Decimal("0"),
                        "credit": diff,
                    }
                )
            elif diff < 0:
                lines.append(
                    {
                        "account_code": "FX_GAIN_LOSS",
                        "currency_code": "USD",
                        "debit": (diff * Decimal("-1")),
                        "credit": Decimal("0"),
                    }
                )

        created_id_raw = created.get("id")
        created_id: Optional[int] = None
        if created_id_raw is not None:
            try:
                created_id = int(str(created_id_raw))
            except Exception:
                created_id = None

        self._safe_record_journal_entry(
            reference_table="transacoes",
            reference_id=created_id,
            description=f"{tipo_operacao} {ativo_nome}",
            source_message_id=source_message_id,
            created_by=operador_id,
            metadata={
                "ativo_id": ativo_id,
                "ativo_nome": ativo_nome,
                "ativo_tipo": ativo_tipo,
                "quantidade": str(quantidade),
                "cotacao_usada": str(cotacao_usada),
                "valor_total_usd": str(valor_total),
                "settlement_currency": moeda_liq,
                "settlement_amount": str(settlement_amount),
                "settlement_usd_equivalent": str(settlement_usd),
                "realized_fx_diff_usd": str(settlement_usd - amount_usd),
                "cambio_para_usd": str(cambio_para_usd),
                "status": status,
            },
            lines=lines,
        )

        return created

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
            payload = dict(payload)
            payload.setdefault("status", "registrada")
            try:
                header_response = self.client.table("gold_transactions").insert(payload).execute()
            except Exception:
                payload.pop("status", None)
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
                    moeda = str(pagamento.get("moeda", "USD")).upper()
                    cambio = Decimal(str(pagamento.get("cambio_para_usd", 1)))
                    if moeda != "USD" and cambio > 0:
                        self._safe_record_fx_rate(
                            base_currency="USD",
                            quote_currency=moeda,
                            rate=cambio,
                            source="gold_payments",
                        )

                    rows.append(
                        {
                            "gold_transaction_id": transaction_id,
                            "moeda": moeda,
                            "valor_moeda": pagamento.get("valor_moeda"),
                            "cambio_para_usd": pagamento.get("cambio_para_usd"),
                            "valor_usd": pagamento.get("valor_usd"),
                            "forma_pagamento": pagamento.get("forma_pagamento"),
                            "criado_em": datetime.now(timezone.utc).isoformat(),
                        }
                    )

                # Best-effort: some environments may not have criado_em in gold_payments yet.
                try:
                    self.client.table("gold_payments").insert(rows).execute()
                except Exception:
                    rows_fallback: List[Dict[str, Any]] = []
                    for row in rows:
                        row_copy = dict(row)
                        row_copy.pop("criado_em", None)
                        rows_fallback.append(row_copy)
                    try:
                        self.client.table("gold_payments").insert(rows_fallback).execute()
                    except Exception:
                        pass

            # UPDATE THE 5 CAIXAS
            op_kind = str(payload.get("tipo_operacao", "compra"))
            peso = Decimal(str(payload.get("peso", 0)))
            pessoa = str(payload.get("pessoa", "N/A"))
            cliente_id = int(payload.get("cliente_id") or 0)
            fechamento_gramas = Decimal(str(payload.get("fechamento_gramas") or peso or "0"))
            pending_grams = max(Decimal("0"), peso - fechamento_gramas)
            
            self.update_caixas_from_transaction(
                gold_transaction_id=int(transaction_id),
                tipo_operacao=op_kind,
                peso_gramas=peso,
                pagamentos=pagamentos,
                pessoa=pessoa,
            )

            # SIMPLIFIED JOURNAL ENTRY (no USD conversion)
            op_kind = str(payload.get("tipo_operacao", "compra"))
            peso = Decimal(str(payload.get("peso", 0)))
            pessoa = str(payload.get("pessoa", "N/A"))
            operador = str(payload.get("operador_id", "N/A"))

            journal_lines: List[Dict[str, Any]] = []

            # Record each moeda in its own currency
            for pagamento in pagamentos:
                moeda = str(pagamento.get("moeda", "USD")).upper()
                valor_moeda = Decimal(str(pagamento.get("valor_moeda", 0)))

                if valor_moeda <= 0:
                    continue

                if op_kind == "compra":
                    journal_lines.append(
                        {
                            "account_code": "INVENTORY_COMMODITIES",
                            "currency_code": moeda,
                            "debit": valor_moeda,
                            "credit": Decimal("0"),
                            "commodity_symbol": "XAU",
                            "quantity": peso if moeda == list(pagamentos)[0].get("moeda", "USD").upper() else None,
                        }
                    )
                    journal_lines.append(
                        {
                            "account_code": "CASH_" + moeda,
                            "currency_code": moeda,
                            "debit": Decimal("0"),
                            "credit": valor_moeda,
                        }
                    )
                else:  # venda
                    journal_lines.append(
                        {
                            "account_code": "CASH_" + moeda,
                            "currency_code": moeda,
                            "debit": valor_moeda,
                            "credit": Decimal("0"),
                        }
                    )
                    journal_lines.append(
                        {
                            "account_code": "INVENTORY_COMMODITIES",
                            "currency_code": moeda,
                            "debit": Decimal("0"),
                            "credit": valor_moeda,
                            "commodity_symbol": "XAU",
                            "quantity": peso if moeda == list(pagamentos)[0].get("moeda", "USD").upper() else None,
                        }
                    )

            self._safe_record_journal_entry(
                reference_table="gold_transactions",
                reference_id=int(transaction_id),
                description=f"{op_kind} ouro - {pessoa}",
                source_message_id=payload.get("source_message_id"),
                created_by=operador,
                metadata={
                    "pessoa": pessoa,
                    "tipo_operacao": op_kind,
                    "peso": str(peso),
                    "teor": str(payload.get("teor")),
                    "pagamentos": pagamentos,
                },
                lines=journal_lines,
            )

            if cliente_id > 0 and pending_grams > 0:
                self.record_cliente_operation_balance(
                    cliente_id=cliente_id,
                    gold_transaction_id=int(transaction_id),
                    tipo_operacao=op_kind,
                    pending_grams=pending_grams,
                    pessoa=pessoa,
                )

            self.sync_gold_inventory_ledger()
            self._invalidate_runtime_cache(
                "saldo_caixa",
                "gold_inventory_overview",
                self._gold_inventory_status_cache_key(open_only=False),
                self._gold_inventory_status_cache_key(open_only=True),
                "gold_pending_closure_grams",
            )
            self._invalidate_cliente_account_snapshot_cache(cliente_id)
            self._invalidate_client_list_cache()

            return header
        except Exception:
            return None

    def cancel_gold_transaction(self, operation_id: int, cancelled_by: Optional[str] = None) -> bool:
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
                return False

            header = header_rows[0]
            if str(header.get("status") or "registrada").lower() == "cancelada":
                return True

            cliente_id = int(header.get("cliente_id") or 0)
            peso = Decimal(str(header.get("peso") or "0"))
            fechamento_gramas = Decimal(str(header.get("fechamento_gramas") or peso or "0"))
            pending_grams = max(Decimal("0"), peso - fechamento_gramas)

            movimentacoes_response = (
                self.client.table("caixas_movimentacoes")
                .select("caixa_moeda,valor")
                .eq("gold_transaction_id", operation_id)
                .order("id", desc=False)
                .execute()
            )
            movimentacoes = cast(List[Dict[str, Any]], movimentacoes_response.data or [])

            for movimento in movimentacoes:
                moeda = str(movimento.get("caixa_moeda") or "").upper()
                if not moeda:
                    continue
                valor = Decimal(str(movimento.get("valor") or "0"))
                saldo_atual = Decimal(str(self.get_saldo_caixa().get(moeda, "0")))
                saldo_novo = saldo_atual - valor
                self.client.table("caixas").update(
                    {"saldo": str(saldo_novo), "atualizado_em": datetime.now(timezone.utc).isoformat()}
                ).eq("moeda", moeda).execute()
                self._invalidate_runtime_cache("saldo_caixa")
                self._record_caixa_movimentacao(
                    caixa_moeda=moeda,
                    tipo_operacao="ajuste",
                    gold_transaction_id=operation_id,
                    valor=(valor * Decimal("-1")),
                    saldo_anterior=saldo_atual,
                    saldo_posterior=saldo_novo,
                    descricao=f"Reversao por cancelamento GT-{operation_id}",
                    pessoa=str(header.get("pessoa") or cancelled_by or "sistema"),
                )

            if cliente_id > 0 and pending_grams > 0:
                self.record_cliente_operation_balance(
                    cliente_id=cliente_id,
                    gold_transaction_id=operation_id,
                    tipo_operacao=str(header.get("tipo_operacao") or "compra"),
                    pending_grams=pending_grams,
                    pessoa=str(header.get("pessoa") or cancelled_by or "sistema"),
                    reverse=True,
                )

            self.client.table("gold_transactions").update({"status": "cancelada"}).eq("id", operation_id).execute()
            self.sync_gold_inventory_ledger()
            self._invalidate_runtime_cache(
                "saldo_caixa",
                "gold_inventory_overview",
                self._gold_inventory_status_cache_key(open_only=False),
                self._gold_inventory_status_cache_key(open_only=True),
                "gold_pending_closure_grams",
            )
            self._invalidate_cliente_account_snapshot_cache(cliente_id)
            self._invalidate_client_list_cache()
            return True
        except Exception:
            return False

    def insert_transfer_money(
        self,
        origem_moeda: str,
        destino_moeda: str,
        valor_origem: Decimal,
        valor_destino: Decimal,
        cambio_origem_para_usd: Decimal,
        cambio_destino_para_usd: Decimal,
        operador_id: str,
        taxa_servico_origem: Decimal = Decimal("0"),
        sender_nome: Optional[str] = None,
        receiver_nome: Optional[str] = None,
        source_message_id: Optional[str] = None,
        status: str = "registrada",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Register transfer money operation with audit-grade FX and accounting records."""
        origem = origem_moeda.upper()
        destino = destino_moeda.upper()

        if valor_origem <= 0 or valor_destino <= 0:
            return None
        if cambio_origem_para_usd <= 0 or cambio_destino_para_usd <= 0:
            return None

        valor_origem_usd = valor_origem / cambio_origem_para_usd
        valor_destino_usd = valor_destino / cambio_destino_para_usd
        fee_usd = taxa_servico_origem / cambio_origem_para_usd if taxa_servico_origem > 0 else Decimal("0")

        payload: Dict[str, Any] = {
            "data_hora": datetime.now(timezone.utc).isoformat(),
            "sender_nome": sender_nome,
            "receiver_nome": receiver_nome,
            "origem_moeda": origem,
            "destino_moeda": destino,
            "valor_origem": str(valor_origem),
            "cambio_origem_para_usd": str(cambio_origem_para_usd),
            "cambio_destino_para_usd": str(cambio_destino_para_usd),
            "taxa_servico_origem": str(taxa_servico_origem),
            "valor_destino": str(valor_destino),
            "valor_origem_usd": str(valor_origem_usd),
            "valor_destino_usd": str(valor_destino_usd),
            "operador_id": operador_id,
            "source_message_id": source_message_id,
            "status": status,
            "metadata": metadata or {},
            "criado_em": datetime.now(timezone.utc).isoformat(),
        }

        try:
            response = self.client.table("transfer_money_transactions").insert(payload).execute()
            data = cast(List[Dict[str, Any]], response.data or [])
            if not data:
                return None
            created = data[0]
        except Exception:
            return None

        if origem != "USD":
            self._safe_record_fx_rate("USD", origem, cambio_origem_para_usd, "transfer_money")
        if destino != "USD":
            self._safe_record_fx_rate("USD", destino, cambio_destino_para_usd, "transfer_money")

        transfer_clear_usd = valor_origem_usd - fee_usd
        fx_diff_usd = transfer_clear_usd - valor_destino_usd

        lines: List[Dict[str, Any]] = [
            {
                "account_code": "CASH_USD_EQUIV",
                "currency_code": "USD",
                "debit": valor_origem_usd,
                "credit": Decimal("0"),
            },
            {
                "account_code": "TRANSFER_CLEARING",
                "currency_code": "USD",
                "debit": Decimal("0"),
                "credit": transfer_clear_usd,
            },
            {
                "account_code": "TRANSFER_CLEARING",
                "currency_code": "USD",
                "debit": valor_destino_usd,
                "credit": Decimal("0"),
            },
            {
                "account_code": "CASH_USD_EQUIV",
                "currency_code": "USD",
                "debit": Decimal("0"),
                "credit": valor_destino_usd,
            },
        ]

        if fee_usd > 0:
            lines.append(
                {
                    "account_code": "TRANSFER_FEE_REVENUE",
                    "currency_code": "USD",
                    "debit": Decimal("0"),
                    "credit": fee_usd,
                }
            )

        if fx_diff_usd > 0:
            lines.append(
                {
                    "account_code": "FX_GAIN_LOSS",
                    "currency_code": "USD",
                    "debit": Decimal("0"),
                    "credit": fx_diff_usd,
                }
            )
        elif fx_diff_usd < 0:
            lines.append(
                {
                    "account_code": "FX_GAIN_LOSS",
                    "currency_code": "USD",
                    "debit": fx_diff_usd * Decimal("-1"),
                    "credit": Decimal("0"),
                }
            )

        created_id_raw = created.get("id")
        created_id: Optional[int] = None
        if created_id_raw is not None:
            try:
                created_id = int(str(created_id_raw))
            except Exception:
                created_id = None

        self._safe_record_journal_entry(
            reference_table="transfer_money_transactions",
            reference_id=created_id,
            description=f"transfer money {origem}->{destino}",
            source_message_id=source_message_id,
            created_by=operador_id,
            metadata={
                "origem_moeda": origem,
                "destino_moeda": destino,
                "valor_origem": str(valor_origem),
                "valor_destino": str(valor_destino),
                "valor_origem_usd": str(valor_origem_usd),
                "valor_destino_usd": str(valor_destino_usd),
                "fee_usd": str(fee_usd),
                "fx_diff_usd": str(fx_diff_usd),
                "status": status,
            },
            lines=lines,
        )

        return created

    def get_daily_gold_summary(self, start_iso: str, end_iso: str) -> Dict[str, Any]:
        """Count operations from BOTH transacoes (simple) and gold_transactions (guided) tables."""
        try:
            # Count simple transacoes (quick flow)
            resp_t = (
                self.client.table("transacoes")
                .select("id,valor_total,status")
                .gte("data_hora", start_iso)
                .lt("data_hora", end_iso)
                .execute()
            )
            t_rows = [
                row
                for row in cast(List[Dict[str, Any]], resp_t.data or [])
                if str(row.get("status") or "registrada").lower() != "cancelada"
            ]
            t_ops = len(t_rows)
            t_usd = sum((Decimal(str(r.get("valor_total", 0))) for r in t_rows), Decimal("0"))

            # Count enterprise gold_transactions (guided flow)
            resp_g = (
                self.client.table("gold_transactions")
                .select("*")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            g_rows = [
                row
                for row in cast(List[Dict[str, Any]], resp_g.data or [])
                if str(row.get("status") or "registrada").lower() != "cancelada"
            ]
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
                .select("*")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            rows = [
                row
                for row in cast(List[Dict[str, Any]], response.data or [])
                if str(row.get("status") or "registrada").lower() != "cancelada"
            ]
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
            gt_response = (
                self.client.table("gold_transactions")
                .select("*")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            valid_ids = [
                int(str(row.get("id")))
                for row in cast(List[Dict[str, Any]], gt_response.data or [])
                if row.get("id") is not None and str(row.get("status") or "registrada").lower() != "cancelada"
            ]
            if not valid_ids:
                return []

            response = (
                self.client.table("gold_payments")
                .select("moeda,valor_moeda,valor_usd")
                .in_("gold_transaction_id", valid_ids)
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

    def get_extrato_transactions(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        """Returns all gold operations in [start_iso, end_iso) ordered by date.

        Priority: gold_transactions (guided flow, rich detail).
        Supplement: transacoes entries that have no matching gold_transaction
        (simple / AI flow). Duplicates from guided flow are detected by comparing
        operator + timestamp within a 10-second window and are excluded from the
        transacoes list.
        """
        from datetime import datetime as _dt

        result: List[Dict[str, Any]] = []
        gt_timestamps: List[Dict[str, str]] = []

        # 1. Guided-flow records (gold_transactions + their payments).
        try:
            gt_resp = (
                self.client.table("gold_transactions")
                .select("*")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .order("criado_em", desc=False)
                .execute()
            )
            gt_rows = cast(List[Dict[str, Any]], gt_resp.data or [])
            gt_id_list = [int(r["id"]) for r in gt_rows if r.get("id") is not None]

            payments_by_tx: Dict[int, List[Dict[str, Any]]] = {}
            if gt_id_list:
                gp_resp = (
                    self.client.table("gold_payments")
                    .select("gold_transaction_id,moeda,valor_moeda,cambio_para_usd,valor_usd,forma_pagamento")
                    .in_("gold_transaction_id", gt_id_list)
                    .execute()
                )
                gp_rows = cast(List[Dict[str, Any]], gp_resp.data or [])
                for p in gp_rows:
                    tid = int(str(p.get("gold_transaction_id", 0) or 0))
                    payments_by_tx.setdefault(tid, []).append(p)

            for row in gt_rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                transaction_id = row.get("id")
                tid_int = int(str(transaction_id)) if transaction_id is not None else 0
                criado_em = str(row.get("criado_em") or "")
                operador = str(row.get("operador_id") or "")
                gt_timestamps.append({"ts": criado_em, "op": operador})
                result.append({
                    "source": "gold_transactions",
                    "id": transaction_id,
                    "cliente_id": row.get("cliente_id"),
                    "tipo_operacao": row.get("tipo_operacao"),
                    "origem": row.get("origem"),
                    "teor": row.get("teor"),
                    "peso": row.get("peso"),
                    "preco_usd": row.get("preco_usd"),
                    "total_usd": row.get("total_usd"),
                    "total_pago_usd": row.get("total_pago_usd"),
                    "diferenca_usd": row.get("diferenca_usd"),
                    "pessoa": row.get("pessoa"),
                    "operador_id": str(row.get("operador_id") or ""),
                    "forma_pagamento": row.get("forma_pagamento"),
                    "observacoes": row.get("observacoes"),
                    "contexto": row.get("contexto") if isinstance(row.get("contexto"), dict) else {},
                    "criado_em": criado_em,
                    "pagamentos": payments_by_tx.get(tid_int, []),
                })
        except Exception:
            pass

        # 2. Simple-flow records from transacoes that are NOT a guided-flow duplicate.
        try:
            t_resp = (
                self.client.table("transacoes")
                .select(
                    "id,tipo_operacao,quantidade,cotacao_usada,valor_total,"
                    "moeda_liquidacao,valor_moeda,cambio_para_usd,operador_id,status,data_hora"
                )
                .gte("data_hora", start_iso)
                .lt("data_hora", end_iso)
                .order("data_hora", desc=False)
                .execute()
            )
            t_rows = cast(List[Dict[str, Any]], t_resp.data or [])

            for row in t_rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                op = str(row.get("operador_id") or "")
                ts = str(row.get("data_hora") or "")

                # Skip if a gold_transaction from the same operator exists within 10 s.
                is_guided_duplicate = False
                try:
                    t_time = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                    for gt_meta in gt_timestamps:
                        if gt_meta["op"] != op:
                            continue
                        gt_time = _dt.fromisoformat(gt_meta["ts"].replace("Z", "+00:00"))
                        if abs((t_time - gt_time).total_seconds()) <= 10:
                            is_guided_duplicate = True
                            break
                except Exception:
                    pass

                if is_guided_duplicate:
                    continue

                result.append({
                    "source": "transacoes",
                    "id": row.get("id"),
                    "tipo_operacao": row.get("tipo_operacao"),
                    "peso": row.get("quantidade"),
                    "preco_usd": row.get("cotacao_usada"),
                    "total_usd": row.get("valor_total"),
                    "total_pago_usd": row.get("valor_total"),
                    "diferenca_usd": "0",
                    "moeda": row.get("moeda_liquidacao"),
                    "valor_moeda": row.get("valor_moeda"),
                    "cambio_para_usd": row.get("cambio_para_usd"),
                    "operador_id": str(row.get("operador_id") or ""),
                    "status": row.get("status"),
                    "criado_em": ts,
                    "pagamentos": [],
                })
        except Exception:
            pass

        result.sort(key=lambda r: str(r.get("criado_em") or ""))
        return result

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

    def _ensure_caixas_exist(self) -> None:
        """Ensure all 5 caixas exist in the database."""
        if type(self)._CAIXAS_READY is True:
            return

        moedas = ["XAU", "EUR", "USD", "SRD", "BRL"]
        try:
            response = (
                self.client.table("caixas")
                .select("moeda")
                .in_("moeda", moedas)
                .execute()
            )
            existing = {
                str(row.get("moeda") or "").upper()
                for row in cast(List[Dict[str, Any]], response.data or [])
            }
        except Exception:
            return

        missing = [moeda for moeda in moedas if moeda not in existing]
        for moeda in missing:
            try:
                self.client.table("caixas").insert({"moeda": moeda, "saldo": "0"}).execute()
            except Exception:
                pass

        type(self)._CAIXAS_READY = True

    def _record_caixa_movimentacao(
        self,
        caixa_moeda: str,
        tipo_operacao: str,
        gold_transaction_id: Optional[int],
        valor: Decimal,
        saldo_anterior: Decimal,
        saldo_posterior: Decimal,
        descricao: Optional[str] = None,
        pessoa: Optional[str] = None,
    ) -> None:
        """Record a caixa movement in audit trail."""
        try:
            payload: Dict[str, Any] = {
                "caixa_moeda": caixa_moeda.upper(),
                "tipo_operacao": tipo_operacao,
                "gold_transaction_id": gold_transaction_id,
                "valor": str(valor),
                "saldo_anterior": str(saldo_anterior),
                "saldo_posterior": str(saldo_posterior),
                "descricao": descricao,
                "pessoa": pessoa,
                "criado_em": datetime.now(timezone.utc).isoformat(),
            }
            self.client.table("caixas_movimentacoes").insert(payload).execute()
        except Exception:
            return

    def _calculate_caixas_from_history(self) -> Dict[str, Decimal]:
        """Rebuild 5-caixas balances from legacy + enterprise transaction history."""
        saldos: Dict[str, Decimal] = {
            "XAU": Decimal("0"),
            "EUR": Decimal("0"),
            "USD": Decimal("0"),
            "SRD": Decimal("0"),
            "BRL": Decimal("0"),
        }

        ouro = self.get_ativo_by_nome("Ouro")
        if not ouro:
            ouro = self.get_ativo_by_nome("Ouro 24k")
        ouro_id = int(ouro["id"]) if ouro else None

        # 1) Legacy table: transacoes
        try:
            t_resp = (
                self.client.table("transacoes")
                .select("tipo_operacao,ativo_id,quantidade,moeda_liquidacao,valor_moeda,valor_total,status")
                .execute()
            )
            t_rows = cast(List[Dict[str, Any]], t_resp.data or [])
            for row in t_rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                tipo = str(row.get("tipo_operacao", ""))
                aid = int(row.get("ativo_id", 0))
                qty = Decimal(str(row.get("quantidade", "0")))

                if ouro_id is not None and aid == ouro_id:
                    if tipo == "compra":
                        saldos["XAU"] += qty
                    elif tipo in ("venda", "cambio"):
                        saldos["XAU"] -= qty

                moeda = str(row.get("moeda_liquidacao") or "USD").upper()
                valor_m_raw = row.get("valor_moeda")
                if valor_m_raw is not None:
                    valor_m = Decimal(str(valor_m_raw))
                else:
                    moeda = "USD"
                    valor_m = Decimal(str(row.get("valor_total", "0")))

                if moeda not in saldos:
                    continue

                if tipo == "venda":
                    saldos[moeda] += valor_m
                elif tipo == "compra":
                    saldos[moeda] -= valor_m
        except Exception:
            pass

        # 2) Enterprise table: gold_transactions + gold_payments
        gt_tipo_map: Dict[int, str] = {}
        gt_context_pagamentos: Dict[int, List[Dict[str, Any]]] = {}
        try:
            gt_resp = (
                self.client.table("gold_transactions")
                .select("*")
                .execute()
            )
            gt_rows = cast(List[Dict[str, Any]], gt_resp.data or [])

            for row in gt_rows:
                if str(row.get("status") or "registrada").lower() == "cancelada":
                    continue
                gid = int(row.get("id", 0))
                tipo = str(row.get("tipo_operacao", ""))
                gt_tipo_map[gid] = tipo

                peso = Decimal(str(row.get("peso", "0")))
                if tipo == "compra":
                    saldos["XAU"] += peso
                elif tipo in ("venda", "cambio"):
                    saldos["XAU"] -= peso

                contexto_raw = row.get("contexto")
                if isinstance(contexto_raw, dict):
                    contexto_dict = cast(Dict[str, Any], contexto_raw)
                    pagamentos_ctx = contexto_dict.get("pagamentos")
                    if isinstance(pagamentos_ctx, list):
                        pagamentos_validos: List[Dict[str, Any]] = []
                        for raw_pagamento in cast(List[Any], pagamentos_ctx):
                            if isinstance(raw_pagamento, dict):
                                pagamentos_validos.append(cast(Dict[str, Any], raw_pagamento))
                        gt_context_pagamentos[gid] = pagamentos_validos
        except Exception:
            gt_tipo_map = {}
            gt_context_pagamentos = {}

        gp_tx_ids: set[int] = set()
        try:
            gp_resp = (
                self.client.table("gold_payments")
                .select("gold_transaction_id,moeda,valor_moeda")
                .execute()
            )
            gp_rows = cast(List[Dict[str, Any]], gp_resp.data or [])

            for row in gp_rows:
                gid = int(row.get("gold_transaction_id", 0))
                tipo = gt_tipo_map.get(gid, "compra")
                moeda = str(row.get("moeda", "USD")).upper()
                val = Decimal(str(row.get("valor_moeda", "0")))
                gp_tx_ids.add(gid)

                if moeda not in saldos:
                    continue

                if tipo == "venda":
                    saldos[moeda] += val
                elif tipo == "compra":
                    saldos[moeda] -= val
        except Exception:
            gp_tx_ids = set()

        # Fallback for guided transactions without rows in gold_payments
        for gid, pagamentos in gt_context_pagamentos.items():
            if gid in gp_tx_ids:
                continue
            tipo = gt_tipo_map.get(gid, "compra")
            for pagamento in pagamentos:
                moeda = str(pagamento.get("moeda", "USD")).upper()
                val = Decimal(str(pagamento.get("valor_moeda", "0")))

                if moeda not in saldos:
                    continue

                if tipo == "venda":
                    saldos[moeda] += val
                elif tipo == "compra":
                    saldos[moeda] -= val

        return saldos

    def backfill_caixas_from_history(self, clear_movements: bool = False) -> Dict[str, Any]:
        """One-time migration: recalculate caixas from full history and persist balances."""
        self._ensure_caixas_exist()

        current = self.get_saldo_caixa()
        recalculated = self._calculate_caixas_from_history()

        if clear_movements:
            try:
                self.client.table("caixas_movimentacoes").delete().neq("id", 0).execute()
            except Exception:
                pass

        now_iso = datetime.now(timezone.utc).isoformat()
        for moeda in ["XAU", "EUR", "USD", "SRD", "BRL"]:
            saldo_anterior = Decimal(str(current.get(moeda, "0")))
            saldo_novo = recalculated.get(moeda, Decimal("0"))

            try:
                self.client.table("caixas").update(
                    {"saldo": str(saldo_novo), "atualizado_em": now_iso}
                ).eq("moeda", moeda).execute()
            except Exception:
                continue

            if saldo_anterior != saldo_novo:
                self._record_caixa_movimentacao(
                    caixa_moeda=moeda,
                    tipo_operacao="ajuste",
                    gold_transaction_id=None,
                    valor=(saldo_novo - saldo_anterior),
                    saldo_anterior=saldo_anterior,
                    saldo_posterior=saldo_novo,
                    descricao="Backfill histórico para novo sistema de 5 caixas",
                    pessoa="sistema",
                )

        self._invalidate_runtime_cache("saldo_caixa")

        return {
            "before": current,
            "after": {k: str(v) for k, v in recalculated.items()},
        }

    def update_caixas_from_transaction(
        self,
        gold_transaction_id: int,
        tipo_operacao: str,
        peso_gramas: Decimal,
        pagamentos: List[Dict[str, Any]],
        pessoa: Optional[str] = None,
    ) -> None:
        """Update all 5 caixas based on a transaction.
        
        Direction rules (caixa drawer perspective):
          - COMPRA: ouro ENTRA (+gramas), dinheiro SAI (-moeda)
          - VENDA: ouro SAI (-gramas), dinheiro ENTRA (+moeda)
        """
        try:
            self._ensure_caixas_exist()

            # Update XAU (ouro) caixa
            if peso_gramas > 0:
                direcao_xau = Decimal("1") if tipo_operacao == "compra" else Decimal("-1")
                movimento_xau = peso_gramas * direcao_xau

                resp = self.client.table("caixas").select("saldo").eq("moeda", "XAU").execute()
                rows = cast(List[Dict[str, Any]], resp.data or [])
                saldo_anterior_xau = Decimal(str(rows[0].get("saldo", 0))) if rows else Decimal("0")
                saldo_posterior_xau = saldo_anterior_xau + movimento_xau

                self.client.table("caixas").update({"saldo": str(saldo_posterior_xau), "atualizado_em": datetime.now(timezone.utc).isoformat()}).eq("moeda", "XAU").execute()
                self._invalidate_runtime_cache("saldo_caixa")

                self._record_caixa_movimentacao(
                    caixa_moeda="XAU",
                    tipo_operacao=tipo_operacao,
                    gold_transaction_id=gold_transaction_id,
                    valor=movimento_xau,
                    saldo_anterior=saldo_anterior_xau,
                    saldo_posterior=saldo_posterior_xau,
                    descricao=f"{tipo_operacao} ouro",
                    pessoa=pessoa,
                )

            # Update moeda caixas (EUR, USD, SRD, BRL)
            for pagamento in pagamentos:
                moeda = str(pagamento.get("moeda", "USD")).upper()
                valor_moeda = Decimal(str(pagamento.get("valor_moeda", "0")))

                if valor_moeda == 0:
                    continue

                # Para COMPRA: dinheiro SAI (-), para VENDA: dinheiro ENTRA (+)
                direcao_moeda = Decimal("-1") if tipo_operacao == "compra" else Decimal("1")
                movimento_moeda = valor_moeda * direcao_moeda

                resp = self.client.table("caixas").select("saldo").eq("moeda", moeda).execute()
                rows = cast(List[Dict[str, Any]], resp.data or [])
                saldo_anterior_moeda = Decimal(str(rows[0].get("saldo", 0))) if rows else Decimal("0")
                saldo_posterior_moeda = saldo_anterior_moeda + movimento_moeda

                self.client.table("caixas").update({"saldo": str(saldo_posterior_moeda), "atualizado_em": datetime.now(timezone.utc).isoformat()}).eq("moeda", moeda).execute()
                self._invalidate_runtime_cache("saldo_caixa")

                self._record_caixa_movimentacao(
                    caixa_moeda=moeda,
                    tipo_operacao=tipo_operacao,
                    gold_transaction_id=gold_transaction_id,
                    valor=movimento_moeda,
                    saldo_anterior=saldo_anterior_moeda,
                    saldo_posterior=saldo_posterior_moeda,
                    descricao=f"{tipo_operacao} ouro ({moeda})",
                    pessoa=pessoa,
                )

        except Exception:
            pass

    def get_saldo_caixa(self) -> Dict[str, Any]:
        """Get current balance for all 5 caixas.
        
        Returns:
          {
            "XAU": "1614.0",        # gramas
            "EUR": "-1861.40",      # euros
            "USD": "-123237.00",    # dólares
            "SRD": "-12236.00",     # surinamês
            "BRL": "-2080.00"       # reais
          }
        
        Each cache is independent - no conversion, no USD reference.
        """
        cached = self._get_runtime_cache("saldo_caixa")
        if cached is not None:
            return cast(Dict[str, Any], cached)
        try:
            self._ensure_caixas_exist()
            
            resp = self.client.table("caixas").select("moeda,saldo").execute()
            rows = cast(List[Dict[str, Any]], resp.data or [])
            
            result: Dict[str, Any] = {}
            for row in rows:
                moeda = str(row.get("moeda", "")).upper()
                saldo = Decimal(str(row.get("saldo", "0")))
                result[moeda] = str(saldo)
            
            # Ensure all 5 caixas are present
            for moeda in ["XAU", "EUR", "USD", "SRD", "BRL"]:
                if moeda not in result:
                    result[moeda] = "0"
            
            return self._set_runtime_cache("saldo_caixa", result)
        except Exception:
            # Fallback silently
            return {
                "XAU": "0",
                "EUR": "0", 
                "USD": "0",
                "SRD": "0",
                "BRL": "0"
            }

    def get_top_divergences(self, start_iso: str, end_iso: str, limit: int = 10) -> List[Dict[str, Any]]:
        # Soft-fail if enterprise tables are not migrated yet.
        try:
            response = (
                self.client.table("gold_transactions")
                .select("*")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
                .execute()
            )
            rows = [
                row
                for row in cast(List[Dict[str, Any]], response.data or [])
                if str(row.get("status") or "registrada").lower() != "cancelada"
            ]
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

            consumptions_response = (
                self.client.table("gold_inventory_consumptions")
                .select("id,sale_transaction_id,lot_id,consumed_grams,unit_cost_usd,consumed_cost_usd,created_at_sale,metadata")
                .eq("sale_transaction_id", operation_id)
                .order("id", desc=False)
                .execute()
            )
            consumptions = cast(List[Dict[str, Any]], consumptions_response.data or [])

            return {"operation": header, "payments": payments, "inventory_consumptions": consumptions}
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

    def get_multi_agent_runs_range(self, start_iso: str, end_iso: str, limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch multi-agent runs in a time range.

        Falls back to logs when `multi_agent_runs` table is unavailable.
        """
        safe_limit = max(1, min(limit, 2000))
        try:
            response = (
                self.client.table("multi_agent_runs")
                .select("id,objective,operation_id,operation_kind,source_message_id,response_payload,criado_em")
                .gte("criado_em", start_iso)
                .lt("criado_em", end_iso)
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
                    .gte("data_hora", start_iso)
                    .lt("data_hora", end_iso)
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

    def get_transaction_learning_snapshot(self, lookback_days: int = 45) -> Dict[str, Any]:
        """Build lightweight learning features from real transactions.

        This is a non-ML, deterministic statistical profile used by agents
        to detect outliers and adapt risk thresholds based on historical behavior.
        """
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=max(lookback_days, 1))).isoformat()

        def _empty() -> Dict[str, Any]:
            return {
                "lookback_days": max(lookback_days, 1),
                "total_samples": 0,
                "operations": {},
                "currency_mix": {},
                "operator_profiles": {},
            }

        try:
            response = (
                self.client.table("gold_transactions")
                .select("*")
                .gte("criado_em", start)
                .execute()
            )
            rows = [
                row
                for row in cast(List[Dict[str, Any]], response.data or [])
                if str(row.get("status") or "registrada").lower() != "cancelada"
            ]
        except Exception:
            return _empty()

        if not rows:
            return _empty()

        op_acc: Dict[str, Dict[str, Decimal]] = {}
        operator_acc: Dict[str, Dict[str, Decimal]] = {}
        ids: List[int] = []

        for row in rows:
            tx_id_raw = row.get("id")
            if tx_id_raw is not None:
                try:
                    ids.append(int(str(tx_id_raw)))
                except Exception:
                    pass

            op = str(row.get("tipo_operacao", "desconhecida")).lower()
            peso = Decimal(str(row.get("peso", "0")))
            total_usd = Decimal(str(row.get("total_usd", "0")))
            abs_diff = abs(Decimal(str(row.get("diferenca_usd", "0"))))
            operador = str(row.get("operador_id", "desconhecido"))

            if op not in op_acc:
                op_acc[op] = {
                    "count": Decimal("0"),
                    "peso_sum": Decimal("0"),
                    "peso_sq_sum": Decimal("0"),
                    "total_sum": Decimal("0"),
                    "total_sq_sum": Decimal("0"),
                    "diff_abs_sum": Decimal("0"),
                    "diff_abs_sq_sum": Decimal("0"),
                }
            op_acc[op]["count"] += Decimal("1")
            op_acc[op]["peso_sum"] += peso
            op_acc[op]["peso_sq_sum"] += peso * peso
            op_acc[op]["total_sum"] += total_usd
            op_acc[op]["total_sq_sum"] += total_usd * total_usd
            op_acc[op]["diff_abs_sum"] += abs_diff
            op_acc[op]["diff_abs_sq_sum"] += abs_diff * abs_diff

            if operador not in operator_acc:
                operator_acc[operador] = {
                    "count": Decimal("0"),
                    "diff_abs_sum": Decimal("0"),
                    "total_sum": Decimal("0"),
                }
            operator_acc[operador]["count"] += Decimal("1")
            operator_acc[operador]["diff_abs_sum"] += abs_diff
            operator_acc[operador]["total_sum"] += total_usd

        currency_mix: Dict[str, int] = {}
        if ids:
            try:
                pay_resp = (
                    self.client.table("gold_payments")
                    .select("gold_transaction_id,moeda")
                    .in_("gold_transaction_id", ids)
                    .execute()
                )
                pay_rows = cast(List[Dict[str, Any]], pay_resp.data or [])
                for pay in pay_rows:
                    moeda = str(pay.get("moeda", "USD")).upper()
                    currency_mix[moeda] = currency_mix.get(moeda, 0) + 1
            except Exception:
                pass

        def _mean_std(sum_v: Decimal, sq_sum_v: Decimal, n: Decimal) -> Dict[str, str]:
            if n <= 0:
                return {"mean": "0", "std": "0"}
            mean = sum_v / n
            variance = (sq_sum_v / n) - (mean * mean)
            if variance < 0:
                variance = Decimal("0")
            std = Decimal(str(sqrt(float(variance))))
            return {"mean": str(mean), "std": str(std)}

        operations: Dict[str, Any] = {}
        for op, acc in op_acc.items():
            n = acc["count"]
            peso_stats = _mean_std(acc["peso_sum"], acc["peso_sq_sum"], n)
            total_stats = _mean_std(acc["total_sum"], acc["total_sq_sum"], n)
            diff_stats = _mean_std(acc["diff_abs_sum"], acc["diff_abs_sq_sum"], n)
            operations[op] = {
                "count": int(n),
                "peso_mean": peso_stats["mean"],
                "peso_std": peso_stats["std"],
                "total_usd_mean": total_stats["mean"],
                "total_usd_std": total_stats["std"],
                "abs_diff_usd_mean": diff_stats["mean"],
                "abs_diff_usd_std": diff_stats["std"],
            }

        operator_profiles: Dict[str, Any] = {}
        for operador, acc in operator_acc.items():
            n = acc["count"]
            if n <= 0:
                continue
            operator_profiles[operador] = {
                "count": int(n),
                "avg_abs_diff_usd": str(acc["diff_abs_sum"] / n),
                "avg_total_usd": str(acc["total_sum"] / n),
            }

        return {
            "lookback_days": max(lookback_days, 1),
            "total_samples": len(rows),
            "operations": operations,
            "currency_mix": currency_mix,
            "operator_profiles": operator_profiles,
        }

    def build_multi_agent_live_context(self, operation_id: Optional[int] = None) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
        end = now.isoformat()

        context: Dict[str, Any] = {
            "daily_summary": self.get_daily_gold_summary(start, end),
            "daily_by_currency": self.get_gold_summary_by_currency(start, end),
            "saldo_caixa": self.get_saldo_caixa(),
            "risk_alerts": self.get_risk_alerts(start, end),
            "top_divergences": self.get_top_divergences(start, end, limit=3),
            "recent_runs": self.get_recent_multi_agent_runs(limit=3),
            "learning_snapshot": self.get_transaction_learning_snapshot(lookback_days=45),
        }

        if operation_id is not None:
            context["operation_audit"] = self.get_gold_operation_audit(operation_id)

        return context
