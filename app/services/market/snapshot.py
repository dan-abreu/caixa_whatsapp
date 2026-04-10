import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, cast

import requests

from app.core.formatting import fx_rate, money
from app.shared_cache import get_shared_cache

from .formatting import _format_market_decimal


logger = logging.getLogger("caixa_whatsapp")

_MARKET_CACHE_TTL_SECONDS = int(os.getenv("MARKET_CACHE_TTL_SECONDS", "15"))
_MARKET_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_MARKET_TICK_HISTORY: List[Dict[str, str]] = []
_MARKET_SNAPSHOT_CACHE_KEY = "market:snapshot"


def _fetch_json_url(url: str) -> Any:
    response = requests.get(
        url,
        headers={"Accept": "application/json", "User-Agent": "CaixaWhatsApp/1.0"},
        timeout=3,
    )
    response.raise_for_status()
    return response.json()


def _extract_gold_api_xau_usd(payload: Any) -> Optional[Decimal]:
    data = cast(Dict[str, Any], payload)
    xau_raw = data.get("price") or data.get("ch")
    if xau_raw is None:
        return None
    return Decimal(str(xau_raw))


def _extract_awesomeapi_gold_price(payload: Any) -> Optional[Decimal]:
    quote = cast(Dict[str, Any], payload).get("XAUUSD")
    if not isinstance(quote, dict):
        return None
    bid = quote.get("bid") or quote.get("ask")
    if bid is None:
        return None
    return Decimal(str(bid))


def _build_market_snapshot_from_rates(
    xau_usd: Optional[Decimal],
    usd_brl: Optional[Decimal],
    eur_usd: Optional[Decimal],
    eur_brl: Optional[Decimal],
) -> Dict[str, Optional[Decimal]]:
    if eur_brl is None and eur_usd and usd_brl:
        eur_brl = money(eur_usd * usd_brl)

    grama_ref = None
    if xau_usd and xau_usd > 0:
        grama_ref = money((xau_usd / Decimal("31.1035")) * Decimal("0.90"))

    return {
        "xau_usd": money(xau_usd) if xau_usd and xau_usd > 0 else None,
        "grama_ref": grama_ref,
        "usd_brl": fx_rate(usd_brl) if usd_brl and usd_brl > 0 else None,
        "eur_usd": fx_rate(eur_usd) if eur_usd and eur_usd > 0 else None,
        "eur_brl": fx_rate(eur_brl) if eur_brl and eur_brl > 0 else None,
    }


def _get_market_snapshot() -> Dict[str, str]:
    now = datetime.now(timezone.utc)
    expires_at = _MARKET_CACHE.get("expires_at")
    cached = _MARKET_CACHE.get("data")
    if isinstance(expires_at, datetime) and cached and expires_at > now:
        return cast(Dict[str, str], cached)

    shared_cache = get_shared_cache()
    if shared_cache is not None:
        shared_snapshot = shared_cache.get_json(_MARKET_SNAPSHOT_CACHE_KEY)
        if isinstance(shared_snapshot, dict) and shared_snapshot:
            _MARKET_CACHE["expires_at"] = now + timedelta(seconds=_MARKET_CACHE_TTL_SECONDS)
            _MARKET_CACHE["data"] = shared_snapshot
            return cast(Dict[str, str], shared_snapshot)

    xau_usd: Optional[Decimal] = None
    usd_brl: Optional[Decimal] = None
    eur_usd: Optional[Decimal] = None
    eur_brl: Optional[Decimal] = None
    status_parts: List[str] = []
    gold_source = "unavailable"
    primary_gold_error: Optional[Exception] = None
    fallback_gold_error: Optional[Exception] = None

    try:
        gold_payload = _fetch_json_url("https://api.gold-api.com/price/XAU/USD")
        xau_usd = _extract_gold_api_xau_usd(gold_payload)
        if xau_usd is not None:
            gold_source = "gold_api"
    except Exception as exc:
        primary_gold_error = exc

    if xau_usd is None:
        try:
            awesome_payload = _fetch_json_url("https://economia.awesomeapi.com.br/last/XAU-USD")
            xau_usd = _extract_awesomeapi_gold_price(awesome_payload)
            if xau_usd is not None:
                gold_source = "awesomeapi"
        except Exception as exc:
            fallback_gold_error = exc

    if xau_usd is not None and primary_gold_error is not None:
        logger.info("Fonte primaria de XAU/USD indisponivel, usando contingencia: %s", primary_gold_error)
    if xau_usd is None:
        if primary_gold_error is not None:
            logger.warning("Falha ao consultar XAU/USD: %s", primary_gold_error)
        if fallback_gold_error is not None:
            logger.warning("Falha ao consultar fallback de ouro via AwesomeAPI: %s", fallback_gold_error)

    xau_source_label = "XAU/USD indisponivel"
    if xau_usd is None:
        status_parts.append(xau_source_label)
    elif gold_source == "awesomeapi":
        xau_source_label = "XAU/USD via AwesomeAPI"
        status_parts.append(xau_source_label)
    else:
        xau_source_label = "XAU/USD via Gold-API"
        status_parts.append(xau_source_label)

    try:
        fx_payload = _fetch_json_url("https://api.frankfurter.app/latest?from=EUR&to=USD,BRL")
        rates = cast(Dict[str, Any], fx_payload.get("rates") or {})
        if rates.get("USD") is not None:
            eur_usd = Decimal(str(rates["USD"]))
        if rates.get("BRL") is not None:
            eur_brl = Decimal(str(rates["BRL"]))
        if eur_usd and eur_usd > 0 and eur_brl and eur_brl > 0:
            usd_brl = eur_brl / eur_usd
    except Exception as exc:
        logger.warning("Falha ao consultar FX do mercado: %s", exc)
        status_parts.append("FX indisponivel")

    metrics = _build_market_snapshot_from_rates(xau_usd, usd_brl, eur_usd, eur_brl)
    snapshot = {
        "xau_usd": _format_market_decimal(metrics["xau_usd"], prefix="USD "),
        "xau_usd_raw": str(metrics["xau_usd"] or ""),
        "grama_ref": _format_market_decimal(metrics["grama_ref"], prefix="USD ", suffix="/g"),
        "grama_ref_raw": str(metrics["grama_ref"] or ""),
        "usd_brl": _format_market_decimal(metrics["usd_brl"]),
        "usd_brl_raw": str(metrics["usd_brl"] or ""),
        "eur_usd": _format_market_decimal(metrics["eur_usd"]),
        "eur_usd_raw": str(metrics["eur_usd"] or ""),
        "eur_brl": _format_market_decimal(metrics["eur_brl"]),
        "eur_brl_raw": str(metrics["eur_brl"] or ""),
        "xau_source": gold_source,
        "xau_source_label": xau_source_label,
        "status": " | ".join(status_parts) if status_parts else "Atualizado por API externa.",
        "updated_at": now.isoformat(),
        "updated_at_label": now.astimezone().strftime("%H:%M:%S"),
    }
    _MARKET_TICK_HISTORY.append(
        {
            "updated_at": str(snapshot.get("updated_at") or now.isoformat()),
            "xau_usd_raw": str(snapshot.get("xau_usd_raw") or ""),
            "grama_ref_raw": str(snapshot.get("grama_ref_raw") or ""),
            "usd_brl_raw": str(snapshot.get("usd_brl_raw") or ""),
            "eur_usd_raw": str(snapshot.get("eur_usd_raw") or ""),
            "eur_brl_raw": str(snapshot.get("eur_brl_raw") or ""),
        }
    )
    del _MARKET_TICK_HISTORY[:-96]
    _MARKET_CACHE["expires_at"] = now + timedelta(seconds=_MARKET_CACHE_TTL_SECONDS)
    _MARKET_CACHE["data"] = snapshot
    if shared_cache is not None:
        shared_cache.set_json(_MARKET_SNAPSHOT_CACHE_KEY, snapshot, _MARKET_CACHE_TTL_SECONDS)
    return snapshot


def _get_market_history_series(field: str, limit: int = 24) -> List[Decimal]:
    series: List[Decimal] = []
    for item in _MARKET_TICK_HISTORY[-limit:]:
        try:
            value = Decimal(str(item.get(field) or "0"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if value > 0:
            series.append(value)
    return series


def _mean_decimal(values: List[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _build_market_trend_context() -> Dict[str, Any]:
    xau_series = _get_market_history_series("xau_usd_raw", limit=24)
    if not xau_series:
        return {
            "signal": "neutral",
            "summary": "Sem historico suficiente para leitura de tendencia.",
            "short_ma": "0",
            "long_ma": "0",
            "momentum_pct": "0",
        }

    short_window = xau_series[-4:] if len(xau_series) >= 4 else xau_series
    long_window = xau_series[-12:] if len(xau_series) >= 12 else xau_series
    short_ma = _mean_decimal(short_window)
    long_ma = _mean_decimal(long_window)
    anchor = xau_series[-6] if len(xau_series) >= 6 else xau_series[0]
    latest = xau_series[-1]
    momentum_pct = ((latest - anchor) / anchor * Decimal("100")) if anchor > 0 else Decimal("0")

    signal = "neutral"
    summary = "Mercado lateral."
    if latest >= short_ma >= long_ma and momentum_pct >= Decimal("0.35"):
        signal = "bullish"
        summary = "Mercado em alta curta e acima da media."
    elif latest <= short_ma <= long_ma and momentum_pct <= Decimal("-0.35"):
        signal = "bearish"
        summary = "Mercado em enfraquecimento e abaixo da media."
    elif latest >= long_ma and momentum_pct > 0:
        signal = "constructive"
        summary = "Mercado construtivo, mas sem confirmacao forte."

    return {
        "signal": signal,
        "summary": summary,
        "short_ma": str(money(short_ma)),
        "long_ma": str(money(long_ma)),
        "momentum_pct": str(money(momentum_pct)),
    }