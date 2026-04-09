from decimal import Decimal, ROUND_HALF_UP


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def grams(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def fx_rate(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _format_decimal_pt_br(value: Decimal, places: int = 2) -> str:
    normalized = value.quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{normalized:,.{places}f}"
    return text.replace(",", "#").replace(".", ",").replace("#", ".")


def _format_usd_pt_br(value: Decimal) -> str:
    return f"USD {_format_decimal_pt_br(money(value), 2)}"


def _format_grams_pt_br(value: Decimal) -> str:
    return f"{_format_decimal_pt_br(grams(value), 3)} g"


def _format_percent_pt_br(value: Decimal) -> str:
    return f"{_format_decimal_pt_br(value, 2)}%"


def _format_receipt_caixa_movement(currency: str, movement: Decimal) -> str:
    signal = "+" if movement >= 0 else "-"
    magnitude = abs(movement)
    if currency == "XAU":
        return f"{signal}{_format_grams_pt_br(magnitude)}"
    if currency == "USD":
        return f"{signal}{_format_usd_pt_br(magnitude)}"
    if currency == "EUR":
        return f"{signal}EUR {_format_decimal_pt_br(magnitude, 2)}"
    if currency == "SRD":
        return f"{signal}SRD {_format_decimal_pt_br(magnitude, 2)}"
    if currency == "BRL":
        return f"{signal}R$ {_format_decimal_pt_br(magnitude, 2)}"
    return f"{signal}{currency} {_format_decimal_pt_br(magnitude, 2)}"