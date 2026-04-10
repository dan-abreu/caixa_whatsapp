import logging
import os
from html import escape
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import ValidationError


def _twiml_message(text: str) -> Response:
    safe_text = escape(text)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe_text}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _twiml_empty_response() -> Response:
    xml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=xml, media_type="application/xml")


def _should_suppress_twilio_reply(message: str) -> bool:
    mode = os.getenv("TWILIO_REPLY_MODE", "normal").strip().lower()
    if mode == "silent_all":
        return True
    if mode != "silent_prefix":
        return False

    prefix = os.getenv("TWILIO_SILENT_PREFIX", "debug:").strip().lower()
    if not prefix:
        return False
    return message.strip().lower().startswith(prefix)


def register_webhook_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    validate_webhook_token: Callable[[Optional[str]], None],
    whatsapp_payload_cls: Any,
    processar_webhook: Callable[[Any, Any, Optional[str]], Dict[str, Any]],
    idempotency_cache: Dict[str, Dict[str, Any]],
    friendly_errors: Dict[int, str],
    parse_query_string: Callable[[str], Dict[str, Any]],
    logger: logging.Logger,
) -> None:
    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(
        request: Request,
        x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
        x_provider_message_id: Optional[str] = Header(default=None, alias="X-Provider-Message-Id"),
        x_twilio_message_sid: Optional[str] = Header(default=None, alias="X-Twilio-MessageSid"),
    ) -> Dict[str, Any]:
        provider_message_id = x_provider_message_id or x_twilio_message_sid
        raw_text = ""
        body_data: Dict[str, Any] = {}

        try:
            raw_text = (await request.body()).decode("utf-8", errors="ignore")
        except Exception:
            pass

        try:
            raw_body = await request.json()
            if isinstance(raw_body, dict):
                body_data = raw_body
        except Exception:
            body_data = {}

        if not body_data:
            try:
                body_data = dict(await request.form())
            except Exception:
                body_data = {}

        if not body_data:
            try:
                body_data = parse_query_string(raw_text)
            except Exception:
                body_data = {}

        try:
            payload = whatsapp_payload_cls(
                remetente=str(body_data.get("remetente") or body_data.get("From") or "").strip(),
                mensagem=str(body_data.get("mensagem") or body_data.get("Body") or "").strip(),
            )
        except ValidationError:
            raise HTTPException(status_code=400, detail="Mensagem inválida")

        token = x_webhook_token or request.query_params.get("token") or body_data.get("token")
        provider_message_id = provider_message_id or str(body_data.get("provider_message_id") or "").strip() or str(body_data.get("MessageSid") or "").strip() or None
        remetente = payload.remetente.strip().replace("whatsapp:", "")
        mensagem = payload.mensagem.strip()
        db: Optional[Any] = None

        try:
            validate_webhook_token(str(token) if token is not None else None)
            db = get_db()

            if provider_message_id:
                existing = db.get_processed_message(provider_message_id)
                if existing and isinstance(existing.get("resposta_payload"), dict):
                    return existing["resposta_payload"]
                cached = idempotency_cache.get(provider_message_id)
                if cached:
                    return cached

            response_payload = processar_webhook(payload, db, provider_message_id)
            if db and provider_message_id:
                db.save_processed_message(provider_message_id=provider_message_id, remetente=remetente, mensagem_recebida=mensagem, resposta_payload=response_payload, status_code=200)
                idempotency_cache[provider_message_id] = response_payload
            return response_payload
        except HTTPException as exc:
            response = {
                "mensagem": f"⚠️ {friendly_errors.get(exc.status_code, 'Não consegui processar. Envie: menu')}",
                "dados": {"erro": exc.status_code, "detalhe": exc.detail},
            }
            if db and provider_message_id:
                db.save_processed_message(provider_message_id=provider_message_id, remetente=remetente, mensagem_recebida=mensagem, resposta_payload=response, status_code=exc.status_code)
                idempotency_cache[provider_message_id] = response
            return response
        except Exception:
            logger.exception("Erro inesperado no webhook")
            response = {"mensagem": "⚠️ Erro inesperado. Tente novamente.", "dados": {"erro": 500}}
            if db and provider_message_id:
                db.save_processed_message(provider_message_id=provider_message_id, remetente=remetente, mensagem_recebida=mensagem, resposta_payload=response, status_code=500)
                idempotency_cache[provider_message_id] = response
            return response

    @app.post("/webhook/twilio")
    async def whatsapp_webhook_twilio(
        request: Request,
        x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
        x_twilio_message_sid: Optional[str] = Header(default=None, alias="X-Twilio-MessageSid"),
    ) -> Response:
        try:
            raw = (await request.body()).decode("utf-8", errors="ignore")
            body_data = parse_query_string(raw)
        except Exception:
            body_data = {}

        token = x_webhook_token or request.query_params.get("token") or body_data.get("token")
        provider_message_id = x_twilio_message_sid or str(body_data.get("MessageSid") or "").strip() or None
        remetente = str(body_data.get("From") or "").strip().replace("whatsapp:", "")
        mensagem = str(body_data.get("Body") or "").strip()

        if not remetente or not mensagem:
            return _twiml_message("⚠️ Mensagem inválida. Tente novamente.")

        payload = whatsapp_payload_cls(remetente=remetente, mensagem=mensagem)
        suppress_reply = _should_suppress_twilio_reply(mensagem)
        db: Optional[Any] = None

        try:
            validate_webhook_token(str(token) if token is not None else None)
            db = get_db()

            if provider_message_id:
                existing = db.get_processed_message(provider_message_id)
                if existing and isinstance(existing.get("resposta_payload"), dict):
                    if suppress_reply:
                        return _twiml_empty_response()
                    return _twiml_message(str(existing["resposta_payload"].get("mensagem") or ""))
                cached = idempotency_cache.get(provider_message_id)
                if cached:
                    if suppress_reply:
                        return _twiml_empty_response()
                    return _twiml_message(str(cached.get("mensagem") or ""))

            response_payload = processar_webhook(payload, db, provider_message_id)
            if db and provider_message_id:
                db.save_processed_message(provider_message_id=provider_message_id, remetente=remetente, mensagem_recebida=mensagem, resposta_payload=response_payload, status_code=200)
                idempotency_cache[provider_message_id] = response_payload
            if suppress_reply:
                return _twiml_empty_response()
            return _twiml_message(str(response_payload.get("mensagem") or "Operação processada."))
        except HTTPException as exc:
            response_payload = {
                "mensagem": f"⚠️ {friendly_errors.get(exc.status_code, 'Não consegui processar. Envie: menu')}",
                "dados": {"erro": exc.status_code, "detalhe": exc.detail},
            }
            if db and provider_message_id:
                db.save_processed_message(provider_message_id=provider_message_id, remetente=remetente, mensagem_recebida=mensagem, resposta_payload=response_payload, status_code=exc.status_code)
                idempotency_cache[provider_message_id] = response_payload
            if suppress_reply:
                return _twiml_empty_response()
            return _twiml_message(response_payload["mensagem"])
        except Exception:
            logger.exception("Erro inesperado no webhook Twilio")
            if suppress_reply:
                return _twiml_empty_response()
            return _twiml_message("⚠️ Erro inesperado. Tente novamente.")