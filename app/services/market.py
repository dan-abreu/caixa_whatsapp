import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlencode

from defusedxml import ElementTree as SafeElementTree
import requests

from app.core.formatting import fx_rate, money
from app.shared_cache import get_shared_cache


logger = logging.getLogger("caixa_whatsapp")

_MARKET_CACHE_TTL_SECONDS = int(os.getenv("MARKET_CACHE_TTL_SECONDS", "15"))
_MARKET_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_MARKET_NEWS_CACHE_TTL_SECONDS = int(os.getenv("MARKET_NEWS_CACHE_TTL_SECONDS", "900"))
_MARKET_NEWS_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_MARKET_TICK_HISTORY: List[Dict[str, str]] = []
_MARKET_SNAPSHOT_CACHE_KEY = "market:snapshot"
_MARKET_NEWS_CACHE_KEY = "market:news"


def _fetch_json_url(url: str) -> Any:
    response = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "CaixaWhatsApp/1.0",
        },
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


def _format_market_decimal(value: Optional[Decimal], prefix: str = "", suffix: str = "") -> str:
    if value is None:
        return "Indisponivel"
    return f"{prefix}{value}{suffix}"


def _format_live_market_value(raw_value: str, prefix: str = "", suffix: str = "", decimals: int = 2) -> str:
    try:
        value = Decimal(str(raw_value or ""))
    except (InvalidOperation, TypeError, ValueError):
        return "Indisponivel"
    quantizer = Decimal("1") if decimals <= 0 else Decimal("1").scaleb(-decimals)
    normalized = value.quantize(quantizer, rounding=ROUND_HALF_UP)
    text = f"{normalized:,.{decimals}f}"
    text = text.replace(",", "#").replace(".", ",").replace("#", ".")
    return f"{prefix}{text}{suffix}"


def _render_market_panel_html(
    market_snapshot: Dict[str, str],
    *,
    market_monitor_cards: List[Dict[str, Any]],
    market_alert_threshold_pct: Decimal,
    format_live_market_value: Any,
    heading: str = "Painel de Mercado",
    compact: bool = False,
    rail: bool = False,
) -> str:
    cards_html = []
    for card in market_monitor_cards:
        field = str(card.get("field") or "")
        label = str(card.get("label") or field)
        prefix = str(card.get("prefix") or "")
        suffix = str(card.get("suffix") or "")
        decimals = int(card.get("decimals") or 2)
        priority = str(card.get("priority") or "secondary")
        alert_enabled = "1" if bool(card.get("alert_enabled")) else "0"
        cards_html.append(
            f"""
            <div class='card market-card market-card-{escape(priority)}' data-market-field='{escape(field)}' data-alert-enabled='{alert_enabled}' data-prefix='{escape(prefix)}' data-suffix='{escape(suffix)}' data-decimals='{decimals}'>
                <div class='market-card-head'>
                    <small>{escape(label)}</small>
                    <span class='market-card-chip'>{'Monitoravel' if alert_enabled == '1' else 'Referencia'}</span>
                </div>
                <strong class='market-value'>{escape(format_live_market_value(str(market_snapshot.get(field) or ''), prefix=prefix, suffix=suffix, decimals=decimals))}</strong>
                <div class='market-card-meta'>
                    <span class='market-window-label' data-market-window>Janela 20s</span>
                    <span class='market-freshness' data-market-freshness>Ao vivo</span>
                </div>
                <div class='market-change neutral'><span class='market-arrow'>•</span><span class='market-delta'>Coletando janela</span></div>
                <svg class='market-sparkline' viewBox='0 0 120 36' preserveAspectRatio='none' aria-hidden='true'><polyline class='market-sparkline-line' points=''></polyline></svg>
            </div>
            """
        )
    panel_class = "panel section market-panel-live"
    if compact:
        panel_class += " compact-market-panel"
    if rail:
        panel_class += " market-rail-panel"
    description_html = (
        ""
        if compact or rail
        else "<p class='hint'>Monitor profissional com foco em variacao por janela, frescor do feed e prioridade operacional. O valor por grama considera onca troy ÷ 31.1035 com desconto tecnico de 10%.</p>"
    )
    status_html = "" if compact or rail else f"<p class='hint market-status'>{escape(market_snapshot['status'])}</p>"
    sources_html = (
        ""
        if compact or rail
        else "<p class='market-sources'>Fontes: <a href='https://api.gold-api.com' target='_blank' rel='noreferrer'>Gold-API</a> para XAU/USD spot, <a href='https://docs.awesomeapi.com.br/api-de-moedas' target='_blank' rel='noreferrer'>AwesomeAPI XAU/USD</a> como contingencia do ouro, e <a href='https://www.frankfurter.app' target='_blank' rel='noreferrer'>Frankfurter</a> para cambio.</p>"
    )
    return f"""
    <section class='{panel_class}' data-market-endpoint='/saas/market-snapshot' data-market-stream-endpoint='/saas/market-stream' data-market-alert-threshold='{str(market_alert_threshold_pct)}'>
        <div class='section-head'>
            <div>
                <h2>{escape(heading)}</h2>
                {description_html}
            </div>
            <div class='market-live-meta'>
                <span class='market-live-badge'>Tempo real</span>
                <span class='market-live-updated' data-market-updated>{escape(str(market_snapshot.get('updated_at_label') or 'agora'))}</span>
                <label class='market-threshold-control'>
                    <span>Alerta</span>
                    <select data-market-threshold-select>
                        <option value='0.25'>0,25%</option>
                        <option value='0.50' selected>0,50%</option>
                        <option value='1.00'>1,00%</option>
                        <option value='2.00'>2,00%</option>
                    </select>
                </label>
            </div>
        </div>
        <div class='market-grid'>
            {''.join(cards_html)}
        </div>
        <div class='market-alert-banner is-hidden' data-market-alert-banner>
            <strong>Alerta de mercado</strong>
            <span data-market-alert-text>Sem alertas relevantes.</span>
        </div>
        {status_html}
        {sources_html}
    </section>
    """


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


def _parse_google_news_feed(xml_text: str, topic: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    try:
        root = SafeElementTree.fromstring(xml_text)
    except SafeElementTree.ParseError:
        return items

    for node in root.findall("./channel/item")[:6]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub_date = (node.findtext("pubDate") or "").strip()
        source_node = node.find("source")
        source = (source_node.text or "") if source_node is not None else ""
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "published_at": pub_date,
                "source": source or "Google News",
                "topic": topic,
            }
        )
    return items


def _get_market_news() -> List[Dict[str, str]]:
    now = datetime.now(timezone.utc)
    expires_at = _MARKET_NEWS_CACHE.get("expires_at")
    cached = _MARKET_NEWS_CACHE.get("data")
    if isinstance(expires_at, datetime) and cached and expires_at > now:
        return cast(List[Dict[str, str]], cached)

    shared_cache = get_shared_cache()
    if shared_cache is not None:
        shared_news = shared_cache.get_json(_MARKET_NEWS_CACHE_KEY)
        if isinstance(shared_news, list) and shared_news:
            _MARKET_NEWS_CACHE["expires_at"] = now + timedelta(seconds=_MARKET_NEWS_CACHE_TTL_SECONDS)
            _MARKET_NEWS_CACHE["data"] = shared_news
            return cast(List[Dict[str, str]], shared_news)

    feeds = [
        ("ouro", "ouro OR gold price OR xau usd when:1d"),
        ("dolar", "dolar OR dollar OR usd brl when:1d"),
    ]
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()
    for topic, query in feeds:
        url = "https://news.google.com/rss/search?" + urlencode(
            {
                "q": query,
                "hl": "pt-BR",
                "gl": "BR",
                "ceid": "BR:pt-419",
            }
        )
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "CaixaWhatsApp/1.0", "Accept": "application/rss+xml"},
                timeout=4,
            )
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            xml_text = response.text
        except Exception as exc:
            logger.warning("Falha ao consultar feed de noticias (%s): %s", topic, exc)
            continue

        for item in _parse_google_news_feed(xml_text, topic):
            dedupe_key = f"{item['title']}|{item['source']}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(item)

    _MARKET_NEWS_CACHE["expires_at"] = now + timedelta(seconds=_MARKET_NEWS_CACHE_TTL_SECONDS)
    _MARKET_NEWS_CACHE["data"] = merged[:12]
    if shared_cache is not None:
        shared_cache.set_json(_MARKET_NEWS_CACHE_KEY, merged[:12], _MARKET_NEWS_CACHE_TTL_SECONDS)
    return cast(List[Dict[str, str]], _MARKET_NEWS_CACHE["data"])


async def _market_stream_events(
    request: Any,
    *,
    get_market_snapshot: Any,
    build_sse_message: Any,
    cache_ttl_seconds: int,
    stream_interval_seconds: float,
):
    while True:
        if await request.is_disconnected():
            break
        payload = {"ok": True, "snapshot": get_market_snapshot(), "cache_ttl_seconds": cache_ttl_seconds}
        yield build_sse_message(payload)
        await asyncio.sleep(stream_interval_seconds)


def _warm_web_runtime_caches(*, get_market_snapshot: Any, get_market_news: Any) -> None:
    try:
        get_market_snapshot()
    except Exception as exc:
        logger.warning("Falha ao aquecer cache de mercado: %s", exc)
    try:
        get_market_news()
    except Exception as exc:
        logger.warning("Falha ao aquecer cache de noticias: %s", exc)