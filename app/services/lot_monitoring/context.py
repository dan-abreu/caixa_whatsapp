from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List

from app.core.formatting import money


def _build_open_lot_market_context(
    open_lots: List[Dict[str, Any]],
    market_snapshot: Dict[str, str],
    *,
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> Dict[str, Any]:
    try:
        xau_usd_spot = Decimal(str(market_snapshot.get("xau_usd_raw") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        xau_usd_spot = Decimal("0")

    pure_gram_spot = money(xau_usd_spot / Decimal("31.1035")) if xau_usd_spot > 0 else Decimal("0")
    now_local = datetime.now(timezone.utc)
    enriched_lots: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Decimal | str | int]] = {}
    total_fine_grams = Decimal("0")
    total_market_value = Decimal("0")
    total_unrealized = Decimal("0")

    for lot in open_lots:
        try:
            remaining_grams = Decimal(str(lot.get("remaining_grams") or "0"))
            initial_grams = Decimal(str(lot.get("initial_grams") or remaining_grams))
            unit_cost_usd = Decimal(str(lot.get("unit_cost_usd") or "0"))
            teor_pct = Decimal(str(lot.get("teor") or "100"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if remaining_grams <= 0:
            continue
        if teor_pct <= 0:
            teor_pct = Decimal("100")

        fine_grams = money(remaining_grams * (teor_pct / Decimal("100")))
        lot_cost_usd = money(remaining_grams * unit_cost_usd)
        market_unit_usd = money(pure_gram_spot * (teor_pct / Decimal("100"))) if pure_gram_spot > 0 else Decimal("0")
        market_value_usd = money(remaining_grams * market_unit_usd) if market_unit_usd > 0 else Decimal("0")
        unrealized_pnl_usd = money(market_value_usd - lot_cost_usd) if market_unit_usd > 0 else Decimal("0")

        created_at = str(lot.get("created_at_tx") or lot.get("criado_em") or "")
        hold_days = 0
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            hold_days = max(0, int((now_local - created_dt).total_seconds() // 86400))
        except Exception:
            hold_days = 0

        teor_label = format_decimal_for_form(teor_pct, 2)
        grouping = grouped.setdefault(
            teor_label,
            {
                "teor": teor_label,
                "lots": 0,
                "grams": Decimal("0"),
                "fine_grams": Decimal("0"),
                "cost_usd": Decimal("0"),
                "market_value_usd": Decimal("0"),
                "unrealized_pnl_usd": Decimal("0"),
            },
        )
        grouping["lots"] = int(grouping["lots"] or 0) + 1
        grouping["grams"] = Decimal(str(grouping["grams"] or "0")) + remaining_grams
        grouping["fine_grams"] = Decimal(str(grouping["fine_grams"] or "0")) + fine_grams
        grouping["cost_usd"] = Decimal(str(grouping["cost_usd"] or "0")) + lot_cost_usd
        grouping["market_value_usd"] = Decimal(str(grouping["market_value_usd"] or "0")) + market_value_usd
        grouping["unrealized_pnl_usd"] = Decimal(str(grouping["unrealized_pnl_usd"] or "0")) + unrealized_pnl_usd

        total_fine_grams += fine_grams
        total_market_value += market_value_usd
        total_unrealized += unrealized_pnl_usd
        enriched_lots.append(
            {
                **lot,
                "initial_grams": str(initial_grams),
                "remaining_grams": str(remaining_grams),
                "teor": teor_label,
                "fine_grams": str(fine_grams),
                "lot_cost_usd": str(lot_cost_usd),
                "market_unit_usd": str(market_unit_usd),
                "market_value_usd": str(market_value_usd),
                "unrealized_pnl_usd": str(unrealized_pnl_usd),
                "hold_days": hold_days,
            }
        )

    by_teor = [
        {
            "teor": str(item["teor"]),
            "lots": int(item["lots"]),
            "grams": str(money(Decimal(str(item["grams"])))),
            "fine_grams": str(money(Decimal(str(item["fine_grams"])))),
            "cost_usd": str(money(Decimal(str(item["cost_usd"])))),
            "market_value_usd": str(money(Decimal(str(item["market_value_usd"])))),
            "unrealized_pnl_usd": str(money(Decimal(str(item["unrealized_pnl_usd"])))),
        }
        for item in sorted(grouped.values(), key=lambda row: Decimal(str(row.get("teor") or "0")), reverse=True)
    ]

    return {
        "pure_gram_spot_usd": str(pure_gram_spot),
        "available_fine_grams": str(money(total_fine_grams)),
        "market_value_usd": str(money(total_market_value)),
        "unrealized_pnl_usd": str(money(total_unrealized)),
        "lots": enriched_lots,
        "by_teor": by_teor,
    }


def _build_operation_lot_market_context(
    open_lots: List[Dict[str, Any]],
    market_snapshot: Dict[str, str],
    *,
    format_decimal_for_form: Callable[[Decimal, int], str],
) -> Dict[str, Any]:
    try:
        xau_usd_spot = Decimal(str(market_snapshot.get("xau_usd_raw") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        xau_usd_spot = Decimal("0")

    pure_gram_spot = money(xau_usd_spot / Decimal("31.1035")) if xau_usd_spot > 0 else Decimal("0")
    grouped: Dict[str, Dict[str, Decimal | str | int]] = {}
    risk_lots: List[Dict[str, Any]] = []
    total_fine_grams = Decimal("0")
    total_market_value = Decimal("0")
    total_unrealized = Decimal("0")

    for lot in open_lots:
        try:
            remaining_grams = Decimal(str(lot.get("remaining_grams") or "0"))
            unit_cost_usd = Decimal(str(lot.get("unit_cost_usd") or "0"))
            teor_pct = Decimal(str(lot.get("teor") or "100"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if remaining_grams <= 0:
            continue
        if teor_pct <= 0:
            teor_pct = Decimal("100")

        fine_grams = money(remaining_grams * (teor_pct / Decimal("100")))
        lot_cost_usd = money(remaining_grams * unit_cost_usd)
        market_unit_usd = money(pure_gram_spot * (teor_pct / Decimal("100"))) if pure_gram_spot > 0 else Decimal("0")
        market_value_usd = money(remaining_grams * market_unit_usd) if market_unit_usd > 0 else Decimal("0")
        unrealized_pnl_usd = money(market_value_usd - lot_cost_usd) if market_unit_usd > 0 else Decimal("0")

        teor_label = format_decimal_for_form(teor_pct, 2)
        grouping = grouped.setdefault(
            teor_label,
            {
                "teor": teor_label,
                "lots": 0,
                "grams": Decimal("0"),
                "fine_grams": Decimal("0"),
                "cost_usd": Decimal("0"),
                "market_value_usd": Decimal("0"),
                "unrealized_pnl_usd": Decimal("0"),
            },
        )
        grouping["lots"] = int(grouping["lots"] or 0) + 1
        grouping["grams"] = Decimal(str(grouping["grams"] or "0")) + remaining_grams
        grouping["fine_grams"] = Decimal(str(grouping["fine_grams"] or "0")) + fine_grams
        grouping["cost_usd"] = Decimal(str(grouping["cost_usd"] or "0")) + lot_cost_usd
        grouping["market_value_usd"] = Decimal(str(grouping["market_value_usd"] or "0")) + market_value_usd
        grouping["unrealized_pnl_usd"] = Decimal(str(grouping["unrealized_pnl_usd"] or "0")) + unrealized_pnl_usd

        total_fine_grams += fine_grams
        total_market_value += market_value_usd
        total_unrealized += unrealized_pnl_usd
        risk_lots.append(
            {
                "source_transaction_id": lot.get("source_transaction_id", lot.get("source_id")),
                "source_id": lot.get("source_id"),
                "teor": teor_label,
                "remaining_grams": str(remaining_grams),
                "unrealized_pnl_usd": str(unrealized_pnl_usd),
            }
        )
        risk_lots.sort(key=lambda item: Decimal(str(item.get("unrealized_pnl_usd") or "0")))
        if len(risk_lots) > 4:
            risk_lots.pop()

    by_teor = [
        {
            "teor": str(item["teor"]),
            "lots": int(item["lots"]),
            "grams": str(money(Decimal(str(item["grams"])))),
            "fine_grams": str(money(Decimal(str(item["fine_grams"])))),
            "cost_usd": str(money(Decimal(str(item["cost_usd"])))),
            "market_value_usd": str(money(Decimal(str(item["market_value_usd"])))),
            "unrealized_pnl_usd": str(money(Decimal(str(item["unrealized_pnl_usd"])))),
        }
        for item in sorted(grouped.values(), key=lambda row: Decimal(str(row.get("teor") or "0")), reverse=True)
    ]

    return {
        "pure_gram_spot_usd": str(pure_gram_spot),
        "available_fine_grams": str(money(total_fine_grams)),
        "market_value_usd": str(money(total_market_value)),
        "unrealized_pnl_usd": str(money(total_unrealized)),
        "by_teor": by_teor,
        "risk_lots": risk_lots,
    }