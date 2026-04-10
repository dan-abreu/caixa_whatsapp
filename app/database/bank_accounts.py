import logging

from .common import Any, Dict, List, Optional, _CLIENT_BALANCE_CURRENCIES, _safe_int, cast, datetime, timezone


logger = logging.getLogger("caixa_whatsapp")
_BANK_ACCOUNT_OWNER_KINDS = {"cliente", "fornecedor", "empresa"}
_BANK_ACCOUNT_COUNTRY_BY_CURRENCY = {"SRD": "SR", "BRL": "BR"}


class BankAccountsMixin:
    def _bank_account_fields(self) -> str:
        return "id,owner_kind,owner_id,currency_code,country_code,label,holder_name,bank_name,branch_name,branch_code,account_number,pix_key,document_number,notes,metadata,is_default,active,created_by_phone,criado_em,atualizado_em"

    def _list_saved_bank_accounts(self, owner_kind: str, owner_id: Optional[int], currency_code: Optional[str] = None) -> List[Dict[str, Any]]:
        normalized_owner_kind = str(owner_kind or "").strip().lower()
        if normalized_owner_kind not in _BANK_ACCOUNT_OWNER_KINDS:
            return []
        normalized_currency = str(currency_code or "").strip().upper() or None
        if normalized_currency and normalized_currency not in _CLIENT_BALANCE_CURRENCIES:
            return []
        cache_key = self._bank_accounts_cache_key(normalized_owner_kind, owner_id, normalized_currency)
        cached = self._get_runtime_cache(cache_key)
        if cached is not None:
            return cast(List[Dict[str, Any]], cached)
        try:
            query = self.client.table("saved_bank_accounts").select(self._bank_account_fields()).eq("owner_kind", normalized_owner_kind).eq("active", True)
            if normalized_owner_kind == "empresa":
                query = query.is_("owner_id", "null")
            elif _safe_int(owner_id, context="saved_bank_accounts.owner_id") > 0:
                query = query.eq("owner_id", int(owner_id or 0))
            else:
                return []
            if normalized_currency:
                query = query.eq("currency_code", normalized_currency)
            rows = cast(List[Dict[str, Any]], query.order("is_default", desc=True).order("atualizado_em", desc=True).execute().data or [])
            return cast(List[Dict[str, Any]], self._set_runtime_cache(cache_key, rows))
        except Exception as exc:
            logger.warning("Falha ao listar contas bancarias salvas (%s/%s): %s", normalized_owner_kind, owner_id, exc)
            return []

    def list_company_bank_accounts(self, currency_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._list_saved_bank_accounts("empresa", None, currency_code=currency_code)

    def list_cliente_bank_accounts(self, cliente_id: int, currency_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._list_saved_bank_accounts("cliente", cliente_id, currency_code=currency_code)

    def list_fornecedor_bank_accounts(self, fornecedor_id: int, currency_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._list_saved_bank_accounts("fornecedor", fornecedor_id, currency_code=currency_code)

    def get_saved_bank_account_by_id(self, bank_account_id: int) -> Optional[Dict[str, Any]]:
        if bank_account_id <= 0:
            return None
        try:
            response = self.client.table("saved_bank_accounts").select(self._bank_account_fields()).eq("id", bank_account_id).eq("active", True).limit(1).execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            return rows[0] if rows else None
        except Exception as exc:
            logger.warning("Falha ao carregar conta bancaria %s: %s", bank_account_id, exc)
            return None

    def create_saved_bank_account(
        self,
        *,
        owner_kind: str,
        owner_id: Optional[int],
        currency_code: str,
        label: str,
        holder_name: str,
        bank_name: Optional[str] = None,
        branch_name: Optional[str] = None,
        branch_code: Optional[str] = None,
        account_number: Optional[str] = None,
        pix_key: Optional[str] = None,
        document_number: Optional[str] = None,
        notes: Optional[str] = None,
        country_code: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        is_default: bool = False,
        created_by_phone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_owner_kind = str(owner_kind or "").strip().lower()
        normalized_owner_id = _safe_int(owner_id, context="create_saved_bank_account.owner_id")
        normalized_currency = str(currency_code or "").strip().upper()
        normalized_label = str(label or "").strip()
        normalized_holder = str(holder_name or "").strip()
        normalized_bank = str(bank_name or "").strip() or None
        normalized_branch_name = str(branch_name or "").strip() or None
        normalized_branch_code = str(branch_code or "").strip() or None
        normalized_account = str(account_number or "").strip() or None
        normalized_pix = str(pix_key or "").strip() or None
        normalized_document = str(document_number or "").strip() or None
        normalized_notes = str(notes or "").strip() or None
        normalized_country = str(country_code or _BANK_ACCOUNT_COUNTRY_BY_CURRENCY.get(normalized_currency, "OTHER")).strip().upper()
        if normalized_owner_kind not in _BANK_ACCOUNT_OWNER_KINDS or normalized_currency not in _CLIENT_BALANCE_CURRENCIES:
            return None
        if normalized_owner_kind in {"cliente", "fornecedor"} and normalized_owner_id <= 0:
            return None
        if not normalized_label or not normalized_holder or normalized_country not in {"SR", "BR", "OTHER"}:
            return None
        if normalized_country == "SR" and (not normalized_bank or not normalized_account):
            return None
        if normalized_country == "BR" and (not normalized_bank or not (normalized_pix or normalized_account)):
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "owner_kind": normalized_owner_kind,
            "owner_id": None if normalized_owner_kind == "empresa" else normalized_owner_id,
            "currency_code": normalized_currency,
            "country_code": normalized_country,
            "label": normalized_label,
            "holder_name": normalized_holder,
            "bank_name": normalized_bank,
            "branch_name": normalized_branch_name,
            "branch_code": normalized_branch_code,
            "account_number": normalized_account,
            "pix_key": normalized_pix,
            "document_number": normalized_document,
            "notes": normalized_notes,
            "metadata": metadata or {},
            "is_default": bool(is_default),
            "active": True,
            "created_by_phone": str(created_by_phone or "").strip() or None,
            "criado_em": now_iso,
            "atualizado_em": now_iso,
        }
        try:
            if payload["is_default"]:
                reset_query = self.client.table("saved_bank_accounts").update({"is_default": False, "atualizado_em": now_iso}).eq("owner_kind", normalized_owner_kind).eq("currency_code", normalized_currency).eq("active", True)
                if normalized_owner_kind == "empresa":
                    reset_query = reset_query.is_("owner_id", "null")
                else:
                    reset_query = reset_query.eq("owner_id", payload["owner_id"])
                reset_query.execute()
            response = self.client.table("saved_bank_accounts").insert(payload).execute()
            rows = cast(List[Dict[str, Any]], response.data or [])
            self._invalidate_saved_bank_accounts_cache()
            return rows[0] if rows else None
        except Exception as exc:
            logger.warning("Falha ao criar conta bancaria salva %s/%s: %s", normalized_owner_kind, normalized_currency, exc)
            return None
