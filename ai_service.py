import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, cast

import requests


SYSTEM_PROMPT = """
Você é um assistente de caixa financeiro. Leia a mensagem do usuário e responda APENAS com JSON válido.

REGRAS CRÍTICAS:
- Entenda linguagem formal, informal, gírias, abreviações e pequenos erros de digitação.
- Entenda mensagens em múltiplos idiomas (ex.: português, inglês, espanhol, francês e holandês).
- Não invente valores ausentes.
- Se não houver dados suficientes para operação/taxa, use intencao=conversar e peça esclarecimento em 'resposta'.

Se a mensagem for sobre atualizar a taxa de um ativo (ex: "Taxa ouro 68.50"):
{
  "intencao": "atualizar_taxa",
  "ativo": "string",
  "quantidade": null,
  "valor_informado": float,
  "resposta": null
}

Se a mensagem for sobre registrar uma operação de compra, venda ou câmbio (ex: "Comprei 2g de ouro a 105", "Vendi 3g ouro a 70 USD"):
{
  "intencao": "registrar_operacao",
  "ativo": "string",
  "quantidade": float,
  "valor_informado": float ou null (se houver preço/taxa informado, ex: "a 105 euros", "a 5.30")
  "resposta": null
}

Se a mensagem for sobre extrato, saldo, caixa, relatório ou fechamento (ex: "quero ver meu caixa", "extrato", "resumo de hoje"):
{
  "intencao": "consultar_relatorio",
  "ativo": null,
  "quantidade": null,
  "valor_informado": null,
  "resposta": null
}

Para qualquer outra mensagem (saudações, perguntas, dúvidas ou conversa geral):
{
  "intencao": "conversar",
  "ativo": null,
  "quantidade": null,
  "valor_informado": null,
  "resposta": "sua resposta amigável e útil aqui"
}

Mapeie variações para ativos quando possível:
- ouro/gold/oro/or -> ouro
- usd/dollar/dólar/dolar -> usd
- eur/euro -> eur
- srd -> srd

Não faça cálculos financeiros. Apenas devolva o JSON.
"""


class AIServiceError(Exception):
    pass


_DEFAULT_LEXICON: Dict[str, Any] = {
    "ativo_aliases": {
        "ouro": "ouro",
        "gold": "ouro",
        "oro": "ouro",
        "or": "ouro",
        "usd": "usd",
        "dolar": "usd",
        "dollar": "usd",
        "dollars": "usd",
        "eur": "eur",
        "euro": "eur",
        "euros": "eur",
        "srd": "srd",
    },
    "rate_words": [
        "taxa",
        "rate",
        "precio",
        "prix",
        "koers",
        "cotacao",
        "cotation",
    ],
    "buy_words": [
        "comprei",
        "comprar",
        "compra",
        "buy",
        "bought",
        "purchase",
        "acheter",
        "achete",
        "comprado",
    ],
    "sell_words": [
        "vendi",
        "vender",
        "venda",
        "sell",
        "sold",
        "vente",
        "vendre",
    ],
    "exchange_words": [
        "cambio",
        "troca",
        "exchange",
        "fx",
        "wissel",
        "change",
    ],
    "report_words": [
        "extrato",
        "caixa",
        "saldo",
        "relatorio",
        "resumo",
        "fechamento",
        "balanco",
        "statement",
        "report",
        "summary",
        "balance",
        "ledger",
    ],
}


def _load_lexicon() -> Dict[str, Any]:
    lexicon = dict(_DEFAULT_LEXICON)
    lexicon_path = os.getenv("AI_LEXICON_PATH")
    if lexicon_path:
        path = Path(lexicon_path)
    else:
        path = Path(__file__).with_name("ai_intents_lexicon.json")

    if not path.exists():
        return lexicon

    try:
        external = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return lexicon

    if not isinstance(external, dict):
        return lexicon

    external_dict = cast(Dict[str, Any], external)

    for key in ["rate_words", "buy_words", "sell_words", "exchange_words", "report_words"]:
        value = external_dict.get(key)
        if isinstance(value, list):
            # Merge while preserving order.
            merged = list(dict.fromkeys([str(v).lower() for v in (lexicon.get(key, []) + value)]))
            lexicon[key] = merged

    aliases = external_dict.get("ativo_aliases")
    if isinstance(aliases, dict):
        aliases_dict = cast(Dict[str, Any], aliases)
        merged_aliases = dict(lexicon.get("ativo_aliases", {}))
        for k, v in aliases_dict.items():
            merged_aliases[str(k).lower()] = str(v).lower()
        lexicon["ativo_aliases"] = merged_aliases

    return lexicon


_LEXICON = _load_lexicon()
_VALID_INTENCOES = {"atualizar_taxa", "registrar_operacao", "consultar_relatorio", "conversar"}
_VALID_ATIVOS = {"ouro", "usd", "eur", "srd"}


def _normalize_text(value: str) -> str:
    lowered = value.strip().lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _find_ativo(text: str) -> str:
    aliases = cast(Dict[str, str], _LEXICON.get("ativo_aliases", {}))
    for token in re.split(r"[^a-zA-Z]+", text):
        if not token:
            continue
        if token in aliases:
            return aliases[token]
    return ""


def _extract_first_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:[\.,]\d+)?", text)
    if not match:
        return None
    raw = match.group(0).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return parsed


def _normalize_ativo_value(value: Any) -> str | None:
    if value is None:
        return None
    text = _normalize_text(str(value))
    if not text:
        return None
    aliases = cast(Dict[str, str], _LEXICON.get("ativo_aliases", {}))
    resolved = aliases.get(text, text)
    return resolved if resolved in _VALID_ATIVOS else None


def _contains_any_token(text: str, words: set[str]) -> bool:
    tokens = set(re.split(r"[^a-zA-Z]+", text))
    return bool(tokens.intersection(words))


def _sanitize_extracted_payload(message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize AI payload and reject unsupported/ambiguous operational actions."""
    text = _normalize_text(message)
    intencao = _normalize_text(str(payload.get("intencao", "conversar")))
    if intencao not in _VALID_INTENCOES:
        intencao = "conversar"

    ativo = _normalize_ativo_value(payload.get("ativo"))
    quantidade = _to_float_or_none(payload.get("quantidade"))
    valor_informado = _to_float_or_none(payload.get("valor_informado"))

    if quantidade is not None and quantidade <= 0:
        quantidade = None
    if valor_informado is not None and valor_informado <= 0:
        valor_informado = None

    buy_words = set(cast(list[str], _LEXICON.get("buy_words", [])))
    sell_words = set(cast(list[str], _LEXICON.get("sell_words", [])))
    exchange_words = set(cast(list[str], _LEXICON.get("exchange_words", [])))
    rate_words = set(cast(list[str], _LEXICON.get("rate_words", [])))
    report_words = set(cast(list[str], _LEXICON.get("report_words", [])))

    has_op_signal = _contains_any_token(text, buy_words.union(sell_words).union(exchange_words))
    has_rate_signal = _contains_any_token(text, rate_words)
    has_report_signal = _contains_any_token(text, report_words)

    if intencao == "consultar_relatorio":
        if not has_report_signal and "caixa" not in text and "extrato" not in text:
            intencao = "conversar"
        return {
            "intencao": intencao,
            "ativo": None,
            "quantidade": None,
            "valor_informado": None,
            "resposta": payload.get("resposta"),
        }

    if intencao == "atualizar_taxa":
        resolved_value = valor_informado if valor_informado is not None else quantidade
        if not has_rate_signal or not ativo or resolved_value is None:
            return {
                "intencao": "conversar",
                "ativo": None,
                "quantidade": None,
                "valor_informado": None,
                "resposta": "Para atualizar taxa, envie no formato: Taxa ouro 70.00",
            }
        return {
            "intencao": "atualizar_taxa",
            "ativo": ativo,
            "quantidade": None,
            "valor_informado": resolved_value,
            "resposta": None,
        }

    if intencao == "registrar_operacao":
        if not has_op_signal or not ativo or quantidade is None:
            return {
                "intencao": "conversar",
                "ativo": None,
                "quantidade": None,
                "valor_informado": None,
                "resposta": "Para registrar operação, envie algo como: Comprei 2g de ouro a 105",
            }
        return {
            "intencao": "registrar_operacao",
            "ativo": ativo,
            "quantidade": quantidade,
            "valor_informado": valor_informado,
            "resposta": None,
        }

    return {
        "intencao": "conversar",
        "ativo": None,
        "quantidade": None,
        "valor_informado": None,
        "resposta": payload.get("resposta"),
    }


def _heuristic_extract(message: str) -> Dict[str, Any]:
    text = _normalize_text(message)

    rate_words = set(cast(list[str], _LEXICON.get("rate_words", [])))
    buy_words = set(cast(list[str], _LEXICON.get("buy_words", [])))
    sell_words = set(cast(list[str], _LEXICON.get("sell_words", [])))
    exchange_words = set(cast(list[str], _LEXICON.get("exchange_words", [])))
    report_words = set(cast(list[str], _LEXICON.get("report_words", [])))

    ativo = _find_ativo(text)
    number = _extract_first_number(text)
    tokens = set(re.split(r"[^a-zA-Z]+", text))

    if tokens.intersection(rate_words):
        if ativo and number is not None:
            return {
                "intencao": "atualizar_taxa",
                "ativo": ativo,
                "quantidade": None,
                "valor_informado": number,
                "resposta": None,
            }
        return {
            "intencao": "conversar",
            "ativo": None,
            "quantidade": None,
            "valor_informado": None,
            "resposta": "Entendi pedido de taxa, mas preciso do ativo e valor. Ex.: 'Taxa ouro 70.00'.",
        }

    # Report queries (statement/cash summary) should not fall through to generic chat.
    if tokens.intersection(report_words):
        return {
            "intencao": "consultar_relatorio",
            "ativo": None,
            "quantidade": None,
            "valor_informado": None,
            "resposta": None,
        }

    if tokens.intersection(buy_words.union(sell_words).union(exchange_words)):
        if ativo and number is not None:
            return {
                "intencao": "registrar_operacao",
                "ativo": ativo,
                "quantidade": number,
                "valor_informado": None,
                "resposta": None,
            }
        return {
            "intencao": "conversar",
            "ativo": None,
            "quantidade": None,
            "valor_informado": None,
            "resposta": "Entendi operação, mas preciso de quantidade e ativo. Ex.: 'Comprei 2g de ouro'.",
        }

    return {
        "intencao": "conversar",
        "ativo": None,
        "quantidade": None,
        "valor_informado": None,
        "resposta": "Posso ajudar em português, inglês e espanhol. Diga, por exemplo: 'Comprei 2g de ouro' ou 'Taxa USD 5.40'.",
    }


def _extract_json_blob(text: str) -> Dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise AIServiceError("A IA não retornou JSON válido.")

    json_text = match.group(0)
    try:
        data = json.loads(json_text)
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
        "systemInstruction": {
            "parts": [
                {
                    "text": SYSTEM_PROMPT,
                }
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": message,
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
    except requests.RequestException:
        return _sanitize_extracted_payload(message, _heuristic_extract(message))

    if response.status_code >= 400:
        return _sanitize_extracted_payload(message, _heuristic_extract(message))

    body: Dict[str, Any] = {}
    text = ""
    try:
        body = cast(Dict[str, Any], response.json())
        text = str(body["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return _sanitize_extracted_payload(message, _heuristic_extract(message))

    try:
        extracted = _extract_json_blob(text)
        return _sanitize_extracted_payload(message, extracted)
    except AIServiceError:
        return _sanitize_extracted_payload(message, _heuristic_extract(message))
