import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, cast


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
    "rate_words": ["taxa", "rate", "precio", "prix", "koers", "cotacao", "cotation"],
    "buy_words": ["comprei", "comprar", "compra", "buy", "bought", "purchase", "acheter", "achete", "comprado"],
    "sell_words": ["vendi", "vender", "venda", "sell", "sold", "vente", "vendre"],
    "exchange_words": ["cambio", "troca", "exchange", "fx", "wissel", "change"],
    "report_words": ["extrato", "caixa", "saldo", "relatorio", "resumo", "fechamento", "balanco", "statement", "report", "summary", "balance", "ledger"],
}

_LEXICON_SECTION_FILES = {
    "ativo_aliases": "ativo_aliases.json",
    "rate_words": "rate_words.json",
    "buy_words": "buy_words.json",
    "sell_words": "sell_words.json",
    "exchange_words": "exchange_words.json",
    "report_words": "report_words.json",
}

_WORD_SECTION_KEYS = [
    "rate_words",
    "buy_words",
    "sell_words",
    "exchange_words",
    "report_words",
]


def _merge_word_section(current_words: Iterable[Any], external_words: Iterable[Any]) -> list[str]:
    merged_words = [str(value).lower() for value in current_words]
    merged_words.extend(str(value).lower() for value in external_words)
    return list(dict.fromkeys(merged_words))


def _merge_alias_section(current_aliases: Dict[str, Any], external_aliases: Dict[str, Any]) -> Dict[str, str]:
    merged_aliases = {str(key).lower(): str(value).lower() for key, value in current_aliases.items()}
    for key, value in external_aliases.items():
        merged_aliases[str(key).lower()] = str(value).lower()
    return merged_aliases


def _merge_lexicon_sections(lexicon: Dict[str, Any], external_dict: Dict[str, Any]) -> Dict[str, Any]:
    for key in _WORD_SECTION_KEYS:
        value = external_dict.get(key)
        if isinstance(value, list):
            lexicon[key] = _merge_word_section(lexicon.get(key, []), value)

    aliases = external_dict.get("ativo_aliases")
    if isinstance(aliases, dict):
        aliases_dict = cast(Dict[str, Any], aliases)
        lexicon["ativo_aliases"] = _merge_alias_section(
            cast(Dict[str, Any], lexicon.get("ativo_aliases", {})),
            aliases_dict,
        )

    return lexicon


def _load_lexicon_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        external = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(external, dict):
        return None
    return cast(Dict[str, Any], external)


def _load_lexicon_directory(path: Path) -> Dict[str, Any]:
    external_sections: Dict[str, Any] = {}
    for key, filename in _LEXICON_SECTION_FILES.items():
        section_path = path / filename
        if not section_path.exists():
            continue
        try:
            external_sections[key] = json.loads(section_path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return external_sections


def _resolve_default_lexicon_path() -> Path:
    legacy_file_path = Path(__file__).with_name("ai_intents_lexicon.json")
    segmented_dir_path = Path(__file__).with_name("ai_lexicon_data")
    if segmented_dir_path.exists():
        return segmented_dir_path
    return legacy_file_path


def _load_lexicon() -> Dict[str, Any]:
    lexicon = dict(_DEFAULT_LEXICON)
    lexicon_path = os.getenv("AI_LEXICON_PATH")
    path = Path(lexicon_path) if lexicon_path else _resolve_default_lexicon_path()

    if not path.exists():
        return lexicon

    external_dict = _load_lexicon_directory(path) if path.is_dir() else _load_lexicon_file(path)
    if not external_dict:
        return lexicon

    return _merge_lexicon_sections(lexicon, external_dict)


LEXICON = _load_lexicon()
VALID_INTENCOES = {"atualizar_taxa", "registrar_operacao", "consultar_relatorio", "conversar"}
VALID_ATIVOS = {"ouro", "usd", "eur", "srd"}