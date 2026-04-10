import base64
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastapi import HTTPException


def build_runtime_saas_auth_helpers(
    *,
    session_ttl_seconds: int,
    session_cookie: str,
    cookie_secure: bool,
    auth_user_cache: Dict[str, Dict[str, Any]],
    auth_user_cache_ttl_seconds: int,
) -> SimpleNamespace:
    def validate_web_pin_format(pin: str) -> str:
        normalized = str(pin or "").strip()
        if not re.fullmatch(r"\d{4,12}", normalized):
            raise HTTPException(status_code=400, detail="PIN web deve ter entre 4 e 12 dígitos numéricos")
        return normalized

    def get_saas_session_secret() -> str:
        return (
            os.getenv("SAAS_SESSION_SECRET")
            or os.getenv("WEBHOOK_TOKEN")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
            or "caixa-saas-dev-secret"
        )

    def encode_saas_session(telefone: str) -> str:
        expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=session_ttl_seconds)).timestamp())
        payload = f"{telefone}|{expires_at}"
        payload_token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
        signature = hmac.new(
            get_saas_session_secret().encode("utf-8"),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload_token}.{signature}"

    def decode_saas_session(raw_cookie: Optional[str]) -> Optional[str]:
        if not raw_cookie or "." not in raw_cookie:
            return None
        payload_token, signature = raw_cookie.rsplit(".", 1)
        expected_signature = hmac.new(
            get_saas_session_secret().encode("utf-8"),
            payload_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        try:
            payload = base64.urlsafe_b64decode(payload_token.encode("ascii")).decode("utf-8")
            telefone, expires_at_raw = payload.split("|", 1)
            if int(expires_at_raw) < int(datetime.now(timezone.utc).timestamp()):
                return None
            return telefone
        except Exception:
            return None

    def set_saas_session(response: Any, telefone: str) -> None:
        response.set_cookie(
            key=session_cookie,
            value=encode_saas_session(telefone),
            httponly=True,
            secure=cookie_secure,
            samesite="lax",
            max_age=session_ttl_seconds,
            path="/",
        )

    def clear_saas_session(response: Any) -> None:
        response.delete_cookie(key=session_cookie, path="/")

    def get_saas_authenticated_user_cached(telefone: str) -> Optional[Dict[str, Any]]:
        cached = auth_user_cache.get(str(telefone or ""))
        if not cached:
            return None
        expires_at = cached.get("expires_at")
        user = cached.get("user")
        if not isinstance(expires_at, datetime) or expires_at <= datetime.now(timezone.utc) or not isinstance(user, dict):
            auth_user_cache.pop(str(telefone or ""), None)
            return None
        return dict(user)

    def set_saas_authenticated_user_cached(telefone: str, user: Dict[str, Any]) -> Dict[str, Any]:
        normalized_phone = str(telefone or "")
        cached_user = dict(user)
        auth_user_cache[normalized_phone] = {
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=auth_user_cache_ttl_seconds),
            "user": cached_user,
        }
        return dict(cached_user)

    def invalidate_saas_authenticated_user_cache(telefone: Optional[str] = None) -> None:
        if telefone is None:
            auth_user_cache.clear()
            return
        auth_user_cache.pop(str(telefone or ""), None)

    def get_saas_authenticated_user(request: Any, db: Any) -> Optional[Dict[str, Any]]:
        telefone = decode_saas_session(request.cookies.get(session_cookie))
        if not telefone:
            return None
        cached = get_saas_authenticated_user_cached(telefone)
        if cached is not None:
            return cached
        usuario = db.get_usuario_web_auth(telefone)
        if not usuario:
            return None
        enriched = dict(usuario)
        enriched["web_pin_bootstrap_required"] = not bool(enriched.get("web_pin_hash"))
        return set_saas_authenticated_user_cached(telefone, enriched)

    return SimpleNamespace(
        validate_web_pin_format=validate_web_pin_format,
        get_saas_session_secret=get_saas_session_secret,
        encode_saas_session=encode_saas_session,
        decode_saas_session=decode_saas_session,
        set_saas_session=set_saas_session,
        clear_saas_session=clear_saas_session,
        get_saas_authenticated_user_cached=get_saas_authenticated_user_cached,
        set_saas_authenticated_user_cached=set_saas_authenticated_user_cached,
        invalidate_saas_authenticated_user_cache=invalidate_saas_authenticated_user_cache,
        get_saas_authenticated_user=get_saas_authenticated_user,
    )