import json
import logging
import os
import re
from typing import Any, Dict, cast

import requests

from app.ai_parsing import heuristic_extract, normalize_ativo_value, sanitize_extracted_payload
from app.ai_prompt import SYSTEM_PROMPT


logger = logging.getLogger("caixa_whatsapp")


class AIServiceError(Exception):
    pass


def _extract_json_blob(text: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise AIServiceError("A IA não retornou JSON válido.")

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIServiceError(f"Falha ao parsear JSON da IA: {exc}") from exc
    if not isinstance(data, dict):
        raise AIServiceError("JSON retornado pela IA não é um objeto.")
    if "intencao" not in data or "ativo" not in data:
        raise AIServiceError("JSON da IA sem campos obrigatórios: intencao e ativo.")
    return cast(Dict[str, Any], data)


def extract_message_data(message: str) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not api_key:
        raise AIServiceError("GEMINI_API_KEY não configurada.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": message}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }

    def _fallback(reason: str, exc: Exception | None = None) -> Dict[str, Any]:
        if exc is None:
            logger.warning("Usando fallback heuristico da IA: %s", reason)
        else:
            logger.warning("Usando fallback heuristico da IA: %s: %s", reason, exc)
        return sanitize_extracted_payload(message, heuristic_extract(message))

    try:
        response = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as exc:
        return _fallback("falha na requisicao ao modelo", exc)
    if response.status_code >= 400:
        return _fallback(f"status HTTP {response.status_code}")

    try:
        body = cast(Dict[str, Any], response.json())
        text = str(body["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return _fallback("resposta do modelo fora do formato esperado", exc)

    try:
        return sanitize_extracted_payload(message, _extract_json_blob(text))
    except AIServiceError as exc:
        return _fallback("json retornado pela IA invalido", exc)


_normalize_ativo_value = normalize_ativo_value
_sanitize_extracted_payload = sanitize_extracted_payload


__all__ = [
    "AIServiceError",
    "SYSTEM_PROMPT",
    "_normalize_ativo_value",
    "_sanitize_extracted_payload",
    "extract_message_data",
]