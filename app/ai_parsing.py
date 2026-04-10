import re
import unicodedata
from typing import Any, Dict, cast

from app.ai_lexicon import LEXICON, VALID_ATIVOS, VALID_INTENCOES


def _normalize_text(value: str) -> str:
    lowered = value.strip().lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _find_ativo(text: str) -> str:
    aliases = cast(Dict[str, str], LEXICON.get("ativo_aliases", {}))
    for token in re.split(r"[^a-zA-Z]+", text):
        if token and token in aliases:
            return aliases[token]
    return ""


def _extract_first_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:[\.,]\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def normalize_ativo_value(value: Any) -> str | None:
    if value is None:
        return None
    text = _normalize_text(str(value))
    if not text:
        return None
    aliases = cast(Dict[str, str], LEXICON.get("ativo_aliases", {}))
    resolved = aliases.get(text, text)
    return resolved if resolved in VALID_ATIVOS else None


def _contains_any_token(text: str, words: set[str]) -> bool:
    tokens = set(re.split(r"[^a-zA-Z]+", text))
    return bool(tokens.intersection(words))


def sanitize_extracted_payload(message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    text = _normalize_text(message)
    intencao = _normalize_text(str(payload.get("intencao", "conversar")))
    if intencao not in VALID_INTENCOES:
        intencao = "conversar"

    ativo = normalize_ativo_value(payload.get("ativo"))
    quantidade = _to_float_or_none(payload.get("quantidade"))
    valor_informado = _to_float_or_none(payload.get("valor_informado"))
    if quantidade is not None and quantidade <= 0:
        quantidade = None
    if valor_informado is not None and valor_informado <= 0:
        valor_informado = None

    buy_words = set(cast(list[str], LEXICON.get("buy_words", [])))
    sell_words = set(cast(list[str], LEXICON.get("sell_words", [])))
    exchange_words = set(cast(list[str], LEXICON.get("exchange_words", [])))
    rate_words = set(cast(list[str], LEXICON.get("rate_words", [])))
    report_words = set(cast(list[str], LEXICON.get("report_words", [])))
    has_op_signal = _contains_any_token(text, buy_words.union(sell_words).union(exchange_words))
    has_rate_signal = _contains_any_token(text, rate_words)
    has_report_signal = _contains_any_token(text, report_words)

    if intencao == "consultar_relatorio":
        if not has_report_signal and "caixa" not in text and "extrato" not in text:
            intencao = "conversar"
        return {"intencao": intencao, "ativo": None, "quantidade": None, "valor_informado": None, "resposta": payload.get("resposta")}

    if intencao == "atualizar_taxa":
        resolved_value = valor_informado if valor_informado is not None else quantidade
        if not has_rate_signal or not ativo or resolved_value is None:
            return {"intencao": "conversar", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": "Para atualizar taxa, envie no formato: Taxa ouro 70.00"}
        return {"intencao": "atualizar_taxa", "ativo": ativo, "quantidade": None, "valor_informado": resolved_value, "resposta": None}

    if intencao == "registrar_operacao":
        if not has_op_signal or not ativo or quantidade is None:
            return {
                "intencao": "conversar",
                "ativo": None,
                "quantidade": None,
                "valor_informado": None,
                "resposta": "Para registrar operação, use termos do sistema, por exemplo: 'compra ouro 2g', 'venda ouro 1.5g' ou apenas 'compra' para iniciar o fluxo guiado.",
            }
        return {"intencao": "registrar_operacao", "ativo": ativo, "quantidade": quantidade, "valor_informado": valor_informado, "resposta": None}

    return {"intencao": "conversar", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": payload.get("resposta")}


def heuristic_extract(message: str) -> Dict[str, Any]:
    text = _normalize_text(message)
    rate_words = set(cast(list[str], LEXICON.get("rate_words", [])))
    buy_words = set(cast(list[str], LEXICON.get("buy_words", [])))
    sell_words = set(cast(list[str], LEXICON.get("sell_words", [])))
    exchange_words = set(cast(list[str], LEXICON.get("exchange_words", [])))
    report_words = set(cast(list[str], LEXICON.get("report_words", [])))
    ativo = _find_ativo(text)
    number = _extract_first_number(text)
    tokens = set(re.split(r"[^a-zA-Z]+", text))

    if tokens.intersection(rate_words):
        if ativo and number is not None:
            return {"intencao": "atualizar_taxa", "ativo": ativo, "quantidade": None, "valor_informado": number, "resposta": None}
        return {"intencao": "conversar", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": "Entendi pedido de taxa, mas preciso do ativo e valor. Ex.: 'Taxa ouro 70.00'."}

    if tokens.intersection(report_words):
        return {"intencao": "consultar_relatorio", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": None}

    if tokens.intersection(buy_words.union(sell_words).union(exchange_words)):
        if ativo and number is not None:
            return {"intencao": "registrar_operacao", "ativo": ativo, "quantidade": number, "valor_informado": None, "resposta": None}
        return {"intencao": "conversar", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": "Entendi a operação, mas preciso de quantidade e ativo. Ex.: 'compra ouro 2g' ou 'venda ouro 1g'."}

    return {
        "intencao": "conversar",
        "ativo": None,
        "quantidade": None,
        "valor_informado": None,
        "resposta": "Atendimento disponível em português, inglês e espanhol. Você pode usar termos do sistema, por exemplo: 'compra', 'venda', 'caixa', 'extrato' ou 'taxa ouro 70.00'.",
    }


_normalize_ativo_value = normalize_ativo_value
_sanitize_extracted_payload = sanitize_extracted_payload
_heuristic_extract = heuristic_extract


__all__ = [
    "heuristic_extract",
    "normalize_ativo_value",
    "sanitize_extracted_payload",
    "_heuristic_extract",
    "_normalize_ativo_value",
    "_sanitize_extracted_payload",
]