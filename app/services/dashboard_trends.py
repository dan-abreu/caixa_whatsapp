import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any, Dict, List


def _build_saas_dashboard_trend(transactions: List[Dict[str, Any]], days: int = 7) -> List[Dict[str, Any]]:
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    local_today = (datetime.now(timezone.utc) + timedelta(hours=tz_offset_hours)).date()
    window_start = local_today - timedelta(days=max(days - 1, 0))
    buckets: Dict[str, Dict[str, Decimal]] = {}

    for offset in range(days - 1, -1, -1):
        day_date = local_today - timedelta(days=offset)
        buckets[day_date.isoformat()] = {
            "gross_grams": Decimal("0"),
            "fine_grams": Decimal("0"),
            "buy_grams": Decimal("0"),
            "sell_grams": Decimal("0"),
        }

    for item in transactions:
        try:
            created_at_raw = str(item.get("criado_em") or "").strip()
            if not created_at_raw:
                continue
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            local_date = (created_at.astimezone(timezone.utc) + timedelta(hours=tz_offset_hours)).date()
            if local_date < window_start or local_date > local_today:
                continue
            peso = Decimal(str(item.get("peso") or "0"))
            teor = Decimal(str(item.get("teor") or "0"))
            tipo_operacao = str(item.get("tipo_operacao") or "").lower()
        except (InvalidOperation, TypeError, ValueError):
            continue

        if peso <= 0 or tipo_operacao not in {"compra", "venda"}:
            continue

        teor = max(Decimal("0"), min(Decimal("100"), teor))
        fine_grams = (peso * teor / Decimal("100")).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        bucket = buckets.get(local_date.isoformat())
        if not bucket:
            continue
        bucket["gross_grams"] += peso
        bucket["fine_grams"] += fine_grams
        if tipo_operacao == "compra":
            bucket["buy_grams"] += peso
        else:
            bucket["sell_grams"] += peso

    points: List[Dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        day_date = local_today - timedelta(days=offset)
        bucket = buckets.get(day_date.isoformat()) or {}
        points.append(
            {
                "label": day_date.strftime("%d/%m"),
                "gross_grams": Decimal(str(bucket.get("gross_grams", Decimal("0")))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
                "fine_grams": Decimal(str(bucket.get("fine_grams", Decimal("0")))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
                "buy_grams": Decimal(str(bucket.get("buy_grams", Decimal("0")))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
                "sell_grams": Decimal(str(bucket.get("sell_grams", Decimal("0")))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
            }
        )
    return points


def _render_saas_trend_chart(points: List[Dict[str, Any]]) -> str:
    if not points:
        return "<div class='empty-state'>Sem dados para o grafico.</div>"

    max_gross = max((Decimal(str(point.get("gross_grams", "0") or "0")) for point in points), default=Decimal("1")) or Decimal("1")
    max_fine = max((Decimal(str(point.get("fine_grams", "0") or "0")) for point in points), default=Decimal("1")) or Decimal("1")
    width = 640
    padding_x = 38
    baseline_y = 188
    chart_width = width - (padding_x * 2)
    step_x = chart_width / max(len(points) - 1, 1)
    bar_width = max(int(chart_width / max(len(points), 1) * 0.42), 18)

    line_points: List[str] = []
    bars: List[str] = []
    labels: List[str] = []

    for index, point in enumerate(points):
        x = padding_x + (index * step_x)
        gross_grams = Decimal(str(point.get("gross_grams", "0") or "0"))
        fine_grams = Decimal(str(point.get("fine_grams", "0") or "0"))
        bar_height = 0 if max_gross <= 0 else int((gross_grams / max_gross) * 104)
        y_bar = baseline_y - bar_height
        line_y = baseline_y - float((fine_grams / max_fine) * Decimal("128"))
        line_points.append(f"{x:.1f},{line_y:.1f}")
        bars.append(
            f"<rect x='{x - (bar_width / 2):.1f}' y='{y_bar:.1f}' width='{bar_width}' height='{bar_height}' rx='10' fill='rgba(173,116,0,.28)' />"
            f"<text x='{x:.1f}' y='{y_bar - 8:.1f}' text-anchor='middle' class='chart-value'>{gross_grams:.1f}g</text>"
        )
        labels.append(f"<text x='{x:.1f}' y='216' text-anchor='middle' class='chart-label'>{escape(str(point.get('label') or ''))}</text>")

    return (
        "<svg viewBox='0 0 640 240' class='trend-chart' role='img' aria-label='Grafico de movimentacao de ouro dos ultimos dias'>"
        "<defs><linearGradient id='trendLine' x1='0' x2='1' y1='0' y2='0'><stop offset='0%' stop-color='#1d5844' /><stop offset='100%' stop-color='#ad7400' /></linearGradient></defs>"
        "<line x1='38' y1='188' x2='602' y2='188' stroke='rgba(27,26,23,.18)' stroke-width='1' />"
        + "".join(bars)
        + f"<polyline points='{' '.join(line_points)}' fill='none' stroke='url(#trendLine)' stroke-width='4' stroke-linecap='round' stroke-linejoin='round' />"
        + "".join(labels)
        + "<text x='38' y='24' class='chart-axis'>g fino</text><text x='602' y='24' text-anchor='end' class='chart-axis'>g bruto</text>"
        + "</svg>"
    )


def _render_dashboard_trend_html(transactions: List[Dict[str, Any]]) -> str:
    points = _build_saas_dashboard_trend(transactions)
    return _render_saas_trend_chart(points)