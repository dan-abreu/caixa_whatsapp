import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, cast
from urllib.parse import urlencode

import requests
from defusedxml import ElementTree as SafeElementTree

from app.shared_cache import get_shared_cache


logger = logging.getLogger("caixa_whatsapp")

_MARKET_NEWS_CACHE_TTL_SECONDS = int(os.getenv("MARKET_NEWS_CACHE_TTL_SECONDS", "900"))
_MARKET_NEWS_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_MARKET_NEWS_CACHE_KEY = "market:news"


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

    feeds = [("ouro", "ouro OR gold price OR xau usd when:1d"), ("dolar", "dolar OR dollar OR usd brl when:1d")]
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()
    for topic, query in feeds:
        url = "https://news.google.com/rss/search?" + urlencode(
            {"q": query, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}
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