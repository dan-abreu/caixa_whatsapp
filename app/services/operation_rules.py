from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Optional, Tuple

from fastapi import HTTPException


def build_operation_rule_helpers(
    *,
    normalize_text: Callable[[str], str],
    parse_decimal_web_field: Callable[[str, str], Decimal],
    money: Callable[[Decimal], Decimal],
) -> SimpleNamespace:
    def normalize_ativo_nome(raw: str) -> str:
        value = raw.strip().lower()
        aliases = {
            "ouro 18k": "Ouro",
            "grama": "Ouro",
            "usd": "USD",
            "dólar": "USD",
            "dolares": "USD",
            "dólares": "USD",
            "euro": "EUR",
            "srd": "SRD",
            "real": "BRL",
            "reais": "BRL",
        }
        return aliases.get(value, raw.strip())

    def infer_tipo_operacao(mensagem: str) -> str:
        text = mensagem.lower()
        if "vendi" in text or "venda" in text:
            return "venda"
        if "cambio" in text or "câmbio" in text or "troca" in text:
            return "cambio"
        return "compra"

    def normalize_gold_type(raw: Any) -> str:
        text = normalize_text(str(raw or "fundido"))
        if text in {"queimado", "queimada", "burned"}:
            return "queimado"
        return "fundido"

    def parse_gold_trade_profile(
        tipo_operacao: str,
        gold_type_raw: Any,
        quebra_raw: Any,
    ) -> Tuple[str, Optional[Decimal]]:
        gold_type = normalize_gold_type(gold_type_raw)
        if tipo_operacao != "compra" or gold_type != "queimado":
            return gold_type, None

        quebra_text = str(quebra_raw or "").strip()
        if not quebra_text:
            raise HTTPException(status_code=400, detail="Informe a quebra quando a compra for queimado")

        quebra = parse_decimal_web_field(quebra_text, "quebra")
        if quebra <= 0 or quebra > Decimal("100"):
            raise HTTPException(status_code=400, detail="Quebra deve estar entre 0 e 100")
        return gold_type, money(quebra)

    return SimpleNamespace(
        normalize_ativo_nome=normalize_ativo_nome,
        infer_tipo_operacao=infer_tipo_operacao,
        normalize_gold_type=normalize_gold_type,
        parse_gold_trade_profile=parse_gold_trade_profile,
    )