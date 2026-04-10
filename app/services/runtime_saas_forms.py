from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional


def build_runtime_saas_form_helpers(
    *,
    get_saas_recent_fx_cached: Callable[[], Optional[Dict[str, str]]],
    set_saas_recent_fx_cached: Callable[[Dict[str, str]], Dict[str, str]],
    display_cambio_for_web_input: Callable[[str, Decimal], str],
    parse_decimal_web_field: Callable[[str, str], Decimal],
) -> SimpleNamespace:
    def dashboard_default_form_values(session_user: Dict[str, Any]) -> Dict[str, str]:
        operador = str((session_user or {}).get("telefone") or "+59711111111")
        return {
            "operador_id": operador,
            "tipo_operacao": "compra",
            "origem": "balcao",
            "gold_type": "fundido",
            "quebra": "",
            "teor": "90",
            "peso": "",
            "preco_usd": "",
            "sale_source_mode": "manual",
            "fechamento_gramas": "",
            "fechamento_tipo": "total",
            "cliente_id": "",
            "cliente_lookup_meta": "",
            "pessoa": "",
            "inline_cliente_mode": "0",
            "inline_cliente_nome": "",
            "inline_cliente_telefone": "",
            "inline_cliente_documento": "",
            "inline_cliente_apelido": "",
            "inline_cliente_observacoes": "",
            "inline_cliente_saldo_xau": "",
            "forma_pagamento": "dinheiro",
            "total_pago_usd": "",
            "observacoes": "",
            "console_remetente": operador,
            "console_mensagem": "",
            "payment_1_moeda": "USD",
            "payment_1_valor": "",
            "payment_1_percent": "",
            "payment_1_cambio": "1",
            "payment_1_forma": "dinheiro",
            "payment_1_client_bank_account_id": "",
            "payment_1_company_bank_account_id": "",
            "payment_2_moeda": "",
            "payment_2_valor": "",
            "payment_2_percent": "",
            "payment_2_cambio": "",
            "payment_2_forma": "dinheiro",
            "payment_2_client_bank_account_id": "",
            "payment_2_company_bank_account_id": "",
            "payment_3_moeda": "",
            "payment_3_valor": "",
            "payment_3_percent": "",
            "payment_3_cambio": "",
            "payment_3_forma": "dinheiro",
            "payment_3_client_bank_account_id": "",
            "payment_3_company_bank_account_id": "",
            "payment_4_moeda": "",
            "payment_4_valor": "",
            "payment_4_cambio": "",
            "payment_4_forma": "dinheiro",
            "payment_4_client_bank_account_id": "",
            "payment_4_company_bank_account_id": "",
        }

    def format_decimal_for_form(value: Decimal, places: int = 2) -> str:
        quant = Decimal("1").scaleb(-places)
        normalized = value.quantize(quant, rounding=ROUND_HALF_UP)
        text = format(normalized, "f").rstrip("0").rstrip(".")
        return text or "0"

    def build_saas_recent_fx_map(db: Any) -> Dict[str, str]:
        cached = get_saas_recent_fx_cached()
        if cached is not None:
            return cached

        snapshot: Dict[str, str] = {"USD": "1"}
        recent_rates = db.get_last_cambio_para_usd_map(["EUR", "SRD", "BRL"])
        for moeda in ["EUR", "SRD", "BRL"]:
            raw = recent_rates.get(moeda)
            if raw and Decimal(str(raw)) > 0:
                snapshot[moeda] = display_cambio_for_web_input(moeda, Decimal(str(raw)))
            else:
                snapshot[moeda] = ""
        return set_saas_recent_fx_cached(snapshot)

    def parse_cliente_opening_balances(form: Dict[str, str], prefix: str) -> Dict[str, Decimal]:
        balances: Dict[str, Decimal] = {}
        for currency in ["XAU", "USD", "EUR", "SRD", "BRL"]:
            field_name = f"{prefix}_{currency.lower()}"
            raw_value = str(form.get(field_name) or "").strip()
            if not raw_value:
                continue
            balances[currency] = parse_decimal_web_field(raw_value, field_name)
        return balances

    return SimpleNamespace(
        dashboard_default_form_values=dashboard_default_form_values,
        format_decimal_for_form=format_decimal_for_form,
        build_saas_recent_fx_map=build_saas_recent_fx_map,
        parse_cliente_opening_balances=parse_cliente_opening_balances,
    )