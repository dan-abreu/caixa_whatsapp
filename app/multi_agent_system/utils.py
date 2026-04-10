from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, cast


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _fmt_decimal(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places)))


def _safe_ratio(numerator: Decimal, denominator: Decimal, default: str = "0") -> Decimal:
    if denominator == 0:
        return Decimal(default)
    return numerator / denominator


def _z_score(value: Decimal, mean: Decimal, std: Decimal) -> Decimal:
    if std <= 0:
        return Decimal("0")
    return (value - mean) / std


def _extract_payments(tx: Dict[str, Any]) -> List[Dict[str, Any]]:
    pagamentos = tx.get("pagamentos")
    out: List[Dict[str, Any]] = []
    if not isinstance(pagamentos, list):
        return out
    for raw_item in cast(List[Any], pagamentos):
        if isinstance(raw_item, dict):
            out.append(cast(Dict[str, Any], raw_item))
    return out