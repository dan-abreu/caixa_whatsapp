import asyncio
import logging
from typing import Any


logger = logging.getLogger("caixa_whatsapp")


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