import re
from types import SimpleNamespace
from typing import Callable


def build_whatsapp_message_pattern_helpers(*, normalize_text: Callable[[str], str]) -> SimpleNamespace:
    def is_help_menu_request(message: str) -> bool:
        text = normalize_text(message)
        keywords = [
            "menu",
            "ajuda",
            "help",
            "comandos",
            "o que voce pode fazer",
            "o que você pode fazer",
            "como funciona",
            "funcionalidades",
        ]
        return any(keyword in text for keyword in keywords)

    def is_greeting(message: str) -> bool:
        text = normalize_text(message)
        compact = re.sub(r"[^a-z0-9\s]", " ", text)
        compact = re.sub(r"\s+", " ", compact).strip()
        if re.match(r"^o+i+$", compact):
            return True
        if re.match(r"^o+l+a+$", compact):
            return True
        if compact.startswith("bom dia") or compact.startswith("boa tarde") or compact.startswith("boa noite"):
            return True
        return compact in {"hello", "hi", "hey"}

    def looks_like_new_operation_start(message: str) -> bool:
        text = normalize_text(message)
        operation_tokens = [
            "comprei",
            "comprar",
            "compra",
            "vendi",
            "vender",
            "venda",
            "cambio",
            "cambio",
            "troca",
        ]
        has_operation_word = any(token in text for token in operation_tokens)
        has_asset_or_amount = ("ouro" in text) or bool(re.search(r"\d", text))
        return has_operation_word and has_asset_or_amount

    def should_reset_guided_session_for_message(message: str) -> bool:
        text = normalize_text(message)
        if looks_like_new_operation_start(message) or is_greeting(message):
            return True
        global_commands = ["menu", "caixa", "extrato", "ajuda", "help", "taxa", "relatorio", "relatório"]
        return any(text.startswith(command) for command in global_commands)

    def sanitize_nome(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip()
        return cleaned[:80]

    return SimpleNamespace(
        is_help_menu_request=is_help_menu_request,
        is_greeting=is_greeting,
        looks_like_new_operation_start=looks_like_new_operation_start,
        should_reset_guided_session_for_message=should_reset_guided_session_for_message,
        sanitize_nome=sanitize_nome,
    )