import re
from types import SimpleNamespace
from typing import Callable, List, Optional, Tuple


def build_whatsapp_input_parser_helpers(*, normalize_text: Callable[[str], str]) -> SimpleNamespace:
    def extract_confirmacao(value: str) -> Optional[bool]:
        text = normalize_text(value)
        if text in {"sim", "confirmar", "ok", "confirmo", "s", "1"}:
            return True
        if text in {"nao", "não", "cancelar", "n", "cancela", "2"}:
            return False
        return None

    def parse_single_currency_choice(value: str) -> Optional[str]:
        text = normalize_text(value)
        number_map = {"1": "USD", "2": "EUR", "3": "SRD", "4": "BRL"}
        if text in number_map:
            return number_map[text]

        aliases = {
            "usd": "USD",
            "dolar": "USD",
            "dolares": "USD",
            "dolar americano": "USD",
            "eur": "EUR",
            "euro": "EUR",
            "euros": "EUR",
            "srd": "SRD",
            "brl": "BRL",
            "real": "BRL",
            "reais": "BRL",
        }
        return aliases.get(text)

    def parse_origem_choice(value: str) -> Optional[str]:
        text = normalize_text(value)
        if text == "1":
            return "balcao"
        if text == "2":
            return "fora"
        if text in {"balcao", "balcão"}:
            return "balcao"
        if text == "fora":
            return "fora"
        return None

    def parse_forma_pagamento_choice(value: str) -> Optional[str]:
        text = normalize_text(value)
        number_map = {
            "1": "dinheiro",
            "2": "transferencia",
            "3": "cheque",
            "4": "misto",
        }
        if text in number_map:
            return number_map[text]
        if text in {"dinheiro", "transferencia", "cheque", "misto"}:
            return text
        return None

    def parse_fechamento_tipo_choice(value: str) -> Optional[str]:
        text = normalize_text(value)
        if text == "1":
            return "total"
        if text == "2":
            return "parcial"
        if text in {"total", "parcial"}:
            return text
        return None

    def extract_moedas(value: str) -> List[str]:
        text = normalize_text(value)
        aliases = {
            "usd": "USD",
            "dolar": "USD",
            "dolares": "USD",
            "srd": "SRD",
            "eur": "EUR",
            "euro": "EUR",
            "euros": "EUR",
            "brl": "BRL",
            "real": "BRL",
            "reais": "BRL",
        }
        found: List[str] = []
        for token in re.split(r"[^a-zA-Z]+", text):
            if not token:
                continue
            moeda = aliases.get(token)
            if moeda and moeda not in found:
                found.append(moeda)
        return found

    def extract_caixa_currency(message: str) -> Optional[str]:
        text = normalize_text(message)
        aliases = {
            "1": "XAU",
            "2": "EUR",
            "3": "USD",
            "4": "SRD",
            "5": "BRL",
            "usd": "USD",
            "dolar": "USD",
            "dolar americano": "USD",
            "eur": "EUR",
            "euro": "EUR",
            "srd": "SRD",
            "brl": "BRL",
            "real": "BRL",
            "reais": "BRL",
            "xau": "XAU",
            "ouro": "XAU",
        }
        if text in aliases:
            return aliases[text]
        for token in re.split(r"[^a-zA-Z0-9]+", text):
            if token in aliases:
                return aliases[token]
        return None

    def parse_operation_id(raw: str) -> Optional[int]:
        text = raw.strip().lower()
        match_op = re.search(r"op-\d{8}-(\d+)", text)
        if match_op:
            return int(match_op.group(1))

        match_num = re.search(r"\b(\d{1,12})\b", text)
        if match_num:
            return int(match_num.group(1))
        return None

    def parse_operation_reference(raw: str) -> Tuple[str, Optional[int]]:
        text = raw.strip().lower()
        if text.startswith("gt-"):
            return "gold", parse_operation_id(text)
        if text.startswith("t-") or text.startswith("op-"):
            return "transacao", parse_operation_id(text)
        return "transacao", parse_operation_id(text)

    def normalize_edit_field(raw: str) -> Optional[str]:
        field = normalize_text(raw)
        aliases = {
            "preco": "cotacao_usada",
            "preço": "cotacao_usada",
            "cotacao": "cotacao_usada",
            "cotacao_usada": "cotacao_usada",
            "quantidade": "quantidade",
            "qtd": "quantidade",
            "moeda": "moeda_liquidacao",
            "moeda_liquidacao": "moeda_liquidacao",
            "valor_moeda": "valor_moeda",
            "cambio": "cambio_para_usd",
            "câmbio": "cambio_para_usd",
            "cambio_para_usd": "cambio_para_usd",
        }
        return aliases.get(field)

    return SimpleNamespace(
        extract_confirmacao=extract_confirmacao,
        parse_single_currency_choice=parse_single_currency_choice,
        parse_origem_choice=parse_origem_choice,
        parse_forma_pagamento_choice=parse_forma_pagamento_choice,
        parse_fechamento_tipo_choice=parse_fechamento_tipo_choice,
        extract_moedas=extract_moedas,
        extract_caixa_currency=extract_caixa_currency,
        parse_operation_id=parse_operation_id,
        parse_operation_reference=parse_operation_reference,
        normalize_edit_field=normalize_edit_field,
    )