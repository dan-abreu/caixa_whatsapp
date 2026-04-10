from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


def build_guided_flow_fx_helpers(
    *,
    fx_rate: Callable[[Decimal], Decimal],
    money: Callable[[Decimal], Decimal],
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> SimpleNamespace:
    moeda_strength: Mapping[str, int] = {"EUR": 0, "USD": 1, "BRL": 2, "SRD": 3}

    def payment_fx_prompt_label(moeda: str) -> str:
        moeda_up = str(moeda or "USD").upper()
        if moeda_up == "EUR":
            return "1 EUR = quantos USD?"
        if moeda_up in {"SRD", "BRL"}:
            return f"1 USD = quantos {moeda_up}?"
        return "Câmbio para USD"

    def display_cambio_for_web_input(moeda: str, cambio_para_usd: Decimal) -> str:
        moeda_up = str(moeda or "USD").upper()
        if moeda_up == "USD":
            return "1"
        normalized = fx_rate(cambio_para_usd)
        if normalized <= 0:
            return ""
        if moeda_up == "EUR":
            return format_decimal_for_form(fx_rate(Decimal("1") / normalized), 4)
        return format_decimal_for_form(normalized, 4)

    def payment_input_to_usd(moeda: str, valor_moeda: Decimal, cambio_informado: Decimal) -> Decimal:
        moeda_up = str(moeda or "USD").upper()
        if moeda_up == "USD":
            return money(valor_moeda)
        if cambio_informado <= 0:
            return Decimal("0")
        if moeda_up == "EUR":
            return money(valor_moeda * cambio_informado)
        return money(valor_moeda / cambio_informado)

    def build_cambio_prompt(moeda: str) -> str:
        moeda_up = str(moeda or "USD").upper()
        if moeda_up == "EUR":
            return "1 EUR = quantos USD?"
        return f"1 USD = quantos {moeda_up}?"

    def build_pair_cambio_prompt(base: str, payment: str) -> str:
        b, p = base.upper(), payment.upper()
        if moeda_strength.get(b, 5) <= moeda_strength.get(p, 5):
            return f"1 {b} = quantos {p}?"
        return f"1 {p} = quantos {b}?"

    def pair_rate_to_payment_per_usd(
        base: str,
        payment: str,
        user_rate: Decimal,
        db: Any,
    ) -> Tuple[Optional[Decimal], Decimal, Optional[Decimal]]:
        b, p = base.upper(), payment.upper()
        if moeda_strength.get(b, 5) <= moeda_strength.get(p, 5):
            pair_p_per_b = user_rate
        else:
            pair_p_per_b = fx_rate(Decimal("1") / user_rate) if user_rate > 0 else Decimal("1")

        raw_base = db.get_last_cambio_para_usd(b)
        if raw_base and Decimal(str(raw_base)) > 0:
            cambio_base = Decimal(str(raw_base))
            return fx_rate(pair_p_per_b * cambio_base), pair_p_per_b, cambio_base

        raw_pay = db.get_last_cambio_para_usd(p)
        if raw_pay and Decimal(str(raw_pay)) > 0:
            return Decimal(str(raw_pay)), pair_p_per_b, None

        return None, pair_p_per_b, None

    def normalize_cambio_para_usd(moeda: str, cambio_informado: Decimal) -> Decimal:
        moeda_up = str(moeda or "USD").upper()
        if moeda_up == "EUR":
            return fx_rate(Decimal("1") / cambio_informado)
        return fx_rate(cambio_informado)

    def try_set_total_usd_from_base_rate(contexto: Dict[str, Any], cambio_base_para_usd: Decimal) -> bool:
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda == "USD":
            return bool(contexto.get("total_usd"))

        preco_moeda_valor_raw = contexto.get("preco_moeda_valor")
        peso_raw = contexto.get("peso")
        if preco_moeda_valor_raw is None or peso_raw is None:
            return False

        preco_moeda_valor = Decimal(str(preco_moeda_valor_raw))
        peso = Decimal(str(peso_raw))
        preco_usd = money(preco_moeda_valor / cambio_base_para_usd)
        total_usd = money(preco_usd * peso)
        contexto["cambio_preco_moeda"] = str(fx_rate(cambio_base_para_usd))
        contexto["preco_usd"] = str(preco_usd)
        contexto["total_usd"] = str(total_usd)
        return True

    return SimpleNamespace(
        payment_fx_prompt_label=payment_fx_prompt_label,
        display_cambio_for_web_input=display_cambio_for_web_input,
        payment_input_to_usd=payment_input_to_usd,
        build_cambio_prompt=build_cambio_prompt,
        moeda_strength=moeda_strength,
        build_pair_cambio_prompt=build_pair_cambio_prompt,
        pair_rate_to_payment_per_usd=pair_rate_to_payment_per_usd,
        normalize_cambio_para_usd=normalize_cambio_para_usd,
        try_set_total_usd_from_base_rate=try_set_total_usd_from_base_rate,
    )