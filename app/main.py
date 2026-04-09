import asyncio
import json
import os
import hmac
import base64
import hashlib
import logging
import threading
import re
import unicodedata
from html import escape
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional, Tuple, cast
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.middleware.base import RequestResponseEndpoint

from app.ai_service import AIServiceError, extract_message_data
from app.core.formatting import (
    _format_decimal_pt_br,
    _format_grams_pt_br,
    _format_percent_pt_br,
    _format_receipt_caixa_movement,
    _format_usd_pt_br,
    fx_rate,
    grams,
    money,
)
from app.database import DatabaseClient, DatabaseError
from app.multi_agent_system import MultiAgentRequest, MultiAgentResponse, run_multi_agent_orchestration
from app.services import dashboard_fragments as dashboard_fragments_service
from app.services import clients as clients_service
from app.services import dashboard_rendering as dashboard_rendering_service
from app.services import dashboard_trends as dashboard_trends_service
from app.services import lot_monitoring as lot_monitoring_service
from app.services import market as market_service
from app.services import operation_drafts as operation_drafts_service
from app.services import reporting as reporting_service
from app.services import receipts as receipts_service
from app.services import statements as statements_service
from app.services import view_caches as view_caches_service
from app.shared_cache import get_shared_cache


class WhatsAppWebhookPayload(BaseModel):
    remetente: str = Field(..., description="Telefone/ID do remetente")
    mensagem: str = Field(..., min_length=1, description="Texto recebido via WhatsApp")


class AIExtractedData(BaseModel):
    intencao: str
    ativo: Optional[str] = None
    quantidade: Optional[float] = None
    valor_informado: Optional[float] = None
    resposta: Optional[str] = None

    @field_validator("intencao")
    @classmethod
    def validate_intencao(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"registrar_operacao", "consultar_relatorio", "conversar"}:
            raise ValueError("intencao inválida")
        return normalized

    @field_validator("ativo")
    @classmethod
    def validate_ativo(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_ativo_nome(value)


app = FastAPI(title="Caixa Inteligente WhatsApp API", version="1.0.0")
logger = logging.getLogger("caixa_whatsapp")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
_DB_INSTANCE: Optional[DatabaseClient] = None
_DB_INSTANCE_LOCK = threading.Lock()
_STATIC_DIR = Path(__file__).with_name("static")
_STATIC_ASSET_VERSIONS: Dict[str, str] = {}
_SAAS_SESSION_COOKIE = os.getenv("SAAS_SESSION_COOKIE", "caixa_saas_session")
_SAAS_SESSION_TTL_SECONDS = int(os.getenv("SAAS_SESSION_TTL_SECONDS", "43200"))
_SAAS_COOKIE_SECURE = os.getenv("SAAS_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
_SAAS_AUTH_USER_CACHE_TTL_SECONDS = int(os.getenv("SAAS_AUTH_USER_CACHE_TTL_SECONDS", "15"))
_SAAS_AUTH_USER_CACHE: Dict[str, Dict[str, Any]] = {}
_MARKET_ALERT_THRESHOLD_PCT = Decimal(os.getenv("MARKET_ALERT_THRESHOLD_PCT", "0.50"))
_DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS = int(os.getenv("DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS", "15"))
_DASHBOARD_FRAGMENT_CACHE: Dict[str, Dict[str, Any]] = {}
_SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS", "15"))
_SAAS_STATEMENT_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}
_SAAS_RECENT_FX_CACHE_TTL_SECONDS = 15
_SAAS_RECENT_FX_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS", "30"))
_SAAS_RECEIPT_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}
_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS = float(os.getenv("SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS", "2"))
_SAAS_LOT_MONITOR_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS = int(os.getenv("REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS", "5"))
_REPORT_INVENTORY_STATUS_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_ADMIN_DASHBOARD_CACHE_TTL_SECONDS = int(os.getenv("ADMIN_DASHBOARD_CACHE_TTL_SECONDS", "5"))
_ADMIN_DASHBOARD_CACHE: Dict[str, Dict[str, Any]] = {}
_MARKET_STREAM_INTERVAL_SECONDS = float(os.getenv("MARKET_STREAM_INTERVAL_SECONDS", "1"))
_LOT_MONITOR_INTERVAL_SECONDS = int(os.getenv("LOT_MONITOR_INTERVAL_SECONDS", "300"))
_LOT_MONITOR_STREAM_INTERVAL_SECONDS = float(os.getenv("LOT_MONITOR_STREAM_INTERVAL_SECONDS", "1"))
_LOT_MONITOR_ENABLED = os.getenv("LOT_MONITOR_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
_DASHBOARD_FRAGMENT_CACHE_KEY_PREFIX = "saas:fragment"
_DASHBOARD_FRAGMENT_NEWS_NAME = "dashboard:news"
_DASHBOARD_FRAGMENT_MONITORS_NAME = "dashboard:monitors"
_DASHBOARD_FRAGMENT_INVENTORY_NAME = "dashboard:inventory"
_DASHBOARD_FRAGMENT_TREND_NAME = "dashboard:trend"
_DASHBOARD_FRAGMENT_SUMMARY_NAME = "dashboard:summary"
_DASHBOARD_FRAGMENT_PENDING_CLOSINGS_NAME = "dashboard:pending-closings"
_DASHBOARD_FRAGMENT_RECENT_OPERATIONS_NAME = "dashboard:recent-operations"
_SAAS_STATEMENT_CONTEXT_CACHE_KEY_PREFIX = "saas:statement"
_SAAS_RECEIPT_CONTEXT_CACHE_KEY_PREFIX = "saas:receipt"
_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_KEY_PREFIX = "saas:lot-monitor"
_ADMIN_DASHBOARD_CACHE_KEY_PREFIX = "admin:dashboard"
_LOT_MONITOR_THREAD: Optional[threading.Thread] = None
_LOT_MONITOR_STOP = threading.Event()
_LOT_MONITOR_LOCK = threading.Lock()
_MARKET_MONITOR_CARDS: List[Dict[str, Any]] = [
    {"field": "eur_brl_raw", "label": "EUR/REAL", "prefix": "", "suffix": "", "decimals": 4, "alert_enabled": False, "priority": "primary"},
    {"field": "usd_brl_raw", "label": "USD/BRL", "prefix": "", "suffix": "", "decimals": 4, "alert_enabled": False, "priority": "primary"},
    {"field": "xau_usd_raw", "label": "XAU/USD", "prefix": "USD ", "suffix": "", "decimals": 2, "alert_enabled": True, "priority": "secondary"},
    {"field": "grama_ref_raw", "label": "Grama referencia", "prefix": "USD ", "suffix": "/g", "decimals": 2, "alert_enabled": True, "priority": "secondary"},
]

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_MARKET_CACHE_TTL_SECONDS = market_service._MARKET_CACHE_TTL_SECONDS
_MARKET_CACHE = market_service._MARKET_CACHE
_MARKET_NEWS_CACHE_TTL_SECONDS = market_service._MARKET_NEWS_CACHE_TTL_SECONDS
_MARKET_NEWS_CACHE = market_service._MARKET_NEWS_CACHE
_MARKET_TICK_HISTORY = market_service._MARKET_TICK_HISTORY
_MARKET_SNAPSHOT_CACHE_KEY = market_service._MARKET_SNAPSHOT_CACHE_KEY
_MARKET_NEWS_CACHE_KEY = market_service._MARKET_NEWS_CACHE_KEY


def _asset_url(filename: str) -> str:
    cached_version = _STATIC_ASSET_VERSIONS.get(filename)
    if not cached_version:
        try:
            cached_version = str(int((_STATIC_DIR / filename).stat().st_mtime))
        except OSError:
            cached_version = "0"
        _STATIC_ASSET_VERSIONS[filename] = cached_version
    return f"/static/{quote(filename)}?v={cached_version}"


def _json_for_html_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


@app.middleware("http")
async def _add_performance_headers(request: Request, call_next: RequestResponseEndpoint):
    response = await call_next(request)
    path = request.url.path
    content_type = response.headers.get("content-type", "")
    if path.startswith("/static/"):
        response.headers.setdefault(
            "Cache-Control",
            "public, max-age=31536000, immutable, stale-while-revalidate=86400",
        )
        response.headers.setdefault("Vary", "Accept-Encoding")
    elif content_type.startswith("text/html"):
        response.headers.setdefault("Cache-Control", "private, no-store")
        response.headers.setdefault("Vary", "Cookie, Accept-Encoding")
    return response


def get_db() -> DatabaseClient:
    global _DB_INSTANCE
    if _DB_INSTANCE is not None:
        return _DB_INSTANCE

    try:
        with _DB_INSTANCE_LOCK:
            if _DB_INSTANCE is None:
                _DB_INSTANCE = DatabaseClient()
            return _DB_INSTANCE
    except DatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

def normalize_ativo_nome(raw: str) -> str:
    value = raw.strip().lower()
    aliases = {
        "ouro 18k": "Ouro",
        "grama": "Ouro",
        "usd": "USD",
        "dólar": "USD",
        "dolares": "USD",
        "dólares": "USD",
        "euro": "EUR",
        "srd": "SRD",
        "real": "BRL",
        "reais": "BRL",
    }
    return aliases.get(value, raw.strip())


def infer_tipo_operacao(mensagem: str) -> str:
    text = mensagem.lower()
    if "vendi" in text or "venda" in text:
        return "venda"
    if "cambio" in text or "câmbio" in text or "troca" in text:
        return "cambio"
    return "compra"


def _normalize_gold_type(raw: Any) -> str:
    text = _normalize_text(str(raw or "fundido"))
    if text in {"queimado", "queimada", "burned"}:
        return "queimado"
    return "fundido"


def _parse_gold_trade_profile(
    tipo_operacao: str,
    gold_type_raw: Any,
    quebra_raw: Any,
) -> Tuple[str, Optional[Decimal]]:
    gold_type = _normalize_gold_type(gold_type_raw)
    if tipo_operacao != "compra" or gold_type != "queimado":
        return gold_type, None

    quebra_text = str(quebra_raw or "").strip()
    if not quebra_text:
        raise HTTPException(status_code=400, detail="Informe a quebra quando a compra for queimado")

    quebra = _parse_decimal_web_field(quebra_text, "quebra")
    if quebra <= 0 or quebra > Decimal("100"):
        raise HTTPException(status_code=400, detail="Quebra deve estar entre 0 e 100")
    return gold_type, money(quebra)


_fetch_json_url = market_service._fetch_json_url
_extract_gold_api_xau_usd = market_service._extract_gold_api_xau_usd
_extract_awesomeapi_gold_price = market_service._extract_awesomeapi_gold_price
_build_market_snapshot_from_rates = market_service._build_market_snapshot_from_rates
_format_market_decimal = market_service._format_market_decimal
_format_live_market_value = market_service._format_live_market_value
_get_market_snapshot = market_service._get_market_snapshot
_get_market_history_series = market_service._get_market_history_series
_mean_decimal = market_service._mean_decimal
_build_market_trend_context = market_service._build_market_trend_context
_parse_google_news_feed = market_service._parse_google_news_feed
_get_market_news = market_service._get_market_news
_extract_lot_monitor_config = lot_monitoring_service._extract_lot_monitor_config
_build_lot_sell_signal = lot_monitoring_service._build_lot_sell_signal
_format_lot_signal_status = lot_monitoring_service._format_lot_signal_status
_build_web_lot_ai_alert_summary = lot_monitoring_service._build_web_lot_ai_alert_summary


def _render_market_panel_html(
    market_snapshot: Dict[str, str], heading: str = "Painel de Mercado", compact: bool = False, rail: bool = False
) -> str:
    return market_service._render_market_panel_html(
        market_snapshot,
        market_monitor_cards=_MARKET_MONITOR_CARDS,
        market_alert_threshold_pct=_MARKET_ALERT_THRESHOLD_PCT,
        format_live_market_value=_format_live_market_value,
        heading=heading,
        compact=compact,
        rail=rail,
    )


def _build_lot_monitor_snapshot_payload(db: DatabaseClient, session_user: Dict[str, Any]) -> Dict[str, Any]:
    return lot_monitoring_service._build_lot_monitor_snapshot_payload(
        db,
        session_user,
        build_snapshot_cache_key=_build_saas_lot_monitor_snapshot_cache_key,
        get_snapshot_cached=_get_saas_lot_monitor_snapshot_cached,
        set_snapshot_cached=_set_saas_lot_monitor_snapshot_cached,
        get_market_snapshot=_get_market_snapshot,
        build_market_trend_context=_build_market_trend_context,
        build_open_lot_market_context=_build_open_lot_market_context,
        build_web_lot_monitor_view_model=_build_web_lot_monitor_view_model,
    )


def _sse_message(data: Dict[str, Any], event: str = "snapshot") -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _market_stream_events(request: Request):
    async for item in market_service._market_stream_events(
        request,
        get_market_snapshot=_get_market_snapshot,
        build_sse_message=_sse_message,
        cache_ttl_seconds=_MARKET_CACHE_TTL_SECONDS,
        stream_interval_seconds=_MARKET_STREAM_INTERVAL_SECONDS,
    ):
        yield item


async def _lot_monitor_stream_events(request: Request, session_user: Dict[str, Any], db: DatabaseClient):
    async for item in lot_monitoring_service._lot_monitor_stream_events(
        request,
        session_user,
        db,
        build_lot_monitor_snapshot_payload=_build_lot_monitor_snapshot_payload,
        build_sse_message=_sse_message,
        stream_interval_seconds=_LOT_MONITOR_STREAM_INTERVAL_SECONDS,
    ):
        yield item


def _build_dashboard_fragment_cache_key(fragment_name: str, scope: str = "global") -> str:
    return dashboard_fragments_service._build_dashboard_fragment_cache_key(
        fragment_name,
        _DASHBOARD_FRAGMENT_CACHE_KEY_PREFIX,
        scope,
    )


def _get_dashboard_fragment_cached_html(cache_key: str, *, use_shared: bool = True) -> Optional[str]:
    return dashboard_fragments_service._get_dashboard_fragment_cached_html(
        cache_key,
        dashboard_fragment_cache=_DASHBOARD_FRAGMENT_CACHE,
        dashboard_fragment_cache_ttl_seconds=_DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS,
        get_shared_cache_backend=get_shared_cache,
        use_shared=use_shared,
    )


def _set_dashboard_fragment_cached_html(cache_key: str, html: str, *, use_shared: bool = True) -> None:
    dashboard_fragments_service._set_dashboard_fragment_cached_html(
        cache_key,
        html,
        dashboard_fragment_cache=_DASHBOARD_FRAGMENT_CACHE,
        dashboard_fragment_cache_ttl_seconds=_DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS,
        get_shared_cache_backend=get_shared_cache,
        use_shared=use_shared,
    )


def _render_cached_dashboard_fragment(cache_key: str, render_html: Callable[[], str], *, use_shared: bool = True) -> Response:
    return dashboard_fragments_service._render_cached_dashboard_fragment(
        cache_key,
        render_html,
        get_cached_html=_get_dashboard_fragment_cached_html,
        set_cached_html=_set_dashboard_fragment_cached_html,
        use_shared=use_shared,
    )


def _invalidate_dashboard_fragment_cache_keys(*cache_keys: str) -> None:
    dashboard_fragments_service._invalidate_dashboard_fragment_cache_keys(
        *cache_keys,
        dashboard_fragment_cache=_DASHBOARD_FRAGMENT_CACHE,
        get_shared_cache_backend=get_shared_cache,
    )


def _invalidate_dashboard_monitors_fragment_cache() -> None:
    dashboard_fragments_service._invalidate_dashboard_monitors_fragment_cache(
        dashboard_fragment_cache=_DASHBOARD_FRAGMENT_CACHE,
        dashboard_fragment_cache_key_prefix=_DASHBOARD_FRAGMENT_CACHE_KEY_PREFIX,
        dashboard_fragment_monitors_name=_DASHBOARD_FRAGMENT_MONITORS_NAME,
    )


def _invalidate_dashboard_operation_fragments() -> None:
    dashboard_fragments_service._invalidate_dashboard_operation_fragments(
        build_dashboard_fragment_cache_key=_build_dashboard_fragment_cache_key,
        invalidate_dashboard_fragment_cache_keys=_invalidate_dashboard_fragment_cache_keys,
        invalidate_dashboard_monitors_fragment_cache=_invalidate_dashboard_monitors_fragment_cache,
        dashboard_fragment_inventory_name=_DASHBOARD_FRAGMENT_INVENTORY_NAME,
        dashboard_fragment_trend_name=_DASHBOARD_FRAGMENT_TREND_NAME,
        dashboard_fragment_summary_name=_DASHBOARD_FRAGMENT_SUMMARY_NAME,
        dashboard_fragment_pending_closings_name=_DASHBOARD_FRAGMENT_PENDING_CLOSINGS_NAME,
        dashboard_fragment_recent_operations_name=_DASHBOARD_FRAGMENT_RECENT_OPERATIONS_NAME,
    )


def _build_saas_statement_context_cache_key(start_iso: str, end_iso: str) -> str:
    return view_caches_service._build_saas_statement_context_cache_key(_SAAS_STATEMENT_CONTEXT_CACHE_KEY_PREFIX, start_iso, end_iso)


def _get_saas_statement_context_cached(cache_key: str) -> Optional[Dict[str, Any]]:
    return view_caches_service._get_saas_statement_context_cached(
        cache_key,
        cache_store=_SAAS_STATEMENT_CONTEXT_CACHE,
        ttl_seconds=_SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS,
    )


def _set_saas_statement_context_cached(cache_key: str, context: Dict[str, Any]) -> Dict[str, Any]:
    return view_caches_service._set_saas_statement_context_cached(
        cache_key,
        context,
        cache_store=_SAAS_STATEMENT_CONTEXT_CACHE,
        ttl_seconds=_SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS,
    )


def _invalidate_statement_context_cache() -> None:
    view_caches_service._invalidate_statement_context_cache(cache_store=_SAAS_STATEMENT_CONTEXT_CACHE)


def _get_saas_recent_fx_cached() -> Optional[Dict[str, str]]:
    return view_caches_service._get_saas_recent_fx_cached(
        cache_store=_SAAS_RECENT_FX_CACHE,
        ttl_seconds=_SAAS_RECENT_FX_CACHE_TTL_SECONDS,
    )


def _set_saas_recent_fx_cached(snapshot: Dict[str, str]) -> Dict[str, str]:
    return view_caches_service._set_saas_recent_fx_cached(
        snapshot,
        cache_store=_SAAS_RECENT_FX_CACHE,
        ttl_seconds=_SAAS_RECENT_FX_CACHE_TTL_SECONDS,
    )


def _invalidate_recent_fx_map_cache() -> None:
    view_caches_service._invalidate_recent_fx_map_cache(cache_store=_SAAS_RECENT_FX_CACHE)


def _build_saas_receipt_context_cache_key(operation_id: int) -> str:
    return view_caches_service._build_saas_receipt_context_cache_key(_SAAS_RECEIPT_CONTEXT_CACHE_KEY_PREFIX, operation_id)


def _get_saas_receipt_context_cached(cache_key: str) -> Optional[Dict[str, Any]]:
    return view_caches_service._get_saas_receipt_context_cached(
        cache_key,
        cache_store=_SAAS_RECEIPT_CONTEXT_CACHE,
        ttl_seconds=_SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS,
    )


def _set_saas_receipt_context_cached(cache_key: str, context: Dict[str, Any]) -> Dict[str, Any]:
    return view_caches_service._set_saas_receipt_context_cached(
        cache_key,
        context,
        cache_store=_SAAS_RECEIPT_CONTEXT_CACHE,
        ttl_seconds=_SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS,
    )


def _invalidate_receipt_context_cache() -> None:
    view_caches_service._invalidate_receipt_context_cache(cache_store=_SAAS_RECEIPT_CONTEXT_CACHE)


def _build_saas_lot_monitor_snapshot_cache_key(phone: str) -> str:
    return view_caches_service._build_saas_lot_monitor_snapshot_cache_key(
        phone,
        cache_key_prefix=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_KEY_PREFIX,
        normalize_phone=_normalize_user_phone,
    )


def _get_saas_lot_monitor_snapshot_cached(cache_key: str) -> Optional[Dict[str, Any]]:
    return view_caches_service._get_saas_lot_monitor_snapshot_cached(
        cache_key,
        cache_store=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE,
        ttl_seconds=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS,
    )


def _set_saas_lot_monitor_snapshot_cached(cache_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return view_caches_service._set_saas_lot_monitor_snapshot_cached(
        cache_key,
        payload,
        cache_store=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE,
        ttl_seconds=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS,
    )


def _invalidate_lot_monitor_snapshot_cache() -> None:
    view_caches_service._invalidate_lot_monitor_snapshot_cache(cache_store=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE)


def _build_admin_dashboard_cache_key(day_label: str) -> str:
    return view_caches_service._build_admin_dashboard_cache_key(_ADMIN_DASHBOARD_CACHE_KEY_PREFIX, day_label)


def _get_inventory_status_report_cached() -> Optional[Dict[str, Any]]:
    return view_caches_service._get_inventory_status_report_cached(
        cache_store=_REPORT_INVENTORY_STATUS_CACHE,
        ttl_seconds=_REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS,
    )


def _set_inventory_status_report_cached(payload: Dict[str, Any]) -> Dict[str, Any]:
    return view_caches_service._set_inventory_status_report_cached(
        payload,
        cache_store=_REPORT_INVENTORY_STATUS_CACHE,
        ttl_seconds=_REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS,
    )


def _get_admin_dashboard_cached(cache_key: str) -> Optional[str]:
    return view_caches_service._get_admin_dashboard_cached(
        cache_key,
        cache_store=_ADMIN_DASHBOARD_CACHE,
        ttl_seconds=_ADMIN_DASHBOARD_CACHE_TTL_SECONDS,
    )


def _set_admin_dashboard_cached(cache_key: str, html: str) -> str:
    return view_caches_service._set_admin_dashboard_cached(
        cache_key,
        html,
        cache_store=_ADMIN_DASHBOARD_CACHE,
        ttl_seconds=_ADMIN_DASHBOARD_CACHE_TTL_SECONDS,
    )


def _invalidate_reporting_cache() -> None:
    view_caches_service._invalidate_reporting_cache(
        inventory_status_cache=_REPORT_INVENTORY_STATUS_CACHE,
        admin_dashboard_cache=_ADMIN_DASHBOARD_CACHE,
    )


def _invalidate_operation_related_view_caches() -> None:
    _invalidate_dashboard_operation_fragments()
    _invalidate_statement_context_cache()
    _invalidate_recent_fx_map_cache()
    _invalidate_receipt_context_cache()
    _invalidate_lot_monitor_snapshot_cache()
    _invalidate_reporting_cache()


def _render_market_news_panel_html(news_items: List[Dict[str, str]], limit: int = 6) -> str:
    return dashboard_rendering_service._render_market_news_panel_html(news_items, limit=limit)


def _render_lot_monitor_cards(
    entries: List[Dict[str, Any]],
    page_name: str,
    empty_message: str,
    default_alert_phone: str,
) -> str:
    return lot_monitoring_service._render_lot_monitor_cards(
        entries,
        page_name,
        empty_message,
        default_alert_phone,
    )


def _render_recent_operations_rows(
    transactions: List[Dict[str, Any]],
    empty_message: str = "Nenhuma operação recente.",
) -> str:
    return dashboard_rendering_service._render_recent_operations_rows(transactions, empty_message=empty_message)


def _render_open_fechamentos_rows(
    transactions: List[Dict[str, Any]],
    limit: int = 8,
    empty_message: str = "Nenhum fechamento parcial em aberto nos movimentos recentes.",
) -> str:
    return dashboard_rendering_service._render_open_fechamentos_rows(
        transactions,
        collect_open_fechamentos=_collect_open_fechamentos,
        limit=limit,
        empty_message=empty_message,
    )


def _render_dashboard_pending_closings_html(transactions: List[Dict[str, Any]]) -> str:
    return dashboard_rendering_service._render_dashboard_pending_closings_html(
        transactions,
        collect_open_fechamentos=_collect_open_fechamentos,
    )


def _render_dashboard_recent_operations_html(transactions: List[Dict[str, Any]]) -> str:
    return dashboard_rendering_service._render_dashboard_recent_operations_html(transactions)


def _render_dashboard_inventory_html(inventory: Dict[str, Any], lot_market_context: Dict[str, Any]) -> str:
    return dashboard_rendering_service._render_dashboard_inventory_html(inventory, lot_market_context)


def _render_dashboard_trend_html(transactions: List[Dict[str, Any]]) -> str:
    return dashboard_trends_service._render_dashboard_trend_html(transactions)


def _render_dashboard_summary_html(
    summary: Dict[str, Any],
    gross_grams_today: Decimal,
    ouro_proprio: Decimal,
) -> str:
    return dashboard_rendering_service._render_dashboard_summary_html(
        summary,
        gross_grams_today,
        ouro_proprio,
        format_caixa_movement=_format_caixa_movement,
    )


def _build_web_lot_ai_alerts(lot_market_context: Dict[str, Any], market_trend: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    return lot_monitoring_service._build_web_lot_ai_alerts(
        lot_market_context,
        market_trend,
        build_lot_sell_signal=_build_lot_sell_signal,
        format_lot_signal_status=_format_lot_signal_status,
        limit=limit,
    )


def _build_web_lot_monitor_view_model(
    lot_market_context: Dict[str, Any],
    market_trend: Dict[str, Any],
    default_alert_phone: str = "",
    entry_limit: int = 8,
    alert_limit: int = 4,
) -> Dict[str, Any]:
    return lot_monitoring_service._build_web_lot_monitor_view_model(
        lot_market_context,
        market_trend,
        build_lot_sell_signal=_build_lot_sell_signal,
        format_lot_signal_status=_format_lot_signal_status,
        build_alert_summary=_build_web_lot_ai_alert_summary,
        default_alert_phone=default_alert_phone,
        entry_limit=entry_limit,
        alert_limit=alert_limit,
    )


def _build_web_lot_monitor_entries(
    lot_market_context: Dict[str, Any],
    market_trend: Dict[str, Any],
    default_alert_phone: str = "",
    limit: int = 8,
) -> List[Dict[str, Any]]:
    return lot_monitoring_service._build_web_lot_monitor_entries(
        lot_market_context,
        market_trend,
        build_lot_sell_signal=_build_lot_sell_signal,
        format_lot_signal_status=_format_lot_signal_status,
        default_alert_phone=default_alert_phone,
        limit=limit,
    )


def _normalize_whatsapp_to(raw_phone: str) -> str:
    return lot_monitoring_service._normalize_whatsapp_to(
        raw_phone,
        normalize_user_phone=_normalize_user_phone,
    )


def _send_outbound_whatsapp_alert(phone: str, message: str) -> bool:
    return lot_monitoring_service._send_outbound_whatsapp_alert(
        phone,
        message,
        normalize_whatsapp_to=_normalize_whatsapp_to,
        logger=logger,
    )


def _build_lot_alert_message(lot: Dict[str, Any], signal: Dict[str, Any], market_trend: Dict[str, Any]) -> str:
    return lot_monitoring_service._build_lot_alert_message(lot, signal, market_trend)


def _run_lot_monitor_cycle() -> None:
    lot_monitoring_service._run_lot_monitor_cycle(
        get_db=get_db,
        get_market_snapshot=_get_market_snapshot,
        build_market_trend_context=_build_market_trend_context,
        build_open_lot_market_context=_build_open_lot_market_context,
        build_lot_sell_signal=_build_lot_sell_signal,
        extract_lot_monitor_config=_extract_lot_monitor_config,
        build_lot_alert_message=_build_lot_alert_message,
        send_outbound_whatsapp_alert=_send_outbound_whatsapp_alert,
        logger=logger,
    )


def _lot_monitor_worker() -> None:
    lot_monitoring_service._lot_monitor_worker(
        stop_event=_LOT_MONITOR_STOP,
        interval_seconds=_LOT_MONITOR_INTERVAL_SECONDS,
        run_lot_monitor_cycle=_run_lot_monitor_cycle,
        logger=logger,
    )


def _warm_web_runtime_caches() -> None:
    market_service._warm_web_runtime_caches(
        get_market_snapshot=_get_market_snapshot,
        get_market_news=_get_market_news,
    )


@app.on_event("startup")
def _start_lot_monitor_background() -> None:
    global _LOT_MONITOR_THREAD
    if _LOT_MONITOR_ENABLED:
        with _LOT_MONITOR_LOCK:
            if not (_LOT_MONITOR_THREAD and _LOT_MONITOR_THREAD.is_alive()):
                _LOT_MONITOR_STOP.clear()
                _LOT_MONITOR_THREAD = threading.Thread(target=_lot_monitor_worker, name="lot-monitor", daemon=True)
                _LOT_MONITOR_THREAD.start()
    threading.Thread(target=_warm_web_runtime_caches, name="web-cache-warmup", daemon=True).start()


@app.on_event("shutdown")
def _stop_lot_monitor_background() -> None:
    _LOT_MONITOR_STOP.set()


def _payment_fx_prompt_label(moeda: str) -> str:
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "EUR":
        return "1 EUR = quantos USD?"
    if moeda_up in {"SRD", "BRL"}:
        return f"1 USD = quantos {moeda_up}?"
    return "Câmbio para USD"


def _display_cambio_for_web_input(moeda: str, cambio_para_usd: Decimal) -> str:
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "USD":
        return "1"
    normalized = fx_rate(cambio_para_usd)
    if normalized <= 0:
        return ""
    if moeda_up == "EUR":
        return _format_decimal_for_form(fx_rate(Decimal("1") / normalized), 4)
    return _format_decimal_for_form(normalized, 4)


def _payment_input_to_usd(moeda: str, valor_moeda: Decimal, cambio_informado: Decimal) -> Decimal:
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "USD":
        return money(valor_moeda)
    if cambio_informado <= 0:
        return Decimal("0")
    if moeda_up == "EUR":
        return money(valor_moeda * cambio_informado)
    return money(valor_moeda / cambio_informado)


def _compute_sale_profit_reference(
    db: DatabaseClient,
    ativo_id: int,
    peso: Decimal,
    total_pago_usd: Decimal,
) -> Optional[Dict[str, str]]:
    taxa_atual = db.get_taxa_atual(ativo_id)
    if not taxa_atual:
        return None

    preco_compra_raw = taxa_atual.get("preco_compra")
    if preco_compra_raw is None:
        return None

    try:
        preco_compra_ref = Decimal(str(preco_compra_raw))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if preco_compra_ref <= 0:
        return None

    custo_ref_usd = money(peso * preco_compra_ref)
    lucro_ref_usd = money(total_pago_usd - custo_ref_usd)
    return {
        "preco_compra_ref_usd": str(money(preco_compra_ref)),
        "custo_ref_usd": str(custo_ref_usd),
        "lucro_ref_usd": str(lucro_ref_usd),
    }


def _attach_sale_profit_reference(db: DatabaseClient, contexto: Dict[str, Any]) -> None:
    if str(contexto.get("tipo_operacao", "")).lower() != "venda":
        return

    try:
        peso = Decimal(str(contexto.get("peso", "0")))
        total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
    except (InvalidOperation, TypeError, ValueError):
        return

    if peso <= 0 or total_pago <= 0:
        return

    ativo = db.get_ativo_by_nome("Ouro")
    if not ativo:
        ativo = db.get_ativo_by_nome("Ouro 24k")
    if not ativo:
        return

    profit_ref = _compute_sale_profit_reference(db, int(ativo["id"]), peso, total_pago)
    if profit_ref:
        contexto.update(profit_ref)

    inventory_txs = db.get_gold_inventory_transactions()
    lots = _build_fifo_inventory_lots(inventory_txs)
    fifo_result = _preview_fifo_sale_consumption(lots, peso)
    consumed_grams = Decimal(str(fifo_result.get("consumed_grams") or "0"))
    consumed_cost = Decimal(str(fifo_result.get("consumed_cost_usd") or "0"))
    shortfall = Decimal(str(fifo_result.get("shortfall_grams") or "0"))
    if consumed_grams > 0 and shortfall == 0:
        contexto.update(
            {
                "profit_method": "fifo_real",
                "custo_fifo_usd": str(money(consumed_cost)),
                "lucro_real_usd": str(money(total_pago - consumed_cost)),
                "consumo_fifo": fifo_result.get("breakdown", []),
            }
        )
    elif shortfall > 0:
        contexto["profit_method"] = "fifo_insufficient_stock"
        contexto["fifo_shortfall_grams"] = str(shortfall)



def _build_fechamento_status(item: Dict[str, Any]) -> Dict[str, Decimal | str | bool]:
    peso = Decimal(str(item.get("peso") or "0"))
    fechamento = Decimal(str(item.get("fechamento_gramas") or peso or "0"))
    fechamento_tipo = str(item.get("fechamento_tipo") or "total").lower()
    if fechamento <= 0 and peso > 0:
        fechamento = peso
    fechado = min(fechamento, peso) if peso > 0 else Decimal("0")
    aberto = max(Decimal("0"), peso - fechado)
    is_partial = fechamento_tipo == "parcial" or aberto > 0
    return {
        "peso": peso,
        "fechado": fechado,
        "aberto": aberto,
        "tipo": fechamento_tipo,
        "is_partial": is_partial,
    }


def _collect_open_fechamentos(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    open_items: List[Dict[str, Any]] = []
    for item in transactions:
        status = _build_fechamento_status(item)
        if not bool(status["is_partial"]):
            continue
        if Decimal(str(status["aberto"])) <= 0:
            continue
        open_items.append({**item, "fechamento_status": status})
    open_items.sort(key=lambda row: str(row.get("criado_em") or ""), reverse=True)
    return open_items


def _sum_open_fechamento_grams(transactions: List[Dict[str, Any]]) -> Decimal:
    total_open = Decimal("0")
    for item in transactions:
        status = cast(Dict[str, Any], item.get("fechamento_status") or _build_fechamento_status(item))
        total_open += Decimal(str(status.get("aberto") or "0"))
    return total_open


def _build_gold_caixa_metrics(saldo_xau: Decimal, transactions: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    open_fechamentos = _collect_open_fechamentos(transactions)
    ouro_pendente = _sum_open_fechamento_grams(open_fechamentos)
    return _build_gold_caixa_metrics_from_pending_grams(saldo_xau, ouro_pendente)


def _build_gold_caixa_metrics_from_pending_grams(saldo_xau: Decimal, ouro_pendente: Decimal) -> Dict[str, Decimal]:
    ouro_proprio = saldo_xau - ouro_pendente
    return {
        "ouro_em_caixa": saldo_xau,
        "ouro_pendente": ouro_pendente,
        "ouro_proprio": ouro_proprio,
    }


def _build_fifo_inventory_lots(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lots: List[Dict[str, Any]] = []
    ordered = sorted(
        transactions,
        key=lambda tx: (
            str(tx.get("criado_em") or ""),
            int(tx.get("id") or 0),
        ),
    )
    for tx in ordered:
        tipo = str(tx.get("tipo_operacao") or "").lower()
        if tipo not in {"compra", "venda"}:
            continue

        try:
            peso = Decimal(str(tx.get("peso") or "0"))
        except (InvalidOperation, TypeError, ValueError):
            continue

        if peso <= 0:
            continue

        if tipo == "compra":
            try:
                unit_cost = Decimal(str(tx.get("preco_usd") or "0"))
            except (InvalidOperation, TypeError, ValueError):
                unit_cost = Decimal("0")
            lots.append(
                {
                    "source_id": int(tx.get("id") or 0),
                    "criado_em": str(tx.get("criado_em") or ""),
                    "initial_grams": peso,
                    "remaining_grams": peso,
                    "unit_cost_usd": unit_cost,
                    "teor": str(tx.get("teor") or ""),
                    "gold_type": str(tx.get("gold_type") or ""),
                    "quebra": str(tx.get("quebra") or ""),
                    "pessoa": str(tx.get("pessoa") or ""),
                }
            )
            continue

        remaining_sale = peso
        while remaining_sale > 0 and lots:
            head = lots[0]
            head_remaining = Decimal(str(head.get("remaining_grams") or "0"))
            if head_remaining <= 0:
                lots.pop(0)
                continue
            consumed = min(head_remaining, remaining_sale)
            head["remaining_grams"] = str(head_remaining - consumed)
            remaining_sale -= consumed
            if Decimal(str(head.get("remaining_grams") or "0")) <= 0:
                lots.pop(0)

    normalized: List[Dict[str, Any]] = []
    for lot in lots:
        remaining = Decimal(str(lot.get("remaining_grams") or "0"))
        if remaining > 0:
            normalized.append(
                {
                    "source_id": int(lot.get("source_id") or 0),
                    "criado_em": str(lot.get("criado_em") or ""),
                    "initial_grams": str(Decimal(str(lot.get("initial_grams") or remaining))),
                    "remaining_grams": str(remaining),
                    "unit_cost_usd": str(Decimal(str(lot.get("unit_cost_usd") or "0"))),
                    "teor": str(lot.get("teor") or ""),
                    "gold_type": str(lot.get("gold_type") or ""),
                    "quebra": str(lot.get("quebra") or ""),
                    "pessoa": str(lot.get("pessoa") or ""),
                }
            )
    return normalized


def _preview_fifo_sale_consumption(
    lots: List[Dict[str, Any]],
    peso_venda: Decimal,
) -> Dict[str, Any]:
    remaining_sale = peso_venda
    consumed_cost = Decimal("0")
    consumed_grams = Decimal("0")
    breakdown: List[Dict[str, Any]] = []

    working_lots = [dict(lot) for lot in lots]
    for lot in working_lots:
        if remaining_sale <= 0:
            break
        lot_remaining = Decimal(str(lot.get("remaining_grams") or "0"))
        if lot_remaining <= 0:
            continue
        unit_cost = Decimal(str(lot.get("unit_cost_usd") or "0"))
        consumed = min(lot_remaining, remaining_sale)
        cost_usd = money(consumed * unit_cost)
        breakdown.append(
            {
                "source_id": int(lot.get("source_id") or 0),
                "grams": str(consumed),
                "unit_cost_usd": str(money(unit_cost)),
                "cost_usd": str(cost_usd),
            }
        )
        consumed_cost += cost_usd
        consumed_grams += consumed
        remaining_sale -= consumed

    return {
        "consumed_grams": consumed_grams,
        "consumed_cost_usd": money(consumed_cost),
        "shortfall_grams": remaining_sale if remaining_sale > 0 else Decimal("0"),
        "breakdown": breakdown,
    }


def _compute_inventory_metrics(transactions: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    lots = _build_fifo_inventory_lots(transactions)
    total_grams = sum((Decimal(str(lot.get("remaining_grams") or "0")) for lot in lots), Decimal("0"))
    total_cost = sum(
        (
            Decimal(str(lot.get("remaining_grams") or "0"))
            * Decimal(str(lot.get("unit_cost_usd") or "0"))
            for lot in lots
        ),
        Decimal("0"),
    )
    avg_cost = money(total_cost / total_grams) if total_grams > 0 else Decimal("0")
    return {
        "available_grams": total_grams,
        "inventory_cost_usd": money(total_cost),
        "avg_cost_usd_per_gram": avg_cost,
    }


def _build_open_lot_market_context(open_lots: List[Dict[str, Any]], market_snapshot: Dict[str, str]) -> Dict[str, Any]:
    return lot_monitoring_service._build_open_lot_market_context(
        open_lots,
        market_snapshot,
        format_decimal_for_form=_format_decimal_for_form,
    )


def _build_operation_lot_market_context(open_lots: List[Dict[str, Any]], market_snapshot: Dict[str, str]) -> Dict[str, Any]:
    return lot_monitoring_service._build_operation_lot_market_context(
        open_lots,
        market_snapshot,
        format_decimal_for_form=_format_decimal_for_form,
    )


def _project_caixa_balances(
    current_saldos: Dict[str, Any],
    tipo_operacao: str,
    peso_gramas: Decimal,
    pagamentos: List[Dict[str, Any]],
) -> Dict[str, Decimal]:
    projected = {
        moeda.upper(): Decimal(str(valor))
        for moeda, valor in current_saldos.items()
    }
    for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]:
        projected.setdefault(moeda, Decimal("0"))

    if peso_gramas > 0:
        projected["XAU"] += peso_gramas if tipo_operacao == "compra" else -peso_gramas

    for pagamento in pagamentos:
        moeda = str(pagamento.get("moeda") or "USD").upper()
        valor_moeda = Decimal(str(pagamento.get("valor_moeda") or "0"))
        if moeda not in projected or valor_moeda == 0:
            continue
        projected[moeda] += -valor_moeda if tipo_operacao == "compra" else valor_moeda

    return projected


def _find_negative_caixa_balances(projected_saldos: Dict[str, Decimal]) -> List[Tuple[str, Decimal]]:
    negatives: List[Tuple[str, Decimal]] = []
    for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]:
        saldo = projected_saldos.get(moeda, Decimal("0"))
        if saldo < 0:
            negatives.append((moeda, saldo))
    return negatives


def _format_negative_caixa_lines(negatives: List[Tuple[str, Decimal]]) -> List[str]:
    lines: List[str] = []
    for moeda, saldo in negatives:
        lines.append(f"- {moeda}: {_format_caixa_movement(moeda, saldo)}")
    return lines


def _should_reset_guided_session_for_message(message: str) -> bool:
    text = _normalize_text(message)
    if _looks_like_new_operation_start(message) or _is_greeting(message):
        return True
    global_commands = ["menu", "caixa", "extrato", "ajuda", "help", "taxa", "relatorio", "relatório"]
    return any(text.startswith(cmd) for cmd in global_commands)


def validate_webhook_token(token: Optional[str]) -> None:
    expected = os.getenv("WEBHOOK_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="Token do sistema nao configurado")
    if token != expected:
        raise HTTPException(status_code=401, detail="Token invalido")


def _twiml_message(text: str) -> Response:
    safe_text = escape(text)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe_text}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _twiml_empty_response() -> Response:
    xml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=xml, media_type="application/xml")


def _should_suppress_twilio_reply(message: str) -> bool:
    mode = os.getenv("TWILIO_REPLY_MODE", "normal").strip().lower()
    if mode == "silent_all":
        return True
    if mode != "silent_prefix":
        return False

    prefix = os.getenv("TWILIO_SILENT_PREFIX", "debug:").strip().lower()
    if not prefix:
        return False
    return message.strip().lower().startswith(prefix)


@app.get("/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/saas", status_code=307)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/menu")
def menu() -> Dict[str, Any]:
    return {
        "titulo": "Central de Comandos",
        "versao": "1.0.0",
        "funcionalidades": [
            {
                "id": 1,
                "nome": "Registrar operacao de compra ou venda",
                "intencao": "registrar_operacao",
                "descricao": "Executa o registro operacional de ouro por fluxo assistido.",
                "exemplos": [
                    "compra",
                    "venda",
                    "compra ouro 2g"
                ],
                "resposta_esperada": "Retorna o comprovante operacional da transacao."
            },
            {
                "id": 2,
                "nome": "Consultar posicao de caixa",
                "intencao": "consultar_relatorio",
                "descricao": "Apresenta a posicao atual por moeda e a situacao do ouro em caixa.",
                "exemplos": [
                    "caixa",
                    "caixa eur",
                    "caixa srd",
                    "caixa xau"
                ],
                "resposta_esperada": "Retorna a posicao consolidada atual."
            },
            {
                "id": 3,
                "nome": "Consultar extrato analitico",
                "intencao": "extrato",
                "descricao": "Lista as operacoes do periodo com detalhamento de cada lancamento.",
                "exemplos": [
                    "extrato",
                    "extrato hoje",
                    "extrato semana"
                ],
                "resposta_esperada": "Retorna o extrato detalhado em formato analitico."
            },
            {
                "id": 4,
                "nome": "Ajustar operacao",
                "intencao": "editar_operacao",
                "descricao": "Permite ajustar preco, quantidade, moeda, valor na moeda ou cambio de uma operacao existente.",
                "exemplos": [
                    "editar 123 preco 110",
                    "editar 123 quantidade 2.5"
                ],
                "resposta_esperada": "Confirma os campos atualizados na operacao."
            },
            {
                "id": 5,
                "nome": "Cancelar operacao",
                "intencao": "cancelar_operacao",
                "descricao": "Inativa a operacao selecionada no controle operacional.",
                "exemplos": [
                    "cancelar 123"
                ],
                "resposta_esperada": "Confirma o cancelamento operacional."
            }
        ],
        "ativos_disponiveis": [
            {"nome": "ouro", "aliases": ["gold", "oro", "or"]},
            {"nome": "usd", "aliases": ["dollar", "dolar"]},
            {"nome": "eur", "aliases": ["euro"]},
            {"nome": "srd", "aliases": []},
            {"nome": "brl", "aliases": ["real", "reais"]}
        ],
        "dicas": [
            "Utilize instrucoes objetivas.",
            "Informe um dado por vez durante o fluxo.",
            "Em caso de duvida, envie: menu.",
            "Para retornar uma etapa, envie: voltar."
        ]
    }
_ERROS_AMIGAVEIS: Dict[int, str] = {
    400: "Solicitacao nao compreendida. Utilize, por exemplo: compra | venda | caixa | extrato | taxa ouro 70.00",
    401: "Acesso negado. Token de autenticacao invalido.",
    403: "Permissao insuficiente para esta operacao.",
    404: "Recurso nao localizado. Envie 'menu' para consultar as opcoes disponiveis.",
    422: "Dados insuficientes para processamento. Reformule a mensagem com maior objetividade.",
    500: "Falha interna de processamento. Tente novamente em alguns segundos.",
    502: "O servico de IA nao respondeu no momento. Tente novamente.",
}

# Fallback de idempotência para ambiente sem migração aplicada.
_IDEMPOTENCY_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}


def _env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_float(name: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    raw = os.getenv(name, str(default)).strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


_MOEDAS_SUPORTADAS = ["USD", "SRD", "EUR", "BRL"]
_RISK_DIFF_LIMIT_USD = Decimal(os.getenv("RISK_DIFF_LIMIT_USD", "250"))
_GUIDED_SESSION_IDLE_MINUTES = int(os.getenv("GUIDED_SESSION_IDLE_MINUTES", "5"))
_MULTI_AGENT_AUTO_ENABLED = os.getenv("MULTI_AGENT_AUTO_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
_MULTI_AGENT_AUTO_MIN_USD = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_USD", "500"))
_MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS", "10"))
_AI_CONF_PRESETS: Dict[str, Dict[str, float]] = {
    "balanced": {
        "samples_target": 300,
        "risk_weight": 0.7,
        "failsafe_weight": 1.3,
        "weight_maturity": 45,
        "weight_stability": 45,
        "weight_alerts": 10,
        "band_excellent": 85,
        "band_good": 70,
        "band_moderate": 50,
    },
    "conservative": {
        "samples_target": 450,
        "risk_weight": 0.9,
        "failsafe_weight": 1.8,
        "weight_maturity": 35,
        "weight_stability": 55,
        "weight_alerts": 10,
        "band_excellent": 90,
        "band_good": 78,
        "band_moderate": 60,
    },
    "aggressive": {
        "samples_target": 220,
        "risk_weight": 0.55,
        "failsafe_weight": 1.0,
        "weight_maturity": 55,
        "weight_stability": 35,
        "weight_alerts": 10,
        "band_excellent": 82,
        "band_good": 66,
        "band_moderate": 45,
    },
}
_ai_conf_profile_setting = os.getenv("AI_CONF_PROFILE", "balanced").strip().lower()
if _ai_conf_profile_setting not in {*_AI_CONF_PRESETS.keys(), "auto"}:
    _ai_conf_profile_setting = "balanced"
_AI_CONF_PROFILE_SETTING = _ai_conf_profile_setting


def _resolve_auto_ai_conf_profile(total_samples: int) -> str:
    if total_samples >= 300:
        return "conservative"
    if total_samples >= 30:
        return "balanced"
    return "aggressive"


def _get_ai_conf_config(total_samples: int) -> Dict[str, Any]:
    selected_profile = _AI_CONF_PROFILE_SETTING
    if selected_profile == "auto":
        selected_profile = _resolve_auto_ai_conf_profile(total_samples)

    defaults = _AI_CONF_PRESETS[selected_profile]
    samples_target = _env_int("AI_CONF_SAMPLES_TARGET", int(defaults["samples_target"]), minimum=50, maximum=5000)
    risk_weight = _env_float("AI_CONF_RISK_WEIGHT", float(defaults["risk_weight"]), minimum=0.0, maximum=5.0)
    failsafe_weight = _env_float("AI_CONF_FAILSAFE_WEIGHT", float(defaults["failsafe_weight"]), minimum=0.0, maximum=5.0)
    weight_maturity = _env_float("AI_CONF_WEIGHT_MATURITY", float(defaults["weight_maturity"]), minimum=0.0, maximum=100.0)
    weight_stability = _env_float("AI_CONF_WEIGHT_STABILITY", float(defaults["weight_stability"]), minimum=0.0, maximum=100.0)
    weight_alerts = _env_float("AI_CONF_WEIGHT_ALERTS", float(defaults["weight_alerts"]), minimum=0.0, maximum=100.0)
    band_excellent = _env_int("AI_CONF_BAND_EXCELLENT", int(defaults["band_excellent"]), minimum=1, maximum=100)
    band_good = _env_int("AI_CONF_BAND_GOOD", int(defaults["band_good"]), minimum=1, maximum=100)
    band_moderate = _env_int("AI_CONF_BAND_MODERATE", int(defaults["band_moderate"]), minimum=1, maximum=100)

    return {
        "profile_setting": _AI_CONF_PROFILE_SETTING,
        "profile_effective": selected_profile,
        "samples_target": samples_target,
        "risk_weight": risk_weight,
        "failsafe_weight": failsafe_weight,
        "weight_maturity": weight_maturity,
        "weight_stability": weight_stability,
        "weight_alerts": weight_alerts,
        "band_excellent": band_excellent,
        "band_good": band_good,
        "band_moderate": band_moderate,
    }
_GUIDED_FLOW_STATES = {
    "await_menu_option",
    "await_menu_tipo_operacao",
    "await_nome_usuario",
    "await_caixa_detalhe",
    "await_origem",
    "await_teor",
    "await_peso",
    "await_preco_moeda",
    "await_preco_usd",
    "await_preco_cambio",
    "await_cambio_base_para_total",
    "await_moedas",
    "await_valor_moeda",
    "await_cambio_moeda_pre_valor",
    "await_cambio_moeda",
    "await_fechamento_gramas",
    "await_fechamento_tipo",
    "await_pessoa",
    "await_forma_pagamento",
    "await_observacoes",
    "await_confirmacao",
    "await_resume_confirmacao",
    "await_preco_simples",
    "await_moeda_simples",
    "await_cambio_simples",
    "await_extrato_periodo",
    "await_extrato_data_inicio",
    "await_extrato_data_fim",
}


def _normalize_text(value: str) -> str:
    lowered = value.strip().lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def parse_decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail=f"Valor invalido para {field_name}")
    if not parsed.is_finite():
        raise HTTPException(status_code=400, detail=f"Valor invalido para {field_name}")
    return parsed


def _parse_decimal_from_text(value: str, field_name: str) -> Decimal:
    cleaned = value.strip().replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    if cleaned in {"", "-", ".", "-.", ".-"}:
        return Decimal("-1")
    try:
        return parse_decimal(cleaned, field_name)
    except HTTPException:
        return Decimal("-1")


def _extract_confirmacao(value: str) -> Optional[bool]:
    text = _normalize_text(value)
    if text in {"sim", "confirmar", "ok", "confirmo", "s", "1"}:
        return True
    if text in {"nao", "não", "cancelar", "n", "cancela", "2"}:
        return False
    return None


def _navigation_hint() -> str:
    return "\n\nDigite voltar para retornar ou cancelar para encerrar."


def _parse_single_currency_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    number_map = {"1": "USD", "2": "EUR", "3": "SRD", "4": "BRL"}
    if text in number_map:
        return number_map[text]

    aliases = {
        "usd": "USD",
        "dolar": "USD",
        "dolares": "USD",
        "dolar americano": "USD",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
        "srd": "SRD",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
    }
    return aliases.get(text)


def _parse_origem_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    if text == "1":
        return "balcao"
    if text == "2":
        return "fora"
    if text in {"balcao", "balcão"}:
        return "balcao"
    if text == "fora":
        return "fora"
    return None


def _parse_forma_pagamento_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    number_map = {
        "1": "dinheiro",
        "2": "transferencia",
        "3": "cheque",
        "4": "misto",
    }
    if text in number_map:
        return number_map[text]
    if text in {"dinheiro", "transferencia", "cheque", "misto"}:
        return text
    return None


def _parse_fechamento_tipo_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    if text == "1":
        return "total"
    if text == "2":
        return "parcial"
    if text in {"total", "parcial"}:
        return text
    return None


def _extract_moedas(value: str) -> List[str]:
    text = _normalize_text(value)
    aliases = {
        "usd": "USD",
        "dolar": "USD",
        "dolares": "USD",
        "srd": "SRD",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
    }
    found: List[str] = []
    for token in re.split(r"[^a-zA-Z]+", text):
        if not token:
            continue
        moeda = aliases.get(token)
        if moeda and moeda not in found:
            found.append(moeda)
    return found


def _build_cambio_prompt(moeda: str) -> str:
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "EUR":
        return "1 EUR = quantos USD?"
    return f"1 USD = quantos {moeda_up}?"


# Strength ordering — stronger = lower number (numerator of the pair prompt).
_MOEDA_STRENGTH: Dict[str, int] = {"EUR": 0, "USD": 1, "BRL": 2, "SRD": 3}


def _build_pair_cambio_prompt(base: str, payment: str) -> str:
    """Return the natural pair prompt: '1 STRONGER = quantos WEAKER?'"""
    b, p = base.upper(), payment.upper()
    if _MOEDA_STRENGTH.get(b, 5) <= _MOEDA_STRENGTH.get(p, 5):
        return f"1 {b} = quantos {p}?"
    return f"1 {p} = quantos {b}?"


def _pair_rate_to_payment_per_usd(
    base: str,
    payment: str,
    user_rate: Decimal,
    db: "DatabaseClient",
) -> Tuple[Optional[Decimal], Decimal, Optional[Decimal]]:
    """Convert a direct B/P pair rate (direction per _build_pair_cambio_prompt) to
    (payment_per_usd, pair_P_per_B, c_base_per_usd)."""
    b, p = base.upper(), payment.upper()
    if _MOEDA_STRENGTH.get(b, 5) <= _MOEDA_STRENGTH.get(p, 5):
        pair_p_per_b = user_rate                          # prompt: "1 B = R P"
    else:
        pair_p_per_b = fx_rate(Decimal("1") / user_rate) if user_rate > 0 else Decimal("1")

    # Primary: B/USD from DB -> pay_per_usd = P_per_B x B_per_USD
    raw_base = db.get_last_cambio_para_usd(b)
    if raw_base and Decimal(str(raw_base)) > 0:
        c_base = Decimal(str(raw_base))
        return fx_rate(pair_p_per_b * c_base), pair_p_per_b, c_base

    # Fallback: P/USD directly from DB
    raw_pay = db.get_last_cambio_para_usd(p)
    if raw_pay and Decimal(str(raw_pay)) > 0:
        return Decimal(str(raw_pay)), pair_p_per_b, None

    return None, pair_p_per_b, None


def _normalize_cambio_para_usd(moeda: str, cambio_informado: Decimal) -> Decimal:
    """Normalize user input to the internal format: quote_currency per 1 USD."""
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "EUR":
        return fx_rate(Decimal("1") / cambio_informado)
    return fx_rate(cambio_informado)


def _try_set_total_usd_from_base_rate(contexto: Dict[str, Any], cambio_base_para_usd: Decimal) -> bool:
    """Set preco_usd/total_usd when the base-pricing currency exchange rate becomes available."""
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


def _guided_prompt_for_state(state: str, contexto: Dict[str, Any]) -> str:
    if state == "await_origem":
        return "Passo 0: local da operação (balcão ou fora)?"
    if state == "await_teor":
        return "Passo 1: qual o teor do ouro em %? Exemplo: 91,6"
    if state == "await_peso":
        return "Passo 2: quantas gramas? Exemplo: 2,5"
    if state == "await_preco_moeda":
        return "Passo 2.5: qual a moeda base da precificação? (USD, EUR, SRD ou BRL)"
    if state == "await_preco_usd":
        return "Passo 3: qual o preço por grama? Exemplo: 115 USD"
    if state == "await_preco_cambio":
        moeda_preco = str(contexto.get("preco_moeda") or "EUR").upper()
        return f"Passo 4: informe o câmbio. Exemplo: {_build_cambio_prompt(moeda_preco)}"
    if state == "await_cambio_base_para_total":
        moeda_preco = str(contexto.get("preco_moeda") or "EUR").upper()
        return f"Passo 4.5: para fechar o total em USD, informe o câmbio da moeda-base ({_build_cambio_prompt(moeda_preco)})"
    if state == "await_moedas":
        return "Passo 5: em quais moedas foi pago? Use: USD, EUR, SRD, BRL"
    if state == "await_valor_moeda":
        moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
        return f"Passo 6: quanto será pago em {moeda_atual}?"
    if state == "await_cambio_moeda_pre_valor":
        moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
        return f"Passo 6.5: informe o câmbio de {moeda_atual} antes do valor ({_build_cambio_prompt(moeda_atual)})"
    if state == "await_cambio_moeda":
        moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
        return f"Passo 7: informe o câmbio ({_build_cambio_prompt(moeda_atual)})"
    if state == "await_fechamento_gramas":
        return "Passo 8: quantas gramas foram fechadas? (use quando for venda/câmbio)"
    if state == "await_fechamento_tipo":
        return "Passo 9: fechamento total ou parcial?"
    if state == "await_pessoa":
        return "Passo 10: nome da pessoa?"
    if state == "await_forma_pagamento":
        return "Passo 11: forma de pagamento (dinheiro, transferência, cheque, misto)"
    if state == "await_observacoes":
        return "Passo 12: observações (ou digite 'nenhuma')"
    return "Continue informando os dados solicitados."


def _guided_clear_from_step(contexto: Dict[str, Any], target_state: str) -> Dict[str, Any]:
    cleared = dict(contexto)
    order = [
        "await_teor",
        "await_peso",
        "await_preco_usd",
        "await_preco_cambio",
        "await_cambio_base_para_total",
        "await_moedas",
        "await_valor_moeda",
        "await_cambio_moeda_pre_valor",
        "await_cambio_moeda",
        "await_fechamento_gramas",
        "await_fechamento_tipo",
        "await_pessoa",
        "await_forma_pagamento",
        "await_observacoes",
    ]
    fields_by_step: Dict[str, List[str]] = {
        "await_teor": ["teor"],
        "await_peso": ["peso"],
        "await_preco_usd": ["preco_moeda", "preco_moeda_valor", "total_moeda", "preco_usd", "cambio_preco_moeda", "total_usd"],
        "await_preco_cambio": ["cambio_preco_moeda", "preco_usd", "total_usd"],
        "await_cambio_base_para_total": ["cambio_preco_moeda", "preco_usd", "total_usd"],
        "await_moedas": ["moedas", "moeda_index", "moeda_atual", "pagamentos", "total_pago_usd"],
        "await_valor_moeda": ["pagamentos", "total_pago_usd"],
        "await_cambio_moeda_pre_valor": ["cambio_moeda_atual_pre", "pagamentos", "total_pago_usd"],
        "await_cambio_moeda": ["pagamentos", "total_pago_usd"],
        "await_fechamento_gramas": ["fechamento_gramas", "fechamento_tipo", "pessoa", "forma_pagamento", "observacoes"],
        "await_fechamento_tipo": ["fechamento_tipo", "pessoa", "forma_pagamento", "observacoes"],
        "await_pessoa": ["pessoa", "forma_pagamento", "observacoes"],
        "await_forma_pagamento": ["forma_pagamento", "observacoes"],
        "await_observacoes": ["observacoes"],
    }

    start_clearing = False
    for step in order:
        if step == target_state:
            start_clearing = True
        if start_clearing:
            for field in fields_by_step.get(step, []):
                cleared.pop(field, None)
    return cleared


def _guided_try_back_command(
    remetente: str,
    mensagem: str,
    estado: str,
    contexto: Dict[str, Any],
    db: DatabaseClient,
) -> Optional[Dict[str, Any]]:
    text = _normalize_text(mensagem)
    if not (text.startswith("voltar") or text.startswith("editar") or text.startswith("corrigir")):
        return None

    aliases: Dict[str, str] = {
        "teor": "await_teor",
        "peso": "await_peso",
        "gramas": "await_peso",
        "preco": "await_preco_usd",
        "preco usd": "await_preco_usd",
        "cotacao": "await_preco_usd",
        "cambio preco": "await_preco_cambio",
        "cambio base": "await_cambio_base_para_total",
        "moedas": "await_moedas",
        "moeda": "await_moedas",
        "pagamento": "await_valor_moeda",
        "valor": "await_valor_moeda",
        "cambio": "await_cambio_moeda",
        "cambio moeda": "await_cambio_moeda_pre_valor",
        "fechamento": "await_fechamento_gramas",
        "pessoa": "await_pessoa",
        "nome": "await_pessoa",
        "forma": "await_forma_pagamento",
        "observacoes": "await_observacoes",
        "observacao": "await_observacoes",
    }

    # "voltar" simples = etapa anterior mais segura
    if text in {"voltar", "corrigir", "editar"}:
        tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
        prev_pessoa = "await_moedas" if tipo_operacao == "compra" else "await_fechamento_tipo"
        previous_map: Dict[str, str] = {
            "await_origem": "await_menu_tipo_operacao",
            "await_teor": "await_origem",
            "await_peso": "await_teor",
            "await_preco_moeda": "await_peso",
            "await_preco_usd": "await_peso",
            "await_preco_cambio": "await_preco_usd",
            "await_cambio_base_para_total": "await_moedas",
            "await_moedas": "await_preco_usd",
            "await_valor_moeda": "await_moedas",
            "await_cambio_moeda_pre_valor": "await_moedas",
            "await_cambio_moeda": "await_valor_moeda",
            "await_fechamento_gramas": "await_moedas",
            "await_fechamento_tipo": "await_fechamento_gramas",
            "await_pessoa": prev_pessoa,
            "await_forma_pagamento": "await_pessoa",
            "await_observacoes": "await_forma_pagamento",
            "await_confirmacao": "await_observacoes",
        }
        target_state = previous_map.get(estado)
    else:
        target_state = None
        for key, mapped_state in aliases.items():
            if key in text:
                target_state = mapped_state
                break

    if not target_state:
        return {
            "mensagem": (
                "Para corrigir sem cancelar, envie: 'voltar', 'voltar peso', 'voltar preço' ou 'voltar teor'."
            ),
            "dados": {"etapa": estado},
        }

    novo_contexto = _guided_clear_from_step(contexto, target_state)
    _save_session(db, remetente, target_state, novo_contexto)
    prompt = _guided_prompt_for_state(target_state, novo_contexto)
    return {
        "mensagem": f"Corrigindo esta etapa.\n{prompt}",
        "dados": {"etapa": target_state, "acao": "voltar_editar"},
    }


def _extract_caixa_currency(message: str) -> Optional[str]:
    text = _normalize_text(message)
    aliases = {
        "1": "XAU",
        "2": "EUR",
        "3": "USD",
        "4": "SRD",
        "5": "BRL",
        "usd": "USD",
        "dolar": "USD",
        "dolar americano": "USD",
        "eur": "EUR",
        "euro": "EUR",
        "srd": "SRD",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
        "xau": "XAU",
        "ouro": "XAU",
    }
    if text in aliases:
        return aliases[text]
    for token in re.split(r"[^a-zA-Z0-9]+", text):
        if token in aliases:
            return aliases[token]
    return None


def _format_caixa_movement(currency: str, movement: Decimal) -> str:
    signal = "+" if movement >= 0 else "-"
    magnitude = abs(movement)
    if currency == "XAU":
        return f"{signal}{magnitude:,.3f} g"
    if currency == "USD":
        return f"{signal}$ {magnitude:,.2f}"
    if currency == "EUR":
        return f"{signal}EUR {magnitude:,.2f}"
    if currency == "SRD":
        return f"{signal}SRD {magnitude:,.2f}"
    if currency == "BRL":
        return f"{signal}R$ {magnitude:,.2f}"
    return f"{signal}{currency} {magnitude:,.2f}"


def _build_caixa_detail_response(
    db: DatabaseClient,
    currency: str,
    start_iso: str,
    end_iso: str,
    label_periodo: str,
) -> Dict[str, Any]:
    currency_up = currency.upper()
    saldo = db.get_saldo_caixa()
    transactions = db.get_extrato_transactions(start_iso, end_iso)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))

    caixa_titles = {
        "XAU": "CAIXA OURO (XAU)",
        "EUR": "CAIXA EURO (EUR)",
        "USD": "CAIXA DOLAR (USD)",
        "SRD": "CAIXA SURINAMES (SRD)",
        "BRL": "CAIXA REAL (BRL)",
    }

    movement_rows: List[Dict[str, Any]] = []
    total_entries = Decimal("0")
    total_exits = Decimal("0")
    total_sale_profit = Decimal("0")

    for tx in transactions:
        tipo = str(tx.get("tipo_operacao") or "").lower()
        if tipo not in {"compra", "venda", "cambio"}:
            continue

        movement = Decimal("0")
        if currency_up == "XAU":
            peso = Decimal(str(tx.get("peso") or "0"))
            if tipo == "compra":
                movement = peso
            elif tipo in {"venda", "cambio"}:
                movement = -peso
        else:
            pagamentos_raw = tx.get("pagamentos")
            pagamentos = cast(List[Dict[str, Any]], pagamentos_raw) if isinstance(pagamentos_raw, list) else []
            if pagamentos:
                for pagamento in pagamentos:
                    moeda = str(pagamento.get("moeda") or "USD").upper()
                    if moeda != currency_up:
                        continue
                    valor_moeda = Decimal(str(pagamento.get("valor_moeda") or "0"))
                    movement += -valor_moeda if tipo == "compra" else valor_moeda
            else:
                moeda = str(tx.get("moeda") or "USD").upper()
                if moeda == currency_up:
                    valor_moeda = Decimal(str(tx.get("valor_moeda") or tx.get("total_usd") or "0"))
                    movement = -valor_moeda if tipo == "compra" else valor_moeda

        if movement == 0:
            continue

        raw_dt = str(tx.get("criado_em") or "")
        try:
            dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            dt_local = dt + timedelta(hours=tz_offset_hours)
            data_fmt = dt_local.strftime("%d/%m/%Y %H:%M")
        except Exception:
            data_fmt = raw_dt[:16]

        if movement > 0:
            total_entries += movement
        else:
            total_exits += abs(movement)

        lucro_venda: Optional[Decimal] = None
        if tipo == "venda" and isinstance(tx.get("contexto"), dict):
            ctx_tx = cast(Dict[str, Any], tx.get("contexto") or {})
            lucro_raw = ctx_tx.get("lucro_real_usd")
            if lucro_raw is None:
                lucro_raw = ctx_tx.get("lucro_ref_usd")
            if lucro_raw is not None:
                try:
                    lucro_venda = Decimal(str(lucro_raw))
                    total_sale_profit += lucro_venda
                except (InvalidOperation, TypeError, ValueError):
                    lucro_venda = None

        movement_rows.append(
            {
                "tx_id": str(tx.get("id") or "-"),
                "data_fmt": data_fmt,
                "tipo": tipo.upper(),
                "movimento": movement,
                "cliente": str(tx.get("pessoa") or "").strip(),
                "operador": str(tx.get("operador_id") or "").strip(),
                "lucro_usd": lucro_venda,
            }
        )

    saldo_atual = Decimal(str(saldo.get(currency_up, "0")))
    lines = [
        f"EXTRATO {caixa_titles.get(currency_up, currency_up)}",
        f"Periodo: {label_periodo}",
        "================================",
    ]

    if movement_rows:
        for i, row in enumerate(movement_rows):
            if i > 0:
                lines.append("--------------------------------")
            lines.append(f"ID: #{row['tx_id']}  |  {row['data_fmt']}")
            lines.append(f"Tipo:     {row['tipo']}")
            lines.append(f"Cliente:  {row['cliente'][:40] if row['cliente'] else '—'}")
            lines.append(f"Operador: {row['operador'][:40] if row['operador'] else '—'}")
            lines.append(f"Valor:    {_format_caixa_movement(currency_up, cast(Decimal, row['movimento']))}")
            lucro_usd = row.get("lucro_usd")
            if isinstance(lucro_usd, Decimal):
                lines.append(f"Lucro:    USD {money(lucro_usd)}")
    else:
        lines.append("Nenhuma movimentacao neste periodo.")

    lines.extend(
        [
            "================================",
            f"Entradas: {_format_caixa_movement(currency_up, total_entries)}",
            f"Saidas:   {_format_caixa_movement(currency_up, -total_exits)}",
            f"Saldo:    {_format_caixa_movement(currency_up, saldo_atual)}",
        ]
    )
    if movement_rows:
        lines.append(f"Ops:      {len(movement_rows)}")
    if total_sale_profit != 0:
        lines.append(f"Lucro vendas: USD {money(total_sale_profit)}")

    return {
        "mensagem": "\n".join(lines),
        "dados": {
            "intencao": "consultar_relatorio",
            "requested_currency": currency_up,
            "periodo": label_periodo,
            "movimentos": len(movement_rows),
            "saldo_atual": str(saldo_atual),
        },
    }


def _persist_gold_operation_from_context(
    db: DatabaseClient,
    remetente: str,
    contexto: Dict[str, Any],
    post_save_session: bool = True,
) -> Dict[str, Any]:
    ativo = db.get_ativo_by_nome("Ouro")
    if not ativo:
        ativo = db.get_ativo_by_nome("Ouro 24k")
    if not ativo:
        raise HTTPException(status_code=404, detail="Ativo não encontrado")

    ativo_id = int(ativo["id"])
    peso = Decimal(str(contexto.get("peso")))
    preco = Decimal(str(contexto.get("preco_usd")))
    total = money(peso * preco)
    total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
    diferenca = money(total - total_pago)
    risco_diferenca = abs(diferenca) >= _RISK_DIFF_LIMIT_USD
    tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
    if tipo_operacao == "venda":
        _attach_sale_profit_reference(db, contexto)

    pagamentos = list(contexto.get("pagamentos", []))
    gold_type = _normalize_gold_type(contexto.get("gold_type"))
    quebra_raw = contexto.get("quebra")
    quebra = money(Decimal(str(quebra_raw))) if quebra_raw not in {None, "", "None"} else None
    header_payload: Dict[str, Any] = {
        "cliente_id": contexto.get("cliente_id"),
        "tipo_operacao": tipo_operacao,
        "origem": str(contexto.get("origem", "balcao")),
        "gold_type": gold_type,
        "quebra": str(quebra) if quebra is not None else None,
        "teor": contexto.get("teor"),
        "peso": str(peso),
        "preco_usd": str(money(preco)),
        "total_usd": str(total),
        "total_pago_usd": str(money(total_pago)),
        "diferenca_usd": str(diferenca),
        "fechamento_gramas": contexto.get("fechamento_gramas"),
        "fechamento_tipo": str(contexto.get("fechamento_tipo", "parcial")),
        "pessoa": str(contexto.get("pessoa", "")),
        "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
        "observacoes": contexto.get("observacoes", ""),
        "operador_id": remetente,
        "source_message_id": contexto.get("source_message_id"),
        "contexto": contexto,
        "criado_em": datetime.now(timezone.utc).isoformat(),
    }

    gold_transaction = db.insert_gold_transaction(
        payload=header_payload,
        pagamentos=pagamentos,
    )

    transacao = db.insert_transacao(
        tipo_operacao=tipo_operacao,
        ativo_id=ativo_id,
        quantidade=peso,
        cotacao_usada=preco,
        valor_total=total,
        operador_id=remetente,
        source_message_id=contexto.get("source_message_id"),
        status="registrada",
    )
    _invalidate_operation_related_view_caches()

    db.insert_log(
        nivel="info",
        remetente=remetente,
        mensagem_recebida="CONFIRMACAO_FLUXO_GUIADO",
        resposta_enviada="Fluxo guiado confirmado",
        contexto=contexto,
    )
    if risco_diferenca:
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida="ALERTA_RISCO_DIFERENCA",
            contexto={
                "intencao": "alerta_risco",
                "tipo": "diferenca_alta",
                "limite_usd": str(_RISK_DIFF_LIMIT_USD),
                "diferenca_usd": str(diferenca),
                "tipo_operacao": contexto.get("tipo_operacao"),
            },
            erro="Diferença de caixa acima do limite",
        )

    review_payload: Optional[Dict[str, Any]] = None
    review_transaction: Dict[str, Any] = {
        "tipo_operacao": tipo_operacao,
        "origem": str(contexto.get("origem", "balcao")),
        "teor": contexto.get("teor"),
        "peso": str(peso),
        "preco_usd": str(money(preco)),
        "total_usd": str(total),
        "total_pago_usd": str(money(total_pago)),
        "diferenca_usd": str(diferenca),
        "fechamento_gramas": contexto.get("fechamento_gramas"),
        "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
        "pagamentos": pagamentos,
        "transacao_id": transacao.get("id"),
    }
    if _should_trigger_multi_agent_review(review_transaction, force=risco_diferenca):
        review_payload = _run_automatic_multi_agent_review(
            db,
            objective="avaliacao automatica de operacao enterprise",
            transaction=review_transaction,
            operation_id=gold_transaction.get("id") if isinstance(gold_transaction, dict) else None,
            operation_kind="gold_transaction",
            source_message_id=contexto.get("source_message_id"),
        )

    if post_save_session:
        _save_session(db, remetente, "await_caixa_detalhe", {"source": "post_operacao"})

    alerta = "" if not risco_diferenca else " ⚠️ Atenção: verificar diferença."
    gt_id = gold_transaction.get("id") if isinstance(gold_transaction, dict) else None
    tx_id = transacao.get("id")
    if gt_id:
        id_linha = f"ID: GT-{gt_id}\n"
    elif tx_id:
        id_linha = f"ID: T-{tx_id}\n"
    else:
        id_linha = ""

    caixa_resp = _build_caixa_response(db)
    caixa_msg = str(caixa_resp.get("mensagem", ""))
    direcao_txt = "Saiu" if tipo_operacao == "compra" else "Entrou"
    direcao_ouro_txt = "Entrou" if tipo_operacao == "compra" else "Saiu"
    mov_linhas: List[str] = [f"- {direcao_ouro_txt} ouro: {peso:,.3f}g"]
    for pagamento in pagamentos:
        moeda_pg = str(pagamento.get("moeda", "USD")).upper()
        valor_moeda_pg = Decimal(str(pagamento.get("valor_moeda", "0")))
        mov_linhas.append(f"- {direcao_txt} {moeda_pg}: {money(valor_moeda_pg)}")
    mov_txt = "\n".join(mov_linhas) if mov_linhas else "- Nenhuma movimentacao registrada"

    response_payload: Dict[str, Any] = {
        "mensagem": (
            f"✅ Operacao registrada com sucesso.\n"
            f"{id_linha}"
            f"Tipo: {tipo_operacao}\n"
            f"Peso: {peso:,.3f}g\n"
            "Movimentacao consolidada dos 5 caixas:\n"
            f"{mov_txt}{alerta}\n"
            "════════════════════════════════\n"
            f"{caixa_msg}"
        ),
        "dados": {
            "intencao": "fluxo_guiado_confirmado",
            "tipo_operacao": contexto.get("tipo_operacao"),
            "peso": str(peso),
            "pagamentos": pagamentos,
            "gold_transaction_id": gt_id,
            "transacao_id": tx_id,
        },
    }
    if review_payload:
        response_payload["dados"]["analise_multiagente"] = review_payload
    return response_payload


def _normalize_user_phone(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    return f"+{digits}"


def _validate_web_pin_format(pin: str) -> str:
    normalized = str(pin or "").strip()
    if not re.fullmatch(r"\d{4,12}", normalized):
        raise HTTPException(status_code=400, detail="PIN web deve ter entre 4 e 12 dígitos numéricos")
    return normalized


def _get_saas_session_secret() -> str:
    return (
        os.getenv("SAAS_SESSION_SECRET")
        or os.getenv("WEBHOOK_TOKEN")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
        or "caixa-saas-dev-secret"
    )


def _encode_saas_session(telefone: str) -> str:
    expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=_SAAS_SESSION_TTL_SECONDS)).timestamp())
    payload = f"{telefone}|{expires_at}"
    payload_token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    signature = hmac.new(
        _get_saas_session_secret().encode("utf-8"),
        payload_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_token}.{signature}"


def _decode_saas_session(raw_cookie: Optional[str]) -> Optional[str]:
    if not raw_cookie or "." not in raw_cookie:
        return None
    payload_token, signature = raw_cookie.rsplit(".", 1)
    expected_signature = hmac.new(
        _get_saas_session_secret().encode("utf-8"),
        payload_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = base64.urlsafe_b64decode(payload_token.encode("ascii")).decode("utf-8")
        telefone, expires_at_raw = payload.split("|", 1)
        if int(expires_at_raw) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return telefone
    except Exception:
        return None


def _set_saas_session(response: Response, telefone: str) -> None:
    response.set_cookie(
        key=_SAAS_SESSION_COOKIE,
        value=_encode_saas_session(telefone),
        httponly=True,
        secure=_SAAS_COOKIE_SECURE,
        samesite="lax",
        max_age=_SAAS_SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_saas_session(response: Response) -> None:
    response.delete_cookie(key=_SAAS_SESSION_COOKIE, path="/")


def _get_saas_authenticated_user_cached(telefone: str) -> Optional[Dict[str, Any]]:
    cached = _SAAS_AUTH_USER_CACHE.get(str(telefone or ""))
    if not cached:
        return None
    expires_at = cached.get("expires_at")
    user = cached.get("user")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.now(timezone.utc) or not isinstance(user, dict):
        _SAAS_AUTH_USER_CACHE.pop(str(telefone or ""), None)
        return None
    return dict(user)


def _set_saas_authenticated_user_cached(telefone: str, user: Dict[str, Any]) -> Dict[str, Any]:
    normalized_phone = str(telefone or "")
    cached_user = dict(user)
    _SAAS_AUTH_USER_CACHE[normalized_phone] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=_SAAS_AUTH_USER_CACHE_TTL_SECONDS),
        "user": cached_user,
    }
    return dict(cached_user)


def _invalidate_saas_authenticated_user_cache(telefone: Optional[str] = None) -> None:
    if telefone is None:
        _SAAS_AUTH_USER_CACHE.clear()
        return
    _SAAS_AUTH_USER_CACHE.pop(str(telefone or ""), None)


def _get_saas_authenticated_user(request: Request, db: DatabaseClient) -> Optional[Dict[str, Any]]:
    telefone = _decode_saas_session(request.cookies.get(_SAAS_SESSION_COOKIE))
    if not telefone:
        return None
    cached = _get_saas_authenticated_user_cached(telefone)
    if cached is not None:
        return cached
    usuario = db.get_usuario_web_auth(telefone)
    if not usuario:
        return None
    enriched = dict(usuario)
    enriched["web_pin_bootstrap_required"] = not bool(enriched.get("web_pin_hash"))
    return _set_saas_authenticated_user_cached(telefone, enriched)


def _derive_forma_pagamento_summary(pagamentos: List[Dict[str, Any]]) -> str:
    if not pagamentos:
        return "dinheiro"
    methods = {str(item.get("forma_pagamento") or "dinheiro") for item in pagamentos}
    if len(methods) == 1:
        method = next(iter(methods))
        if method in {"dinheiro", "transferencia", "cheque"}:
            return method
    return "misto"


def _build_web_payment_rows_html(values: Dict[str, str]) -> str:
    rows: List[str] = []
    for index in range(1, 5):
        currency_key = f"payment_{index}_moeda"
        amount_key = f"payment_{index}_valor"
        fx_key = f"payment_{index}_cambio"
        percent_key = f"payment_{index}_percent"
        method_key = f"payment_{index}_forma"
        moeda = values.get(currency_key, "USD" if index == 1 else "")
        valor = values.get(amount_key, "")
        cambio = values.get(fx_key, "1" if moeda == "USD" and index == 1 else "")
        percent = values.get(percent_key, "")
        forma = values.get(method_key, "dinheiro")
        rows.append(
            f"""
            <div class='payment-row js-payment-row'>
                <label>Moeda #{index}
                    <select name='{currency_key}' class='js-payment-moeda'>
                        <option value='' {'selected' if not moeda else ''}>-</option>
                        <option value='USD' {'selected' if moeda=='USD' else ''}>USD</option>
                        <option value='EUR' {'selected' if moeda=='EUR' else ''}>EUR</option>
                        <option value='SRD' {'selected' if moeda=='SRD' else ''}>SRD</option>
                        <option value='BRL' {'selected' if moeda=='BRL' else ''}>BRL</option>
                    </select>
                </label>
                <label>Valor na moeda
                    <input name='{amount_key}' value='{escape(valor)}' placeholder='ex.: 380' class='js-payment-valor' inputmode='decimal' />
                </label>
                <label>% do pagamento
                    <input name='{percent_key}' value='{escape(percent)}' placeholder='ex.: 40' class='js-payment-percent' inputmode='decimal' />
                </label>
                <label><span class='js-payment-cambio-label'>{escape(_payment_fx_prompt_label(moeda))}</span>
                    <input name='{fx_key}' value='{escape(cambio)}' placeholder='vazio = último câmbio' class='js-payment-cambio' inputmode='decimal' />
                </label>
                <label>Forma
                    <select name='{method_key}'>
                        <option value='dinheiro' {'selected' if forma=='dinheiro' else ''}>Dinheiro</option>
                        <option value='transferencia' {'selected' if forma=='transferencia' else ''}>Transferência</option>
                        <option value='cheque' {'selected' if forma=='cheque' else ''}>Cheque</option>
                    </select>
                </label>
                <div class='payment-preview js-payment-preview'>USD 0.00</div>
            </div>
            """
        )
    return "".join(rows)


def _parse_decimal_web_field(raw: str, field_name: str) -> Decimal:
    return parse_decimal(str(raw or "0").strip().replace(",", "."), field_name)


def _parse_web_payments_from_form(db: DatabaseClient, form: Dict[str, str]) -> List[Dict[str, Any]]:
    pagamentos: List[Dict[str, Any]] = []
    for index in range(1, 5):
        currency_key = f"payment_{index}_moeda"
        amount_key = f"payment_{index}_valor"
        fx_key = f"payment_{index}_cambio"
        method_key = f"payment_{index}_forma"
        moeda_raw = str(form.get(currency_key) or "").strip().upper()
        valor_raw = str(form.get(amount_key) or "").strip()
        cambio_raw = str(form.get(fx_key) or "").strip()
        forma = _normalize_text(str(form.get(method_key) or "dinheiro"))

        if not any([moeda_raw, valor_raw, cambio_raw]):
            continue
        if not moeda_raw or not valor_raw:
            raise HTTPException(status_code=400, detail=f"Pagamento #{index} incompleto")
        if moeda_raw not in {"USD", "EUR", "SRD", "BRL"}:
            raise HTTPException(status_code=400, detail=f"Moeda inválida no pagamento #{index}")
        if forma not in {"dinheiro", "transferencia", "cheque"}:
            raise HTTPException(status_code=400, detail=f"Forma inválida no pagamento #{index}")

        valor_moeda = _parse_decimal_web_field(valor_raw, amount_key)
        if valor_moeda <= 0:
            raise HTTPException(status_code=400, detail=f"Valor do pagamento #{index} deve ser maior que zero")

        if moeda_raw == "USD":
            cambio_para_usd = Decimal("1")
        elif cambio_raw:
            cambio_para_usd = _normalize_cambio_para_usd(moeda_raw, _parse_decimal_web_field(cambio_raw, fx_key))
        else:
            last_cambio = db.get_last_cambio_para_usd(moeda_raw)
            if not last_cambio or Decimal(str(last_cambio)) <= 0:
                raise HTTPException(status_code=400, detail=f"Sem câmbio disponível para {moeda_raw} no pagamento #{index}")
            cambio_para_usd = fx_rate(Decimal(str(last_cambio)))

        if cambio_para_usd <= 0:
            raise HTTPException(status_code=400, detail=f"Câmbio inválido no pagamento #{index}")

        pagamentos.append(
            {
                "moeda": moeda_raw,
                "valor_moeda": str(money(valor_moeda)),
                "cambio_para_usd": str(cambio_para_usd),
                "valor_usd": str(money(valor_moeda / cambio_para_usd)),
                "forma_pagamento": forma,
            }
        )

    if pagamentos:
        return pagamentos

    total_pago_raw = str(form.get("total_pago_usd") or "").strip()
    forma_pagamento = _normalize_text(str(form.get("forma_pagamento") or "dinheiro"))
    if total_pago_raw:
        total_pago = _parse_decimal_web_field(total_pago_raw, "total_pago_usd")
        if total_pago <= 0:
            raise HTTPException(status_code=400, detail="Total pago deve ser maior que zero")
        return [
            {
                "moeda": "USD",
                "valor_moeda": str(money(total_pago)),
                "cambio_para_usd": "1",
                "valor_usd": str(money(total_pago)),
                "forma_pagamento": forma_pagamento if forma_pagamento in {"dinheiro", "transferencia", "cheque"} else "dinheiro",
            }
        ]

    raise HTTPException(status_code=400, detail="Informe ao menos um pagamento")


async def _request_form_dict(request: Request) -> Dict[str, str]:
    raw_text = ""
    try:
        raw_text = (await request.body()).decode("utf-8", errors="ignore")
    except Exception:
        raw_text = ""

    try:
        form = await request.form()
        return {str(k): str(v) for k, v in dict(form).items()}
    except Exception:
        pass

    try:
        parsed = parse_qs(raw_text)
        return {k: v[0] for k, v in parsed.items() if v}
    except Exception:
        return {}


def _dashboard_default_form_values(session_user: Dict[str, Any]) -> Dict[str, str]:
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
        "payment_2_moeda": "",
        "payment_2_valor": "",
        "payment_2_percent": "",
        "payment_2_cambio": "",
        "payment_2_forma": "dinheiro",
        "payment_3_moeda": "",
        "payment_3_valor": "",
        "payment_3_percent": "",
        "payment_3_cambio": "",
        "payment_3_forma": "dinheiro",
        "payment_4_moeda": "",
        "payment_4_valor": "",
        "payment_4_cambio": "",
        "payment_4_forma": "dinheiro",
    }


def _format_decimal_for_form(value: Decimal, places: int = 2) -> str:
    quant = Decimal("1").scaleb(-places)
    normalized = value.quantize(quant, rounding=ROUND_HALF_UP)
    text = format(normalized, "f").rstrip("0").rstrip(".")
    return text or "0"


def _build_saas_recent_fx_map(db: DatabaseClient) -> Dict[str, str]:
    cached = _get_saas_recent_fx_cached()
    if cached is not None:
        return cached

    snapshot: Dict[str, str] = {"USD": "1"}
    recent_rates = db.get_last_cambio_para_usd_map(["EUR", "SRD", "BRL"])
    for moeda in ["EUR", "SRD", "BRL"]:
        raw = recent_rates.get(moeda)
        if raw and Decimal(str(raw)) > 0:
            snapshot[moeda] = _display_cambio_for_web_input(moeda, Decimal(str(raw)))
        else:
            snapshot[moeda] = ""
    return _set_saas_recent_fx_cached(snapshot)


def _build_statement_summary(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return statements_service._build_statement_summary(transactions)


def _build_statement_summary_for_window(
    transactions: List[Dict[str, Any]],
    start_iso: str,
    end_iso: str,
) -> Dict[str, Any]:
    return statements_service._build_statement_summary_for_window(transactions, start_iso, end_iso)


def _build_operation_draft_from_message(
    db: DatabaseClient,
    session_user: Dict[str, Any],
    message: str,
) -> Dict[str, Any]:
    return operation_drafts_service._build_operation_draft_from_message(
        db,
        session_user,
        message,
        normalize_text=_normalize_text,
        build_recent_fx_map=_build_saas_recent_fx_map,
        ai_extracted_data_cls=AIExtractedData,
        dashboard_default_form_values=_dashboard_default_form_values,
        infer_tipo_operacao=infer_tipo_operacao,
        parse_decimal_from_text=_parse_decimal_from_text,
        format_decimal_for_form=_format_decimal_for_form,
        payment_input_to_usd=_payment_input_to_usd,
        build_cliente_lookup_meta=_build_cliente_lookup_meta,
    )


def _build_saas_chat_welcome(user_name: str) -> Dict[str, str]:
    return {
        "role": "assistant",
        "content": f"Ola, {user_name}. Estou disponivel para apoiar o registro de operacoes, a consulta de saldos, extratos, estoque e demais rotinas operacionais do painel.",
    }


def _normalize_saas_page(raw: Optional[str]) -> str:
    text = _normalize_text(str(raw or "dashboard"))
    aliases = {
        "dashboard": "dashboard",
        "inicio": "dashboard",
        "home": "dashboard",
        "operacao": "operation",
        "operacoes": "operation",
        "operation": "operation",
        "lancar": "operation",
        "perfil": "profile",
        "profile": "profile",
        "usuario": "profile",
        "conta": "profile",
        "clientes": "clients",
        "cliente": "clients",
        "cadastro": "clients",
        "clients": "clients",
        "monitor": "monitors",
        "monitores": "monitors",
        "monitoria": "monitors",
        "monitors": "monitors",
        "noticia": "news_hub",
        "noticias": "news_hub",
        "news": "news_hub",
        "mercado": "news_hub",
        "extrato": "statement",
        "statement": "statement",
        "movimentos": "statement",
    }
    return aliases.get(text, "dashboard")


def _format_cliente_code(cliente_id: Any) -> str:
    try:
        return f"CL-{int(cliente_id):06d}"
    except Exception:
        return "CL-000000"


def _build_cliente_lookup_meta(cliente: Dict[str, Any]) -> str:
    bits = [_format_cliente_code(cliente.get("id"))]
    telefone = str(cliente.get("telefone") or "").strip()
    documento = str(cliente.get("documento") or "").strip()
    apelido = str(cliente.get("apelido") or "").strip()
    if telefone:
        bits.append(telefone)
    if documento:
        bits.append(documento)
    if apelido:
        bits.append(f"apelido: {apelido}")
    return " | ".join(bits)


def _parse_cliente_opening_balances(form: Dict[str, str], prefix: str) -> Dict[str, Decimal]:
    balances: Dict[str, Decimal] = {}
    for currency in ["XAU", "USD", "EUR", "SRD", "BRL"]:
        field_name = f"{prefix}_{currency.lower()}"
        raw_value = str(form.get(field_name) or "").strip()
        if not raw_value:
            continue
        balances[currency] = _parse_decimal_web_field(raw_value, field_name)
    return balances


def _build_saas_clients_context(
    db: DatabaseClient,
    selected_client_id: Optional[int] = None,
    search_term: Optional[str] = None,
) -> Dict[str, Any]:
    return clients_service._build_saas_clients_context(
        db,
        selected_client_id=selected_client_id,
        search_term=search_term,
    )


def _format_datetime_pt_br(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
        dt_local = dt + timedelta(hours=tz_offset_hours)
        return dt_local.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return raw[:16].replace("T", " ")


def _build_gold_receipt_context(db: DatabaseClient, operation_id: int) -> Dict[str, Any]:
    return receipts_service._build_gold_receipt_context(
        db,
        operation_id,
        build_cache_key=_build_saas_receipt_context_cache_key,
        get_cached_context=_get_saas_receipt_context_cached,
        set_cached_context=_set_saas_receipt_context_cached,
        format_datetime_pt_br=_format_datetime_pt_br,
    )


def _render_saas_receipt_html(receipt: Dict[str, Any], pdf_url: str, back_url: str) -> str:
    return receipts_service._render_saas_receipt_html(
        receipt,
        pdf_url,
        back_url,
        build_cliente_lookup_meta=_build_cliente_lookup_meta,
    )


def _build_gold_receipt_pdf(receipt: Dict[str, Any], pdf_url: str) -> bytes:
    return receipts_service._build_gold_receipt_pdf(
        receipt,
        pdf_url,
        build_cliente_lookup_meta=_build_cliente_lookup_meta,
    )


def _render_saas_clients_page(clients_context: Dict[str, Any], values: Dict[str, str]) -> str:
    return clients_service._render_saas_clients_page(
        clients_context,
        values,
        build_cliente_lookup_meta=_build_cliente_lookup_meta,
        format_caixa_movement=_format_caixa_movement,
    )


def _build_saas_statement_context(
    db: DatabaseClient,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Dict[str, Any]:
    return statements_service._build_saas_statement_context(
        db,
        start_date,
        end_date,
        build_day_range=_build_day_range,
        build_cache_key=_build_saas_statement_context_cache_key,
        get_cached_context=_get_saas_statement_context_cached,
        set_cached_context=_set_saas_statement_context_cached,
        build_extrato_response=_build_extrato_response,
    )


def _build_saas_dashboard_trend(transactions: List[Dict[str, Any]], days: int = 7) -> List[Dict[str, Any]]:
    return dashboard_trends_service._build_saas_dashboard_trend(transactions, days=days)


def _render_saas_trend_chart(points: List[Dict[str, Any]]) -> str:
    return dashboard_trends_service._render_saas_trend_chart(points)


def _render_saas_login_html(message: Optional[str] = None, telefone: str = "") -> str:
    alert = ""
    if message:
        alert = f"<div class='alert error'>{escape(message)}</div>"
    login_css_url = _asset_url("saas-login.css")
    return f"""
    <html>
        <head>
            <title>Caixa SaaS</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <link rel='preload' href='{login_css_url}' as='style'>
            <link href='{login_css_url}' rel='stylesheet'>
        </head>
        <body>
            <div class='shell'>
                <h1>Caixa SaaS</h1>
                <p>Painel web para operar o mesmo motor do WhatsApp com leitura mais clara, relatórios e entrada rápida de dados.</p>
                {alert}
                <form method='post' action='/saas/login'>
                    <label>Telefone do operador</label>
                    <input name='telefone' value='{escape(telefone)}' placeholder='+59711111111' required />
                    <label>PIN web</label>
                    <input type='password' name='pin' inputmode='numeric' placeholder='Seu PIN numérico' required />
                    <p class='hint'>Primeiro acesso após a migração: use os últimos 6 dígitos do telefone e troque o PIN logo após entrar.</p>
                    <button type='submit'>Entrar no painel</button>
                </form>
            </div>
        </body>
    </html>
    """


def _render_saas_dashboard_html(
    db: DatabaseClient,
    session_user: Dict[str, Any],
    notice: Optional[str] = None,
    notice_kind: str = "info",
    assistant_result: Optional[Dict[str, Any]] = None,
    form_values: Optional[Dict[str, str]] = None,
    current_page: str = "dashboard",
    statement_context: Optional[Dict[str, Any]] = None,
    clients_context: Optional[Dict[str, Any]] = None,
) -> str:
    current_page = _normalize_saas_page(current_page)
    values = dict(_dashboard_default_form_values(session_user))
    if form_values:
        values.update({k: str(v) for k, v in form_values.items()})

    day = _build_day_range(None)
    week = _build_week_range()
    needs_statement = current_page == "statement" or statement_context is not None
    needs_clients = current_page == "clients" or clients_context is not None
    needs_week_activity = current_page == "news_hub"
    needs_inventory_details = current_page in {"operation", "monitors"}
    needs_gold_caixa_metrics = current_page == "operation"
    needs_market_news = current_page == "news_hub"
    needs_lot_monitors = current_page == "monitors"
    needs_recent_fx = current_page == "operation"
    needs_market_rail = current_page in {"dashboard", "operation", "monitors", "news_hub"}
    needs_market_snapshot = needs_inventory_details or needs_market_rail
    needs_sidebar_inventory = current_page in {"dashboard", "operation", "monitors", "news_hub"}
    needs_balance_cards = current_page in {"dashboard", "profile"}
    needs_money_balances = current_page == "operation"
    needs_operation_inventory_tables = current_page == "operation"
    needs_news_recent_ops = current_page == "news_hub"
    needs_full_lot_market_context = needs_lot_monitors

    saldo: Dict[str, Any] = {"XAU": "0", "USD": "0", "EUR": "0", "SRD": "0", "BRL": "0"}
    if needs_balance_cards or needs_money_balances:
        saldo = db.get_saldo_caixa()

    if needs_inventory_details:
        inventory = db.get_gold_inventory_status(open_only=True)
        if not inventory.get("has_any_lots"):
            db.sync_gold_inventory_ledger()
            inventory = db.get_gold_inventory_status(open_only=True)
    elif needs_sidebar_inventory:
        inventory = db.get_gold_inventory_overview()
    else:
        inventory = {"available_grams": "0"}

    week_transactions: List[Dict[str, Any]] = []
    if needs_week_activity:
        week_transactions = db.get_extrato_transactions(week["start"], week["end"])
    recent_ops = week_transactions[-12:] if needs_week_activity else []
    statement = statement_context or {}
    if needs_statement and not statement:
        statement = _build_saas_statement_context(db, None, None)
    client_view = clients_context or (_build_saas_clients_context(db) if needs_clients else None)
    statement_transactions = cast(List[Dict[str, Any]], statement.get("transactions") or [])
    open_fechamentos_statement = _collect_open_fechamentos(statement_transactions) if needs_statement else []
    gold_caixa_metrics = _build_gold_caixa_metrics_from_pending_grams(Decimal(str(saldo.get("XAU", "0"))), db.get_gold_pending_closure_grams()) if needs_gold_caixa_metrics else {
        "ouro_pendente": Decimal("0"),
        "ouro_em_caixa": Decimal(str(saldo.get("XAU", "0"))),
        "ouro_proprio": Decimal(str(saldo.get("XAU", "0"))),
    }
    market_snapshot = _get_market_snapshot() if needs_market_snapshot else {
        "xau_usd_raw": "",
        "grama_ref_raw": "",
        "usd_brl_raw": "",
        "eur_usd_raw": "",
        "eur_brl_raw": "",
        "xau_source": "",
        "xau_source_label": "",
        "status": "Indisponivel",
        "updated_at_label": "",
    }
    lot_market_context = _build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot) if needs_full_lot_market_context else {
        "lots": [],
        "by_teor": [],
        "available_fine_grams": "0",
        "market_value_usd": "0",
        "unrealized_pnl_usd": "0",
    }
    operation_lot_market_context = _build_operation_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot) if needs_operation_inventory_tables else {
        "by_teor": [],
        "risk_lots": [],
        "available_fine_grams": "0",
        "market_value_usd": "0",
        "unrealized_pnl_usd": "0",
    }
    market_trend = _build_market_trend_context() if needs_lot_monitors else {"trend_label": "Lateral"}
    web_lot_ai_alerts: List[Dict[str, Any]] = []
    web_lot_ai_summary = _build_web_lot_ai_alert_summary(web_lot_ai_alerts)

    balances_html = ""
    if needs_balance_cards:
        balances_html = "".join(
            f"<div class='balance'><span>{escape(moeda)}</span><strong>{escape(_format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</strong></div>"
            for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]
        )

    money_balances_html = ""
    if needs_money_balances:
        money_balances_html = "".join(
            f"<div class='balance'><span>{escape(moeda)}</span><strong>{escape(_format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</strong></div>"
            for moeda in ["USD", "EUR", "SRD", "BRL"]
        )

    operation_lot_teor_html = "<tr><td colspan='3'>Sem teor aberto.</td></tr>"
    risk_lots_html = "<tr><td colspan='4'>Sem lotes em aberto.</td></tr>"
    if needs_operation_inventory_tables:
        operation_lot_teor_rows: List[str] = []
        for item in cast(List[Dict[str, Any]], operation_lot_market_context.get("by_teor") or [])[:4]:
            pnl_value = Decimal(str(item.get("unrealized_pnl_usd") or "0"))
            pnl_class = "positive" if pnl_value >= 0 else "negative"
            operation_lot_teor_rows.append(
                f"<tr><td>{escape(str(item.get('teor') or '-'))}%</td><td>{escape(str(item.get('grams') or '0'))} g</td><td class='{pnl_class}'>USD {escape(str(item.get('unrealized_pnl_usd') or '0'))}</td></tr>"
            )
        operation_lot_teor_html = "".join(operation_lot_teor_rows) or operation_lot_teor_html

        risk_lot_rows: List[str] = []
        ranked_risk_lots = cast(List[Dict[str, Any]], operation_lot_market_context.get("risk_lots") or [])
        for item in ranked_risk_lots:
            pnl_value = Decimal(str(item.get("unrealized_pnl_usd") or "0"))
            pnl_class = "positive" if pnl_value >= 0 else "negative"
            risk_lot_rows.append(
                f"<tr><td>GT-{escape(str(item.get('source_transaction_id', item.get('source_id', ''))))}</td><td>{escape(str(item.get('teor', '-')))}%</td><td>{escape(str(item.get('remaining_grams', '0')))} g</td><td class='{pnl_class}'>USD {escape(str(item.get('unrealized_pnl_usd', '0')))}</td></tr>"
            )
        risk_lots_html = "".join(risk_lot_rows) or risk_lots_html

    recent_html = _render_recent_operations_rows(recent_ops) if needs_news_recent_ops else ""

    notice_html = ""
    if notice:
        notice_html = f"<div class='notice {escape(notice_kind)}'>{escape(notice)}</div>"

    user_name = escape(str(session_user.get("nome") or session_user.get("telefone") or "Operador"))
    user_phone = escape(str(session_user.get("telefone") or "-"))
    user_role = escape(str(session_user.get("tipo_usuario") or "operador"))
    chat_bootstrap = [_build_saas_chat_welcome(str(session_user.get("nome") or "operador"))]
    if assistant_result and str(assistant_result.get("mensagem") or "").strip():
        chat_bootstrap.append({"role": "assistant", "content": str(assistant_result.get("mensagem") or "")})
    recent_fx = _build_saas_recent_fx_map(db) if needs_recent_fx else {"USD": "1"}
    chat_remetente = escape(values["console_remetente"])
    is_admin = str(session_user.get("tipo_usuario") or "").lower() == "admin"
    chat_operator_field = ""
    if is_admin:
        chat_operator_field = f"""
        <label class='chat-meta-field'>Operador / remetente
            <input id='aiChatRemetente' name='console_remetente' value='{chat_remetente}' required />
        </label>
        """
    else:
        chat_operator_field = f"""
        <div class='chat-identity'>Conversando como <strong>{user_phone}</strong></div>
        <input id='aiChatRemetente' name='console_remetente' value='{chat_remetente}' type='hidden' />
        """
    bootstrap_notice = ""
    if session_user.get("web_pin_bootstrap_required"):
        bootstrap_notice = "<div class='notice error'>PIN temporário em uso. Troque o PIN agora para remover o bootstrap de login.</div>"
    payment_rows_html = _build_web_payment_rows_html(values)
    web_ai_banner_html = ""
    if current_page == "dashboard":
        banner_status_class = "neutral"
        banner_hidden_class = " is-hidden"
        web_ai_banner_html = f"""
        <section class='notice info web-ai-alert-banner {banner_status_class}{banner_hidden_class}' id='webAiAlertBanner' data-lot-alert-endpoint='/saas/lot-monitor-snapshot' data-lot-alert-stream-endpoint='/saas/lot-monitor-stream'>
            <div class='notice-action web-ai-alert-shell'>
                <div>
                    <strong>IA da web</strong><br>
                    <span id='webAiAlertText'>Monitorando lotes em segundo plano...</span>
                </div>
                <button type='button' class='ghost-btn mini-action web-ai-notification-btn' id='webAiNotificationButton'>Ativar avisos no navegador</button>
            </div>
        </section>
        """

    nav_items = [
        ("dashboard", "/saas/dashboard", "Dashboard", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M4 13h7V4H4zm9 7h7V4h-7zm-9 0h7v-5H4z'/></svg>"),
        ("monitors", "/saas/monitores", "Monitores IA", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M3 17h3V7H3zm5 4h3V3H8zm5-6h3V9h-3zm5 4h3V5h-3z'/></svg>"),
        ("news_hub", "/saas/noticias", "Noticias", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M4 5h14v14H4zm3 3v2h8V8zm0 4v2h8v-2zm0 4v2h5v-2zm13-8h-1v9a2 2 0 0 1-2 2H7v1h10a3 3 0 0 0 3-3z'/></svg>"),
        ("operation", "/saas/operation", "Operacao", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M19 11h-6V5h-2v6H5v2h6v6h2v-6h6z'/></svg>"),
        ("clients", "/saas/clientes", "Clientes", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M16 11c1.66 0 2.99-1.57 2.99-3.5S17.66 4 16 4s-3 1.57-3 3.5S14.34 11 16 11m-8 0c1.66 0 2.99-1.57 2.99-3.5S9.66 4 8 4 5 5.57 5 7.5 6.34 11 8 11m0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13m8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.94 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5'/></svg>"),
        ("statement", "/saas/extrato", "Extrato", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M6 2h9l5 5v15a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m8 1.5V8h4.5zM8 12v2h8v-2zm0 4v2h6v-2z'/></svg>"),
        ("profile", "/saas/profile", "Perfil", "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4m0 2c-3.33 0-6 1.79-6 4v2h12v-2c0-2.21-2.67-4-6-4'/></svg>"),
    ]
    nav_html = "".join(
        f"<a href='{href}' class='nav-link {'active' if current_page == key else ''}'><span class='nav-icon'>{icon}</span><span class='nav-label'>{label}</span></a>"
        for key, href, label, icon in nav_items
    )

    statement_rows_html = "<tr><td colspan='7'>Nenhuma operacao encontrada para o periodo.</td></tr>"
    if needs_statement:
        statement_rows: List[str] = []
        for item in reversed(statement_transactions):
            source = str(item.get("source") or "transacoes")
            item_id = str(item.get("id") or "-")
            id_label = f"GT-{item_id}" if source == "gold_transactions" else f"T-{item_id}"
            fechamento_status = _build_fechamento_status(item)
            fechamento_txt = "Total"
            if bool(fechamento_status["is_partial"]):
                fechamento_txt = (
                    f"Parcial: {Decimal(str(fechamento_status['fechado'])):,.3f} g fechados"
                    f" | {Decimal(str(fechamento_status['aberto'])):,.3f} g em aberto"
                )
            pagamentos = cast(List[Dict[str, Any]], item.get("pagamentos") or [])
            pagamentos_txt = ", ".join(
                f"{str(p.get('moeda') or 'USD').upper()} {money(Decimal(str(p.get('valor_moeda') or '0')))}"
                for p in pagamentos
            ) or "-"
            statement_rows.append(
                f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('tipo_operacao') or '-').upper())}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>USD {escape(str(item.get('total_usd') or '0'))}</td><td>{escape(fechamento_txt)}</td><td>{escape(pagamentos_txt)}</td></tr>"
            )
        statement_rows_html = "".join(statement_rows) or statement_rows_html

    open_fechamentos_statement_html = "<tr><td colspan='5'>Nenhum fechamento parcial em aberto nesse periodo.</td></tr>"
    if needs_statement:
        open_fechamentos_statement_rows = []
        for item in open_fechamentos_statement[:12]:
            source = str(item.get("source") or "gold_transactions")
            item_id = str(item.get("id") or "-")
            id_label = f"GT-{item_id}" if source == "gold_transactions" else f"T-{item_id}"
            status = cast(Dict[str, Any], item.get("fechamento_status") or {})
            open_fechamentos_statement_rows.append(
                f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>{escape(str(status.get('fechado') or '0'))} g</td><td>{escape(str(status.get('aberto') or '0'))} g</td></tr>"
            )
        open_fechamentos_statement_html = "".join(open_fechamentos_statement_rows) or open_fechamentos_statement_html
    pending_open_gold_total = gold_caixa_metrics["ouro_pendente"]
    market_panel_html = _render_market_panel_html(market_snapshot, heading="Mercado", rail=True) if needs_market_rail else ""
    market_news_items = _get_market_news() if needs_market_news else []
    news_hub_html = _render_market_news_panel_html(market_news_items, limit=12) if needs_market_news else ""
    market_rail_html = ""
    if needs_market_rail:
        market_rail_html = f"""
    <aside class='market-rail is-minimized' id='marketRail'>
        {market_panel_html}
    </aside>
    """

    sidebar_inventory_html = ""
    if needs_sidebar_inventory:
        sidebar_inventory_html = f"""
                <div class='sidebar-metric'>
                    <span>Estoque</span>
                    <strong>{escape(str(inventory.get('available_grams', '0')))} g</strong>
                </div>
        """

    shared_top_shell_html = f"""
    <aside class='app-sidebar panel'>
        <div class='sidebar-shell'>
            <div class='sidebar-brand'>
                <div class='sidebar-brand-mark'>CW</div>
                <div class='sidebar-brand-copy'>
                    <span class='sidebar-kicker'>Caixa de compra</span>
                    <strong>Caixa SaaS</strong>
                    <small>{user_name}</small>
                </div>
            </div>
            <nav class='sidebar-nav'>{nav_html}</nav>
            <div class='sidebar-footer'>
                <div class='sidebar-metric'>
                    <span>Data</span>
                    <strong>{escape(day['date'])}</strong>
                </div>
                {sidebar_inventory_html}
                <div class='sidebar-actions'>
                    <a href='/reports/inventory-status' class='ghost-link mini-action sidebar-link' target='_blank'>JSON estoque</a>
                    <form method='post' action='/saas/logout'><button class='ghost-btn mini-action sidebar-link' type='submit'>Sair</button></form>
                </div>
            </div>
        </div>
    </aside>
    {market_rail_html}
    """

    default_alert_phone = str(session_user.get("telefone") or "")
    lot_monitor_entries: List[Dict[str, Any]] = []
    if current_page == "monitors":
        lot_monitor_model = _build_web_lot_monitor_view_model(
            lot_market_context,
            market_trend,
            default_alert_phone=default_alert_phone,
            entry_limit=24,
            alert_limit=4,
        )
        web_lot_ai_alerts = cast(List[Dict[str, Any]], lot_monitor_model.get("alerts") or [])
        web_lot_ai_summary = str(lot_monitor_model.get("summary") or "")
        lot_monitor_entries = cast(List[Dict[str, Any]], lot_monitor_model.get("entries") or [])
    market_snapshot_client = {
        "xau_usd_raw": str(market_snapshot.get("xau_usd_raw") or ""),
        "grama_ref_raw": str(market_snapshot.get("grama_ref_raw") or ""),
        "usd_brl_raw": str(market_snapshot.get("usd_brl_raw") or ""),
        "eur_usd_raw": str(market_snapshot.get("eur_usd_raw") or ""),
        "eur_brl_raw": str(market_snapshot.get("eur_brl_raw") or ""),
        "xau_source": str(market_snapshot.get("xau_source") or ""),
        "xau_source_label": str(market_snapshot.get("xau_source_label") or ""),
        "status": str(market_snapshot.get("status") or ""),
        "updated_at_label": str(market_snapshot.get("updated_at_label") or ""),
    }
    dashboard_bootstrap = {
        "chatHistory": chat_bootstrap,
        "lotAlerts": web_lot_ai_alerts,
        "lotMonitorEntries": lot_monitor_entries,
        "lotSummary": web_lot_ai_summary,
        "recentFx": recent_fx,
        "currentPage": current_page,
        "marketSnapshot": market_snapshot_client,
    }
    dashboard_bootstrap_json = _json_for_html_script(dashboard_bootstrap)

    enabled_lot_monitor_entries = [item for item in lot_monitor_entries if item.get("enabled")] if current_page == "monitors" else []
    full_lot_monitor_html = ""
    monitor_alerts_html = "<tr><td colspan='4'>Nenhum gatilho ativo agora.</td></tr>"
    if current_page == "monitors":
        full_lot_monitor_html = _render_lot_monitor_cards(
            lot_monitor_entries,
            "monitors",
            "Nenhum lote aberto para monitorar.",
            default_alert_phone,
        )
        monitor_alert_rows = []
        for alert in web_lot_ai_alerts[:8]:
            monitor_alert_rows.append(
                f"<tr><td>GT-{escape(str(alert.get('source_transaction_id') or '-'))}</td><td>{escape(str(alert.get('status_label') or '-'))}</td><td>{escape(str(alert.get('profit_pct') or '0'))}%</td><td>{escape(str(alert.get('reason') or '-'))}</td></tr>"
            )
        monitor_alerts_html = "".join(monitor_alert_rows) or monitor_alerts_html

    news_gold_count = sum(1 for item in market_news_items if _normalize_text(str(item.get("topic") or "")) in {"ouro", "gold"}) if needs_market_news else 0
    news_fx_count = (len(market_news_items) - news_gold_count) if needs_market_news else 0

    page_content_html = ""
    if current_page == "dashboard":
        page_content_html = f"""
        <section class='panel section'>
            <div data-fragment-url='/saas/fragments/dashboard-summary' data-fragment-priority='eager'>
                <div class='empty-state'>Carregando resumo executivo...</div>
            </div>
        </section>
        <div class='dashboard-shell'>
            <div class='dashboard-main'>
                <section class='panel section'>
                    <h2>Evolucao Operacional</h2>
                    <p class='hint'>As barras mostram as gramas brutas giradas por dia. A linha acompanha o ouro fino equivalente movimentado, refletindo melhor a qualidade real do metal no caixa.</p>
                    <div data-fragment-url='/saas/fragments/dashboard-trend' data-fragment-priority='eager'>
                        <div class='empty-state'>Carregando evolucao operacional...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Posicao Consolidada dos Caixas</h2>
                    <div class='balance-grid'>{balances_html}</div>
                </section>
                <section class='panel section'>
                    <h2>Estoque FIFO em Aberto</h2>
                    <div data-fragment-url='/saas/fragments/dashboard-inventory' data-fragment-priority='viewport'>
                        <div class='empty-state'>Carregando estoque FIFO...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Monitores IA Selecionados</h2>
                            <p class='hint'>O dashboard exibe apenas os lotes com monitor 24h habilitado. A pagina dedicada concentra todos os lotes abertos, inclusive os ainda nao monitorados.</p>
                        </div>
                        <a href='/saas/monitores' class='ghost-link mini-action'>Abrir pagina completa</a>
                    </div>
                    <div class='lot-monitor-explainer'>
                        <p class='hint'><strong>Como funciona:</strong> a IA da web revisa cada lote aberto em ciclos. Ela compara o preco atual com sua meta em USD/g e com a tendencia recente do ouro.</p>
                        <p class='hint'><span class='legend-chip positive'>Verde</span> indica oportunidade de venda ou meta atingida. <span class='legend-chip negative'>Vermelho</span> indica enfraquecimento e sugestao de proteger lucro. <span class='legend-chip neutral'>Cinza</span> indica que o lote ainda deve aguardar.</p>
                        <p class='hint'>Campos usados no gatilho: <strong>Meta USD/g</strong>, <strong>Lucro minimo %</strong> e <strong>Ativar monitor 24h</strong>. O card atualiza sozinho e a IA da web repete o aviso no banner e no chat quando surgir novo gatilho.</p>
                    </div>
                    <div class='lot-monitor-grid' data-fragment-url='/saas/fragments/dashboard-monitors' data-fragment-priority='viewport'>
                        <div class='empty-state'>Carregando monitores selecionados...</div>
                    </div>
                </section>
            </div>
            <div class='dashboard-side'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Radar de Noticias</h2>
                            <p class='hint'>Somente as 3 manchetes mais recentes entram no dashboard. A central de noticias preserva o fluxo completo.</p>
                        </div>
                        <a href='/saas/noticias' class='ghost-link mini-action'>Ver noticias</a>
                    </div>
                    <div data-fragment-url='/saas/fragments/dashboard-news' data-fragment-priority='idle'>
                        <div class='empty-state'>Carregando noticias...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Fechamentos Pendentes</h2>
                    <div data-fragment-url='/saas/fragments/dashboard-pending-closings' data-fragment-priority='idle'>
                        <div class='empty-state'>Carregando fechamentos pendentes...</div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Ultimas Operacoes</h2>
                    <div data-fragment-url='/saas/fragments/dashboard-recent-operations' data-fragment-priority='idle'>
                        <div class='empty-state'>Carregando ultimas operacoes...</div>
                    </div>
                </section>
            </div>
        </div>
        """
    elif current_page == "monitors":
        page_content_html = f"""
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Monitores IA dos Lotes</h2>
                            <p class='hint'>Esta pagina organiza a rotina de acompanhamento dos lotes abertos. O operador escolhe o que merece vigilancia 24h e a IA destaca lotes em janela de saida, meta batida ou perda de forca.</p>
                        </div>
                    </div>
                    <div class='cards'>
                        <div class='card'><small>Lotes Abertos</small><strong>{escape(str(len(lot_monitor_entries)))}</strong></div>
                        <div class='card'><small>Monitores 24h Ativos</small><strong>{escape(str(len(enabled_lot_monitor_entries)))}</strong></div>
                        <div class='card'><small>Gatilhos Ativos</small><strong>{escape(str(len(web_lot_ai_alerts)))}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Painel Completo de Lotes</h2>
                            <p class='hint'>Use esta area para ligar ou desligar monitores, calibrar meta por grama e exigir folga minima antes de vender.</p>
                        </div>
                    </div>
                    <div class='lot-monitor-grid'>{full_lot_monitor_html}</div>
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Fila de Gatilhos</h2>
                    <p class='hint'>Priorize primeiro meta batida, depois janela favoravel e por fim protecao de lucro. Isso preserva disciplina de caixa e evita operar lote fora do plano.</p>
                    <table>
                        <thead><tr><th>Lote</th><th>Status</th><th>P/L %</th><th>Leitura</th></tr></thead>
                        <tbody>{monitor_alerts_html}</tbody>
                    </table>
                </section>
            </div>
        </div>
        """
    elif current_page == "news_hub":
        page_content_html = f"""
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Central de Noticias</h2>
                            <p class='hint'>O dashboard ficou enxuto. Aqui ficam todas as noticias recentes usadas para leitura de contexto macro antes de ajustar preco, segurar lote ou travar lucro.</p>
                        </div>
                    </div>
                    <div class='cards'>
                        <div class='card'><small>Noticias Carregadas</small><strong>{escape(str(len(market_news_items)))}</strong></div>
                        <div class='card'><small>Radar Ouro</small><strong>{escape(str(news_gold_count))}</strong></div>
                        <div class='card'><small>Radar Cambio</small><strong>{escape(str(news_fx_count))}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Feed Completo</h2>
                    <p class='hint'>Leitura dedicada para ouro, dolar e referencias externas que podem deslocar preco, spread e urgencia de saida.</p>
                    {news_hub_html}
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Ultimas Operacoes</h2>
                    <p class='hint'>Cruze a noticia com o fluxo do balcao: manchete sem reflexo nas compras e vendas recentes raramente exige ajuste imediato.</p>
                    <table>
                        <thead><tr><th>ID</th><th>Tipo</th><th>Pessoa</th><th>Peso</th><th>Total</th></tr></thead>
                        <tbody>{recent_html}</tbody>
                    </table>
                </section>
            </div>
        </div>
        """
    elif current_page == "operation":
        page_content_html = f"""
        <div class='operation-layout'>
            <div class='operation-main'>
                <section class='panel section'>
                    <div class='section-head'>
                        <div>
                            <h2>Registro de Operacao</h2>
                            <p class='hint'>Area central para registro operacional. O formulario grava diretamente no sistema, enquanto o assistente pode apoiar a montagem por linguagem natural.</p>
                        </div>
                    </div>
                    <div class='notice info is-hidden notice-action' id='operationFormNotice'>
                        <span id='operationFormNoticeText'></span>
                        <a id='operationFormReceiptLink' class='notice-link is-hidden' href='#' target='_blank' rel='noopener'>Abrir recibo</a>
                    </div>
                    <form method='post' action='/saas/operations/quick' id='quickOperationForm'>
                        <input type='hidden' name='page' value='operation' />
                        <div class='quick-mode-bar'>
                            <button type='button' class='ghost-link mini-action' id='toggleQuickOrderMode'>Modo de lancamento agil</button>
                            <span class='hint' id='quickModeHint'>Enter avanca campo a campo. Ctrl+Enter confirma o registro.</span>
                        </div>
                        <div class='fields-3'>
                            <label data-quick-optional='1'>Operador
                                <input name='operador_id' value='{escape(values['operador_id'])}' required />
                            </label>
                            <label>Tipo
                                <select name='tipo_operacao' id='opTipoOperacao'>
                                    <option value='compra' {'selected' if values['tipo_operacao']=='compra' else ''}>Compra</option>
                                    <option value='venda' {'selected' if values['tipo_operacao']=='venda' else ''}>Venda</option>
                                </select>
                            </label>
                            <label data-quick-optional='1'>Origem
                                <select name='origem'>
                                    <option value='balcao' {'selected' if values['origem']=='balcao' else ''}>Balcao</option>
                                    <option value='fora' {'selected' if values['origem']=='fora' else ''}>Fora</option>
                                </select>
                            </label>
                        </div>
                        <div class='fields-2'>
                            <label>Material do ouro
                                <select name='gold_type' id='opGoldType'>
                                    <option value='fundido' {'selected' if _normalize_gold_type(values.get('gold_type'))=='fundido' else ''}>Fundido</option>
                                    <option value='queimado' {'selected' if _normalize_gold_type(values.get('gold_type'))=='queimado' else ''}>Queimado</option>
                                </select>
                            </label>
                            <label id='opQuebraWrap' class='{'is-hidden' if not (values.get('tipo_operacao') == 'compra' and _normalize_gold_type(values.get('gold_type')) == 'queimado') else ''}'>Quebra %
                                <input name='quebra' id='opQuebra' value='{escape(values.get('quebra', ''))}' placeholder='Obrigatorio se a compra for queimado' inputmode='decimal' />
                            </label>
                        </div>
                        <div class='fields-3'>
                            <label>Teor %
                                <input name='teor' id='opTeor' value='{escape(values['teor'])}' required inputmode='decimal' />
                            </label>
                            <label>Peso g
                                <input name='peso' id='opPeso' value='{escape(values['peso'])}' required inputmode='decimal' />
                            </label>
                            <label>Preco USD/g
                                <input name='preco_usd' id='opPrecoUsd' value='{escape(values['preco_usd'])}' required inputmode='decimal' />
                            </label>
                        </div>
                        <div class='operation-strip'>
                            <div class='mini-stat'><span>Ouro fino</span><strong id='opFineGold'>0.000 g</strong></div>
                            <div class='mini-stat'><span>Valor de referencia</span><strong id='opTotalUsd'>USD 0.00</strong></div>
                            <div class='mini-stat'><span>Base de fechamento</span><strong id='opTargetUsd'>USD 0.00</strong></div>
                            <div class='mini-stat'><span>Total liquidado</span><strong id='opPaidUsd'>USD 0.00</strong></div>
                            <div class='mini-stat'><span>Diferenca apurada</span><strong id='opDiffUsd'>USD 0.00</strong></div>
                        </div>
                        <div class='fields-2'>
                            <label>Fechamento g
                                <input name='fechamento_gramas' id='opFechamentoGramas' value='{escape(values['fechamento_gramas'])}' placeholder='vazio = total' inputmode='decimal' />
                            </label>
                            <label>Fechamento Tipo
                                <select name='fechamento_tipo' id='opFechamentoTipo'>
                                    <option value='total' {'selected' if values['fechamento_tipo']=='total' else ''}>Total</option>
                                    <option value='parcial' {'selected' if values['fechamento_tipo']=='parcial' else ''}>Parcial</option>
                                </select>
                            </label>
                        </div>
                        <p class='hint inline-hint' id='opFechamentoHint'>Selecione Total quando toda a quantidade ja estiver liquidada. Selecione Parcial quando houver saldo a regularizar posteriormente.</p>
                        <div class='quick-actions'>
                            <button type='button' class='ghost-link mini-action' id='opUsePesoTotal'>Aplicar peso integral no fechamento</button>
                            <button type='button' class='ghost-link mini-action' id='opUseTotalAsUsd'>Replicar base de fechamento no pagamento em USD</button>
                        </div>
                        <div class='fields-2'>
                            <label class='client-picker'>Cliente
                                <input type='hidden' name='cliente_id' id='opClienteId' value='{escape(values['cliente_id'])}' />
                                <input type='hidden' name='cliente_lookup_meta' id='opClienteLookupMeta' value='{escape(values['cliente_lookup_meta'])}' />
                                <input name='pessoa' id='opPessoa' value='{escape(values['pessoa'])}' placeholder='Digite nome, telefone ou documento' autocomplete='off' required />
                                <div class='client-autocomplete is-hidden' id='opClienteResults'></div>
                                <span class='hint' id='opClienteMeta'>{escape(values['cliente_lookup_meta'] or 'Selecione um cliente existente ou use o cadastro rapido abaixo.')}</span>
                            </label>
                            <label>Total liquidado em USD
                                <input name='total_pago_usd' id='opTotalPagoUsd' value='{escape(values['total_pago_usd'])}' placeholder='utilize apenas se nao informar as linhas de pagamento' inputmode='decimal' />
                            </label>
                        </div>
                        <div class='quick-actions'>
                            <button type='button' class='ghost-link mini-action' id='toggleInlineCliente'>Cadastro rapido de cliente</button>
                            <a href='/saas/clientes' class='ghost-link mini-action'>Abrir base de clientes</a>
                        </div>
                        <div class='tip-box inline-client-box {'is-hidden' if values.get('inline_cliente_mode', '0') != '1' else ''}' id='inlineClienteBox'>
                            <input type='hidden' name='inline_cliente_mode' id='inlineClienteMode' value='{escape(values.get('inline_cliente_mode', '0'))}' />
                            <strong>Cadastro rapido no proprio lancamento</strong>
                            <p class='hint'>Use este bloco quando o cliente ainda nao existir. O operador continua no fluxo, registra o cliente e segue com a operacao.</p>
                            <div class='fields-3'>
                                <label>Nome do cliente
                                    <input name='inline_cliente_nome' id='inlineClienteNome' value='{escape(values.get('inline_cliente_nome', ''))}' />
                                </label>
                                <label>Telefone
                                    <input name='inline_cliente_telefone' value='{escape(values.get('inline_cliente_telefone', ''))}' />
                                </label>
                                <label>Documento
                                    <input name='inline_cliente_documento' value='{escape(values.get('inline_cliente_documento', ''))}' />
                                </label>
                            </div>
                            <div class='fields-3'>
                                <label>Apelido / referencia
                                    <input name='inline_cliente_apelido' value='{escape(values.get('inline_cliente_apelido', ''))}' />
                                </label>
                                <label>Saldo inicial em ouro (g)
                                    <input name='inline_cliente_saldo_xau' value='{escape(values.get('inline_cliente_saldo_xau', ''))}' inputmode='decimal' />
                                </label>
                                <label>Observacoes
                                    <input name='inline_cliente_observacoes' value='{escape(values.get('inline_cliente_observacoes', ''))}' />
                                </label>
                            </div>
                            <div class='inline-client-actions'>
                                <button type='button' id='inlineClienteSave' class='ghost-link mini-action'>Confirmar cadastro do cliente</button>
                                <span class='hint inline-client-status' id='inlineClienteStatus'>Salve o cliente aqui para selecionar a conta antes de registrar a operacao.</span>
                            </div>
                        </div>
                        <div class='payment-stack'>
                            {payment_rows_html}
                        </div>
                        <div class='tip-box rateio-box'>
                            <strong>Rateio automatico de liquidacao</strong>
                            <p class='hint' id='opRateioHint'>Ao informar o percentual por moeda, o sistema distribui a base de fechamento entre os pagamentos e calcula automaticamente os respectivos valores.</p>
                        </div>
                        <div class='tip-box op-summary-box'>
                            <strong>Resumo operacional</strong>
                            <p class='hint' id='opSummaryText'>Preencha peso, preco e pagamentos para gerar uma sintese da operacao antes da confirmacao.</p>
                        </div>
                        <label data-quick-optional='1'>Observacoes
                            <textarea name='observacoes' placeholder='Detalhes adicionais'>{escape(values['observacoes'])}</textarea>
                        </label>
                        <label data-quick-optional='1'><input type='checkbox' name='risk_override' value='1' style='width:auto;margin-right:8px;' /> Autorizar risco se o operador informado for admin</label>
                        <button type='submit'>Registrar operacao</button>
                    </form>
                </section>
            </div>
            <aside class='operation-side'>
                <section class='panel section'>
                    <h2>Assistente Operacional</h2>
                    <p class='hint'>O assistente pode conduzir consultas, esclarecer duvidas e estruturar a operacao a partir de instrucoes em texto livre.</p>
                    <div class='tip-box ai-draft-box'>
                        <strong>Preparar pre-lancamento</strong>
                        <form id='aiDraftForm' class='ai-draft-form'>
                            <label>Descreva a operacao
                                <textarea id='aiDraftInput' placeholder='Ex.: comprei 12,4g teor 91,6 a 104 usd de Joao pago em 300 USD e 7600 SRD'></textarea>
                            </label>
                            <div class='quick-actions'>
                                <button type='submit'>Gerar pre-lancamento</button>
                            </div>
                            <p class='hint' id='aiDraftStatus'>A IA interpreta a descricao e preenche o formulario para conferencia antes do registro.</p>
                        </form>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Posicao Financeira e de Ouro</h2>
                    <p class='hint'>Consulta lateral com os saldos por moeda e a segregacao entre ouro fisico em caixa, ouro de terceiros pendente e posicao propria.</p>
                    <div class='operation-balance-grid'>{money_balances_html}</div>
                    <div class='cards cards-closure operation-side-cards compact-gold-cards'>
                        <div class='card'><small>Ouro de terceiros pendente</small><strong>{escape(str(pending_open_gold_total))} g</strong></div>
                        <div class='card'><small>Ouro fisico em caixa</small><strong>{escape(_format_caixa_movement('XAU', gold_caixa_metrics['ouro_em_caixa']))}</strong></div>
                        <div class='card'><small>Posicao propria em ouro</small><strong>{escape(_format_caixa_movement('XAU', gold_caixa_metrics['ouro_proprio']))}</strong></div>
                    </div>
                    <div class='cards cards-closure operation-side-cards compact-gold-cards'>
                        <div class='card'><small>Ouro fino aberto</small><strong>{escape(str(operation_lot_market_context.get('available_fine_grams', '0')))} g</strong></div>
                        <div class='card'><small>Mercado em aberto</small><strong>USD {escape(str(operation_lot_market_context.get('market_value_usd', '0')))}</strong></div>
                        <div class='card'><small>P/L em aberto</small><strong class='{'positive' if Decimal(str(operation_lot_market_context.get('unrealized_pnl_usd', '0'))) >= 0 else 'negative'}'>USD {escape(str(operation_lot_market_context.get('unrealized_pnl_usd', '0')))}</strong></div>
                    </div>
                    <p class='hint'>No caixa, os lotes seguem segregados por teor. Isso impede leitura misturada entre, por exemplo, 90 e 85, e mostra onde a posição aberta está concentrando lucro ou risco.</p>
                    <table style='margin-top:14px;'>
                        <thead><tr><th>Teor</th><th>Gramas</th><th>P/L</th></tr></thead>
                        <tbody>{operation_lot_teor_html}</tbody>
                    </table>
                    <table style='margin-top:14px;'>
                        <thead><tr><th>Lote</th><th>Teor</th><th>Saldo</th><th>P/L</th></tr></thead>
                        <tbody>{risk_lots_html}</tbody>
                    </table>
                </section>
            </aside>
        </div>
        """
    elif current_page == "clients":
        page_content_html = _render_saas_clients_page(client_view or _build_saas_clients_context(db), values)
    elif current_page == "profile":
        bootstrap_flag = "Sim" if session_user.get("web_pin_bootstrap_required") else "Nao"
        page_content_html = f"""
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Perfil do Usuario</h2>
                    <p class='hint'>Esta area concentra as informacoes cadastrais, credenciais e parametros vinculados ao acesso atual do painel.</p>
                    <div class='cards'>
                        <div class='card'><small>Nome</small><strong>{user_name}</strong></div>
                        <div class='card'><small>Telefone</small><strong>{user_phone}</strong></div>
                        <div class='card'><small>Perfil</small><strong>{user_role}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Seguranca de Acesso</h2>
                    <p class='hint'>Atualize o PIN sempre que houver bootstrap ou renovacao de credencial. Em caso de PIN temporario, recomenda-se a troca imediata.</p>
                    <form method='post' action='/saas/profile/pin'>
                        <input type='hidden' name='page' value='profile' />
                        <div class='fields-3'>
                            <label>PIN atual
                                <input type='password' name='current_pin' inputmode='numeric' required />
                            </label>
                            <label>Novo PIN
                                <input type='password' name='new_pin' inputmode='numeric' required />
                            </label>
                            <label>Confirmar novo PIN
                                <input type='password' name='confirm_pin' inputmode='numeric' required />
                            </label>
                        </div>
                        <button type='submit'>Atualizar PIN de acesso</button>
                    </form>
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Conta de Acesso</h2>
                    <div class='cards'>
                        <div class='card'><small>Login ativo</small><strong>{user_phone}</strong></div>
                        <div class='card'><small>PIN temporario</small><strong>{escape(bootstrap_flag)}</strong></div>
                        <div class='card'><small>Status da sessao</small><strong>Ativa</strong></div>
                    </div>
                    <div class='tip-box'>
                        <strong>Escopo desta area</strong>
                        <p class='hint'>Esta aba concentra configuracoes pessoais e informacoes da conta, preservando a separacao entre gestao de acesso e rotina operacional.</p>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>Saldos de Referencia</h2>
                    <p class='hint'>Os saldos permanecem visiveis nesta area como consulta rapida, sem necessidade de retornar ao painel inicial.</p>
                    <div class='balance-grid'>{balances_html}</div>
                </section>
            </div>
        </div>
        """
    else:
        page_content_html = f"""
        <section class='panel section'>
            <div class='section-head'>
                <div>
                    <h2>Extrato Operacional</h2>
                    <p class='hint'>Por padrao, a consulta apresenta o movimento do dia e permite filtragem por intervalo fechado de datas.</p>
                </div>
            </div>
            <form method='get' action='/saas/extrato' class='filter-bar'>
                <label>Data inicial
                    <input type='date' name='start_date' value='{escape(str(statement.get('start_date') or ''))}' />
                </label>
                <label>Data final
                    <input type='date' name='end_date' value='{escape(str(statement.get('end_date') or ''))}' />
                </label>
                <button type='submit'>Aplicar filtro</button>
                <a href='/saas/extrato' class='ghost-link'>Hoje</a>
            </form>
            <div class='cards'>
                <div class='card'><small>Periodo</small><strong>{escape(str(statement.get('label') or '-'))}</strong></div>
                <div class='card'><small>Operacoes no periodo</small><strong>{escape(str(statement.get('summary', {}).get('total_operacoes', 0)))}</strong></div>
                <div class='card'><small>Volume em USD</small><strong>USD {escape(str(statement.get('summary', {}).get('total_usd', '0')))}</strong></div>
            </div>
        </section>
        <div class='grid'>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Movimentacoes</h2>
                    <table>
                        <thead><tr><th>ID</th><th>Tipo</th><th>Pessoa</th><th>Peso</th><th>Total</th><th>Fechamento</th><th>Pagamentos</th></tr></thead>
                        <tbody>{statement_rows_html}</tbody>
                    </table>
                </section>
            </div>
            <div class='stack'>
                <section class='panel section'>
                    <h2>Posicoes com Fechamento Pendente</h2>
                    <p class='hint'>Este quadro apresenta as operacoes do periodo filtrado que permaneceram com fechamento parcial e ainda possuem gramas em aberto.</p>
                    <table>
                        <thead><tr><th>ID</th><th>Pessoa</th><th>Peso</th><th>Fechado</th><th>Em aberto</th></tr></thead>
                        <tbody>{open_fechamentos_statement_html}</tbody>
                    </table>
                </section>
                <section class='panel section'>
                    <h2>Resumo Textual</h2>
                    <pre>{escape(str(statement.get('statement_text') or ''))}</pre>
                </section>
            </div>
        </div>
        """

    floating_ai_html = f"""
    <aside class='ai-float panel minimized' id='aiChatWidget' data-page='{current_page}'>
        <div class='ai-shell'>
            <div class='ai-head' id='aiChatHandle'>
                <div>
                    <strong>IA Operacional</strong>
                    <p>Canal de apoio operacional para consultas, registros e orientacoes do fluxo.</p>
                </div>
                <span class='ai-drag-handle' aria-hidden='true'>Arraste</span>
            </div>
            <div class='ai-body' id='aiChatBody'>
                <div class='ai-status'>Disponivel</div>
                <div class='ai-thread' id='aiChatThread' aria-live='polite'></div>
                <form method='post' action='/saas/console' id='aiChatForm' class='ai-chat-form'>
                    <input type='hidden' name='page' value='{current_page}' />
                    {chat_operator_field}
                    <label class='chat-composer'>
                        <textarea id='aiChatInput' name='console_mensagem' rows='2' placeholder='Digite a solicitacao operacional...' required>{escape(values['console_mensagem'])}</textarea>
                    </label>
                    <div class='ai-actions'>
                        <span class='chat-helper'>Enter envia. Shift+Enter insere nova linha.</span>
                        <button type='submit' id='aiChatSend'>Enviar</button>
                    </div>
                </form>
            </div>
        </div>
    </aside>
    """

    saas_css_url = _asset_url("saas.css")
    saas_js_url = _asset_url("saas.js")

    return f"""
    <html>
        <head>
            <title>Caixa SaaS Dashboard</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <link rel='preload' href='{saas_css_url}' as='style'>
            <link href='{saas_css_url}' rel='stylesheet'>
            <link rel='preload' href='{saas_js_url}' as='script'>
        </head>
        <body>
            {shared_top_shell_html}
            <div class='wrap app-content-wrap'>
                {bootstrap_notice}
                {notice_html}
                {web_ai_banner_html}
                {page_content_html}
            </div>
            {floating_ai_html}
            <script id='saasDashboardBootstrap' type='application/json'>{dashboard_bootstrap_json}</script>
            <script src='{saas_js_url}' defer></script>
        </body>
    </html>
    """


def _is_help_menu_request(message: str) -> bool:
    text = _normalize_text(message)
    keywords = [
        "menu",
        "ajuda",
        "help",
        "comandos",
        "o que voce pode fazer",
        "o que você pode fazer",
        "como funciona",
        "funcionalidades",
    ]
    return any(k in text for k in keywords)


def _is_greeting(message: str) -> bool:
    text = _normalize_text(message)
    # Remove punctuation and collapse spaces for robust matching.
    compact = re.sub(r"[^a-z0-9\s]", " ", text)
    compact = re.sub(r"\s+", " ", compact).strip()

    # Accept common variants like: "oii", "olaaa", "ola!", "bom diaaa".
    if re.match(r"^o+i+$", compact):
        return True
    if re.match(r"^o+l+a+$", compact):
        return True
    if compact.startswith("bom dia") or compact.startswith("boa tarde") or compact.startswith("boa noite"):
        return True
    if compact in {"hello", "hi", "hey"}:
        return True
    return False


def _looks_like_new_operation_start(message: str) -> bool:
    text = _normalize_text(message)
    operation_tokens = [
        "comprei",
        "comprar",
        "compra",
        "vendi",
        "vender",
        "venda",
        "cambio",
        "cambio",
        "troca",
    ]
    has_operation_word = any(token in text for token in operation_tokens)
    has_asset_or_amount = ("ouro" in text) or bool(re.search(r"\d", text))
    return has_operation_word and has_asset_or_amount


def _sanitize_nome(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:80]


def _parse_operation_id(raw: str) -> Optional[int]:
    text = raw.strip().lower()
    match_op = re.search(r"op-\d{8}-(\d+)", text)
    if match_op:
        return int(match_op.group(1))

    match_num = re.search(r"\b(\d{1,12})\b", text)
    if match_num:
        return int(match_num.group(1))
    return None


def _parse_operation_reference(raw: str) -> Tuple[str, Optional[int]]:
    text = raw.strip().lower()
    if text.startswith("gt-"):
        return "gold", _parse_operation_id(text)
    if text.startswith("t-") or text.startswith("op-"):
        return "transacao", _parse_operation_id(text)
    return "transacao", _parse_operation_id(text)


def _normalize_edit_field(raw: str) -> Optional[str]:
    field = _normalize_text(raw)
    aliases = {
        "preco": "cotacao_usada",
        "preço": "cotacao_usada",
        "cotacao": "cotacao_usada",
        "cotacao_usada": "cotacao_usada",
        "quantidade": "quantidade",
        "qtd": "quantidade",
        "moeda": "moeda_liquidacao",
        "moeda_liquidacao": "moeda_liquidacao",
        "valor_moeda": "valor_moeda",
        "cambio": "cambio_para_usd",
        "câmbio": "cambio_para_usd",
        "cambio_para_usd": "cambio_para_usd",
    }
    return aliases.get(field)


def _try_handle_whatsapp_commands(
    db: DatabaseClient,
    usuario: Dict[str, Any],
    remetente: str,
    mensagem: str,
) -> Optional[Dict[str, Any]]:
    text = mensagem.strip()
    text_norm = _normalize_text(text)

    # extrato: intercept before AI so it starts the dedicated extract flow.
    if re.match(r"^extrato\b", text_norm):
        if any(w in text_norm for w in {"hoje", "dia", "agora"}):
            day = _build_day_range(None)
            _clear_session(db, remetente)
            return _build_extrato_response(db, day["start"], day["end"], f"Hoje ({day['date']})")
        if any(w in text_norm for w in {"semana", "week"}):
            week = _build_week_range()
            _clear_session(db, remetente)
            return _build_extrato_response(db, week["start"], week["end"], week["label"])
        _save_session(db, remetente, "await_extrato_periodo", {})
        return {
            "mensagem": (
                "EXTRATO OPERACIONAL - selecione o periodo de consulta:\n"
                "1) Hoje\n"
                "2) Esta semana\n"
                "3) Informar intervalo de datas"
            ),
            "dados": {"etapa": "await_extrato_periodo"},
        }

    # editar 123 preco 110
    edit_match = re.match(r"^\s*(editar|edit)\s+(.+?)\s+([\w_çÇãÃâÂáÁéÉíÍóÓúÚ]+)\s+(.+?)\s*$", text, re.IGNORECASE)
    if edit_match:
        op_token = edit_match.group(2)
        field_token = edit_match.group(3)
        value_token = edit_match.group(4)

        op_kind, op_id = _parse_operation_reference(op_token)
        if op_id is None:
            return {"mensagem": "ID inválido. Exemplo: editar 123 preco 110", "dados": {"acao": "editar_operacao"}}

        if op_kind == "gold":
            return {
                "mensagem": "Operações guiadas GT não suportam edição direta. Use cancelar GT-<id> e refaça a operação.",
                "dados": {"acao": "editar_operacao", "id": op_id, "kind": "gold", "permitido": False},
            }

        transacao_resp = (
            db.client.table("transacoes")
            .select("id,operador_id,quantidade,cotacao_usada,valor_total,moeda_liquidacao,valor_moeda,cambio_para_usd,status")
            .eq("id", op_id)
            .limit(1)
            .execute()
        )
        rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
        if not rows:
            return {"mensagem": f"Operação {op_id} não encontrada.", "dados": {"acao": "editar_operacao"}}

        row = rows[0]
        is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
        if not is_admin and str(row.get("operador_id", "")) != remetente:
            return {
                "mensagem": "Você não tem permissão para editar esta operação.",
                "dados": {"acao": "editar_operacao", "permitido": False},
            }

        field = _normalize_edit_field(field_token)
        if field is None:
            return {
                "mensagem": "Campo inválido. Use: preco, quantidade, moeda, valor_moeda ou cambio.",
                "dados": {"acao": "editar_operacao"},
            }

        update_payload: Dict[str, Any] = {}

        quantidade = Decimal(str(row.get("quantidade", "0")))
        cotacao = Decimal(str(row.get("cotacao_usada", "0")))
        moeda = str(row.get("moeda_liquidacao") or "USD").upper()
        valor_moeda = Decimal(str(row.get("valor_moeda") or row.get("valor_total") or "0"))
        cambio = Decimal(str(row.get("cambio_para_usd") or "1"))

        if field in {"quantidade", "cotacao_usada", "valor_moeda", "cambio_para_usd"}:
            novo = _parse_decimal_from_text(value_token, field)
            if field in {"quantidade", "cotacao_usada", "cambio_para_usd"} and novo <= 0:
                return {"mensagem": f"Valor inválido para {field}.", "dados": {"acao": "editar_operacao"}}
            if field == "valor_moeda" and novo < 0:
                return {"mensagem": "O valor da moeda não pode ser negativo.", "dados": {"acao": "editar_operacao"}}

            if field == "quantidade":
                quantidade = novo
                update_payload["quantidade"] = str(novo)
            elif field == "cotacao_usada":
                cotacao = novo
                update_payload["cotacao_usada"] = str(novo)
            elif field == "valor_moeda":
                valor_moeda = novo
                update_payload["valor_moeda"] = str(novo)
            elif field == "cambio_para_usd":
                cambio = novo
                update_payload["cambio_para_usd"] = str(novo)

        elif field == "moeda_liquidacao":
            nova_moeda = _normalize_text(value_token).upper()
            if nova_moeda not in _MOEDAS_SUPORTADAS:
                return {
                    "mensagem": "Moeda inválida. Use: USD, EUR, SRD ou BRL.",
                    "dados": {"acao": "editar_operacao"},
                }
            moeda = nova_moeda
            update_payload["moeda_liquidacao"] = moeda

        total_usd = money(quantidade * cotacao)
        update_payload["valor_total"] = str(total_usd)

        if moeda == "USD":
            update_payload["moeda_liquidacao"] = "USD"
            update_payload["cambio_para_usd"] = "1"
            update_payload["valor_moeda"] = str(total_usd)
        else:
            if field != "valor_moeda":
                valor_moeda = money(total_usd * cambio)
            update_payload["valor_moeda"] = str(valor_moeda)
            update_payload["cambio_para_usd"] = str(cambio)

        db.client.table("transacoes").update(update_payload).eq("id", op_id).execute()
        _invalidate_operation_related_view_caches()
        return {
            "mensagem": f"✅ Operação {op_id} atualizada com sucesso.",
            "dados": {"acao": "editar_operacao", "id": op_id, "campos": list(update_payload.keys())},
        }

    # cancelar 123
    cancel_match = re.match(r"^\s*(cancelar|cancela|excluir|delete)\s+(.+?)\s*$", text, re.IGNORECASE)
    if cancel_match:
        op_kind, op_id = _parse_operation_reference(cancel_match.group(2))
        if op_id is None:
            return {"mensagem": "ID inválido. Exemplo: cancelar 123", "dados": {"acao": "cancelar_operacao"}}

        if op_kind == "gold":
            transacao_resp = (
                db.client.table("gold_transactions")
                .select("*")
                .eq("id", op_id)
                .limit(1)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
            if not rows:
                return {"mensagem": f"Operação GT-{op_id} não encontrada.", "dados": {"acao": "cancelar_operacao", "kind": "gold"}}

            row = rows[0]
            is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
            if not is_admin and str(row.get("operador_id", "")) != remetente:
                return {
                    "mensagem": "Você não tem permissão para cancelar esta operação guiada.",
                    "dados": {"acao": "cancelar_operacao", "permitido": False, "kind": "gold"},
                }

            ok = db.cancel_gold_transaction(op_id, cancelled_by=remetente)
            if not ok:
                return {"mensagem": "Não consegui cancelar a operação guiada agora.", "dados": {"acao": "cancelar_operacao", "id": op_id, "kind": "gold"}}
            _invalidate_operation_related_view_caches()
            return {
                "mensagem": f"✅ Operação GT-{op_id} cancelada com sucesso.",
                "dados": {"acao": "cancelar_operacao", "id": op_id, "status": "cancelada", "kind": "gold"},
            }

        transacao_resp = (
            db.client.table("transacoes")
            .select("id,operador_id,status")
            .eq("id", op_id)
            .limit(1)
            .execute()
        )
        rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
        if not rows:
            return {"mensagem": f"Operação {op_id} não encontrada.", "dados": {"acao": "cancelar_operacao"}}

        row = rows[0]
        is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
        if not is_admin and str(row.get("operador_id", "")) != remetente:
            return {
                "mensagem": "Você não tem permissão para cancelar esta operação.",
                "dados": {"acao": "cancelar_operacao", "permitido": False},
            }

        db.client.table("transacoes").update({"status": "cancelada"}).eq("id", op_id).execute()
        _invalidate_operation_related_view_caches()
        return {
            "mensagem": f"✅ Operação {op_id} cancelada com sucesso.",
            "dados": {"acao": "cancelar_operacao", "id": op_id, "status": "cancelada"},
        }

    return None


def _needs_name_onboarding(usuario: Dict[str, Any]) -> bool:
    nome = str(usuario.get("nome") or "").strip().lower()
    if not nome:
        return True
    placeholders = {"operador", "usuario", "usuário", "sem nome", "unknown", "n/a"}
    return nome in placeholders


def _build_whatsapp_checklist_menu() -> str:
    return (
        "Central de atendimento operacional:\n"
        "──────────────────\n"
        "1) Registrar operacao de compra ou venda\n"
        "   Ex: compra | venda | comprei ouro 2g\n\n"
        "2) Consultar posicao de caixa\n"
        "   Ex: caixa | caixa eur | caixa srd | caixa xau\n\n"
        "3) Consultar extrato\n"
        "   Ex: extrato | extrato hoje | extrato semana\n\n"
        "4) Ajustar operacao\n"
        "   Ex: editar 123 preco 110 | editar 123 quantidade 2.5\n\n"
        "5) Cancelar operacao\n"
        "   Ex: cancelar 123\n"
        "──────────────────\n"
        "Se preferir, descreva diretamente a solicitacao operacional em texto livre."
    )


def _build_caixa_response(db: DatabaseClient, requested_currency: Optional[str] = None) -> Dict[str, Any]:
    """Build safe-to-display caixa status with 5 independent cashes (5 caixas).
    
    NEW STRUCTURE (as of refactor):
    - Caixa XAU: gramas de ouro (quantidade)
    - Caixa EUR: saldo em euros (sem conversão)
    - Caixa USD: saldo em dólares (sem conversão)
    - Caixa SRD: saldo em surinamês (sem conversão)
    - Caixa BRL: saldo em reais (sem conversão)
    
    Each caixa is independent. No USD reference layer.
    """
    day = _build_day_range(None)
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    saldo = db.get_saldo_caixa()
    
    ops_hoje = int(summary.get("total_operacoes", 0) or 0)
    
    # New structure: each currency directly in saldo
    saldo_xau = Decimal(str(saldo.get("XAU", "0")))
    saldo_eur = Decimal(str(saldo.get("EUR", "0")))
    saldo_usd = Decimal(str(saldo.get("USD", "0")))
    saldo_srd = Decimal(str(saldo.get("SRD", "0")))
    saldo_brl = Decimal(str(saldo.get("BRL", "0")))
    gold_caixa_metrics = _build_gold_caixa_metrics_from_pending_grams(saldo_xau, db.get_gold_pending_closure_grams())
    ouro_pendente = gold_caixa_metrics["ouro_pendente"]
    ouro_proprio = gold_caixa_metrics["ouro_proprio"]
    
    def situacao_txt(val: Decimal) -> str:
        return "entrou mais 💰" if val > 0 else ("nada" if val == 0 else "saiu mais 📉")
    
    if requested_currency:
        moeda = requested_currency.upper()
        
        if moeda == "XAU":
            resposta = (
                f"💰 CAIXA OURO (XAU)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Ouro fisico em caixa: {saldo_xau:,.3f} g\n"
                f"Ouro de terceiros pendente: {ouro_pendente:,.3f} g\n"
                f"Posicao propria em ouro: {ouro_proprio:,.3f} g\n"
                f"Situacao: {situacao_txt(saldo_xau)}\n"
                "════════════════════════════════"
            )
        elif moeda == "EUR":
            resposta = (
                f"🇪🇺 CAIXA EURO (EUR)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: EUR {saldo_eur:,.2f}\n"
                f"Situacao: {situacao_txt(saldo_eur)}\n"
                "════════════════════════════════"
            )
        elif moeda == "USD":
            resposta = (
                f"🇺🇸 CAIXA DÓLAR (USD)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: $ {saldo_usd:,.2f}\n"
                f"Situacao: {situacao_txt(saldo_usd)}\n"
                "════════════════════════════════"
            )
        elif moeda == "SRD":
            resposta = (
                f"🇸🇷 CAIXA SURINAMÊS (SRD)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: SRD {saldo_srd:,.2f}\n"
                f"Situacao: {situacao_txt(saldo_srd)}\n"
                "════════════════════════════════"
            )
        elif moeda == "BRL":
            resposta = (
                f"🇧🇷 CAIXA REAL (BRL)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: R$ {saldo_brl:,.2f}\n"
                f"Situacao: {situacao_txt(saldo_brl)}\n"
                "════════════════════════════════"
            )
        else:
            resposta = f"Moeda {moeda} não reconhecida. Digite: xau, eur, usd, srd ou brl"
    else:
        # Default: show all 5 caixas
        resposta = (
            f"📊 POSICAO CONSOLIDADA DOS 5 CAIXAS\n"
            f"Data: {day['date']}\n"
            f"Operações hoje: {ops_hoje}\n"
            "════════════════════════════════════════════\n"
            f"1) 💰 OURO (XAU):      {saldo_xau:>10,.3f} g\n"
            f"   Situacao: {situacao_txt(saldo_xau)}\n"
            f"   Ouro de terceiros: {ouro_pendente:>8,.3f} g\n"
            f"   Posicao propria:   {ouro_proprio:>8,.3f} g\n"
            "\n"
            f"2) 🇪🇺 EURO (EUR):      EUR {saldo_eur:>10,.2f}\n"
            f"   Situacao: {situacao_txt(saldo_eur)}\n"
            "\n"
            f"3) 🇺🇸 DÓLAR (USD):     $ {saldo_usd:>12,.2f}\n"
            f"   Situacao: {situacao_txt(saldo_usd)}\n"
            "\n"
            f"4) 🇸🇷 SURINAMÊS (SRD): SRD {saldo_srd:>10,.2f}\n"
            f"   Situacao: {situacao_txt(saldo_srd)}\n"
            "\n"
            f"5) 🇧🇷 REAL (BRL):      R$ {saldo_brl:>11,.2f}\n"
            f"   Situacao: {situacao_txt(saldo_brl)}\n"
            "════════════════════════════════════════════\n"
            "Legenda operacional:\n"
            "- 💰 entrou mais: houve incremento liquido neste caixa\n"
            "- 📉 saiu mais: houve reducao liquida neste caixa\n"
            "- nada: movimentacao equilibrada\n"
            "- Ouro de terceiros: ouro de cliente ainda nao liquidado\n"
            "- Posicao propria: ouro fisico em caixa deduzido do saldo de terceiros\n"
            "\nPara detalhar um caixa, responda:\n"
            "1 (ouro) | 2 (euro) | 3 (dólar) | 4 (surinamês) | 5 (real)"
        )
    
    return {
        "mensagem": resposta,
        "dados": {
            "intencao": "consultar_relatorio",
            "date": day["date"],
            "saldo_xau": str(saldo_xau),
            "ouro_pendente": str(ouro_pendente),
            "ouro_proprio": str(ouro_proprio),
            "saldo_eur": str(saldo_eur),
            "saldo_usd": str(saldo_usd),
            "saldo_srd": str(saldo_srd),
            "saldo_brl": str(saldo_brl),
            "ops_hoje": ops_hoje,
            "summary": summary,
            "requested_currency": requested_currency,
        },
    }


def _build_extrato_response(
    db: DatabaseClient,
    start_iso: str,
    end_iso: str,
    label_periodo: str,
    transactions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a professional bank-style transaction statement for the given period."""
    statement_transactions = transactions if transactions is not None else db.get_extrato_transactions(start_iso, end_iso)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    moeda_simbolo: Dict[str, str] = {"USD": "$", "EUR": "EUR ", "SRD": "SRD ", "BRL": "R$"}

    linhas: List[str] = [
        "===== EXTRATO =====",
        f"Periodo: {label_periodo}",
        f"Total: {len(statement_transactions)} operac{'oes' if len(statement_transactions) != 1 else 'ao'}",
        "====================",
    ]

    total_compra_g = Decimal("0")
    total_venda_g = Decimal("0")
    total_compra_usd = Decimal("0")
    total_venda_usd = Decimal("0")

    for i, t in enumerate(statement_transactions, 1):
        tipo = str(t.get("tipo_operacao") or "").upper()
        data_hora_raw = str(t.get("criado_em") or "")
        try:
            dt = datetime.fromisoformat(data_hora_raw.replace("Z", "+00:00"))
            dt_local = dt + timedelta(hours=tz_offset_hours)
            data_fmt = dt_local.strftime("%d/%m %H:%M")
        except Exception:
            data_fmt = data_hora_raw[:16]

        peso = Decimal(str(t.get("peso") or "0"))
        preco_usd = Decimal(str(t.get("preco_usd") or "0"))
        total_usd_val = Decimal(str(t.get("total_usd") or "0"))
        total_pago = Decimal(str(t.get("total_pago_usd") or total_usd_val))
        diferenca = Decimal(str(t.get("diferenca_usd") or "0"))
        pessoa = str(t.get("pessoa") or "").strip()
        observacoes = str(t.get("observacoes") or "").strip()
        status = str(t.get("status") or "registrada")
        source = str(t.get("source") or "transacoes")
        tid = t.get("id")
        id_prefixado = f"GT-{tid}" if source == "gold_transactions" else f"T-{tid}"

        linhas.append("--------------------")
        status_tag = f" [{status.upper()}]" if status not in ("registrada", "") else ""
        linhas.append(f"#{i} | {data_fmt} | {tipo}{status_tag}")
        if tid:
            linhas.append(f"ID: {id_prefixado}")
        if peso > 0:
            linhas.append(f"Peso: {peso:,.3f} g")
        if preco_usd > 0:
            linhas.append(f"Preco: ${preco_usd:,.2f}/g")
        linhas.append(f"Total ref: ${total_usd_val:,.2f}")

        pagamentos: List[Dict[str, Any]] = t.get("pagamentos") or []
        if pagamentos:
            for p in pagamentos:
                moeda = str(p.get("moeda") or "USD").upper()
                valor_m = Decimal(str(p.get("valor_moeda") or "0"))
                cambio = Decimal(str(p.get("cambio_para_usd") or "1"))
                simbolo = moeda_simbolo.get(moeda, f"{moeda} ")
                if moeda == "USD":
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f}")
                else:
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f} (cambio: {cambio:,.4f})")
        else:
            moeda = str(t.get("moeda") or "USD").upper()
            valor_m_raw = t.get("valor_moeda")
            if valor_m_raw:
                valor_m = Decimal(str(valor_m_raw))
                cambio_raw = t.get("cambio_para_usd")
                cambio = Decimal(str(cambio_raw)) if cambio_raw else Decimal("1")
                simbolo = moeda_simbolo.get(moeda, f"{moeda} ")
                if moeda == "USD":
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f}")
                else:
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f} (cambio: {cambio:,.4f})")
            else:
                linhas.append(f"Pago: ${total_pago:,.2f}")

        if diferenca != 0:
            sinal = "+" if diferenca > 0 else ""
            linhas.append(f"Diferenca: {sinal}${diferenca:,.2f}")
        if pessoa:
            linhas.append(f"Pessoa: {pessoa}")
        if observacoes:
            linhas.append(f"Obs: {observacoes[:60]}")

        if tipo == "COMPRA":
            total_compra_g += peso
            total_compra_usd += total_usd_val
        elif tipo in ("VENDA", "CAMBIO"):
            total_venda_g += peso
            total_venda_usd += total_usd_val

    linhas.append("====================")
    linhas.append("RESUMO:")
    if not statement_transactions:
        linhas.append("Nenhuma operação encontrada.")
    else:
        if total_compra_g > 0:
            n_c = sum(1 for x in statement_transactions if str(x.get("tipo_operacao") or "").upper() == "COMPRA")
            linhas.append(f"Compras: {n_c} op | {total_compra_g:,.3f} g | ${total_compra_usd:,.2f}")
        if total_venda_g > 0:
            n_v = sum(1 for x in statement_transactions if str(x.get("tipo_operacao") or "").upper() in ("VENDA", "CAMBIO"))
            linhas.append(f"Vendas:  {n_v} op | {total_venda_g:,.3f} g | ${total_venda_usd:,.2f}")
        saldo_g = total_compra_g - total_venda_g
        sinal_g = "+" if saldo_g >= 0 else ""
        linhas.append(f"Saldo ouro: {sinal_g}{saldo_g:,.3f} g")
    linhas.append("====================")

    return {
        "mensagem": "\n".join(linhas),
        "dados": {
            "intencao": "extrato",
            "periodo": label_periodo,
            "total_operacoes": len(statement_transactions),
        },
    }


def _handle_menu_option(remetente: str, mensagem: str, db: DatabaseClient) -> Optional[Dict[str, Any]]:
    option = _normalize_text(mensagem)
    if option not in {"1", "2", "3", "4", "5"}:
        return {
            "mensagem": (
                "Opção inválida. Escolha um número de 1 a 5.\n\n"
                f"{_build_whatsapp_checklist_menu()}"
            ),
            "dados": {"etapa": "await_menu_option"},
        }

    if option == "1":
        _save_session(
            db,
            remetente,
            "await_menu_tipo_operacao",
            {"source": "menu", "source_message_id": None},
        )
        return {
            "mensagem": (
                "Registrar operação.\n"
                "Informe o tipo: compra ou venda."
            ),
            "dados": {"acao": "registrar_operacao"},
        }

    if option == "2":
        response = _build_caixa_response(db)
        _save_session(db, remetente, "await_caixa_detalhe", {"source": "menu_caixa"})
        return response

    if option == "3":
        _clear_session(db, remetente)
        _save_session(db, remetente, "await_extrato_periodo", {})
        return {
            "mensagem": (
                "EXTRATO OPERACIONAL - selecione o periodo de consulta:\n"
                "1) Hoje\n"
                "2) Esta semana\n"
                "3) Informar intervalo de datas"
            ),
            "dados": {"etapa": "await_extrato_periodo"},
        }

    if option == "4":
        _clear_session(db, remetente)
        return {
            "mensagem": (
                "Editar operação.\n"
                "Formato: editar ID campo valor\n\n"
                "Campos: preço | quantidade | moeda | valor_moeda | câmbio\n"
                "Exemplos:\n"
                "- editar 123 preco 110\n"
                "- editar 123 quantidade 2.5"
            ),
            "dados": {"acao": "editar_operacao"},
        }

    # option == "5"
    _clear_session(db, remetente)
    return {
        "mensagem": (
            "Cancelar operação.\n"
        ),
        "dados": {"acao": "cancelar_operacao"},
    }


def _save_session(db: DatabaseClient, remetente: str, estado: str, contexto: Dict[str, Any]) -> None:
    atualizado_em = datetime.now(timezone.utc).isoformat()
    _SESSION_CACHE[remetente] = {"estado": estado, "contexto": contexto, "atualizado_em": atualizado_em}
    db.save_conversation_session(remetente=remetente, estado=estado, contexto=contexto)


def _get_session(db: DatabaseClient, remetente: str) -> Optional[Dict[str, Any]]:
    cached = _SESSION_CACHE.get(remetente)
    if cached:
        return cached
    db_session = db.get_conversation_session(remetente)
    if db_session and isinstance(db_session.get("contexto"), dict):
        session: Dict[str, Any] = {
            "estado": db_session.get("estado", ""),
            "contexto": cast(Dict[str, Any], db_session["contexto"]),
            "atualizado_em": db_session.get("atualizado_em"),
        }
        _SESSION_CACHE[remetente] = session
        return session
    return None


def _guided_session_idle_minutes(session: Dict[str, Any]) -> Optional[int]:
    updated_raw = session.get("atualizado_em")
    if not updated_raw:
        return None
    try:
        updated_dt = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - updated_dt.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds() // 60))


def _is_guided_session_stale(session: Dict[str, Any]) -> bool:
    idle = _guided_session_idle_minutes(session)
    if idle is None:
        return False
    return idle >= _GUIDED_SESSION_IDLE_MINUTES


def _clear_session(db: DatabaseClient, remetente: str) -> None:
    _SESSION_CACHE.pop(remetente, None)
    db.clear_conversation_session(remetente)


def _start_guided_flow_if_requested(
    remetente: str,
    mensagem: str,
    db: DatabaseClient,
    provider_message_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    text = _normalize_text(mensagem)
    if any(token in text for token in {"compra", "comprei", "comprar", "buy", "bought"}):
        tipo = "compra"
    elif any(token in text for token in {"venda", "vendi", "vender", "sell", "sold"}):
        tipo = "venda"
    else:
        return None

    contexto: Dict[str, Any] = {
        "tipo_operacao": tipo,
        "pagamentos": [],
        "moedas": [],
        "moeda_index": 0,
        "moeda_atual": None,
        "source_message_id": provider_message_id,
    }
    _save_session(db, remetente, "await_origem", contexto)
    return {
        "mensagem": (
            f"Iniciando registro de {tipo}.\n"
            "Local da operação:\n"
            "1) balcão\n"
            "2) fora"
            f"{_navigation_hint()}"
        ),
        "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
    }


def _format_resumo(contexto: Dict[str, Any]) -> str:
    """Format operation summary WITHOUT USD as single reference.
    
    New structure: show pagamentos in each currency independently.
    Each caixa is updated with its own value, no conversion.
    """
    pagamentos = contexto.get("pagamentos", [])
    linhas_pagamento: List[str] = []
    for p in pagamentos:
        moeda = p.get('moeda', 'USD')
        valor = p.get('valor_moeda', '0')
        linhas_pagamento.append(f"- {moeda}: {valor}")
    
    linhas_pagamento_texto = "\n".join(linhas_pagamento) if linhas_pagamento else "- Sem pagamentos informados"

    tipo_operacao = str(contexto.get("tipo_operacao") or "")
    pessoa_label = "Vendedor" if tipo_operacao == "compra" else "Comprador"
    lucro_real_usd = contexto.get("lucro_real_usd")
    custo_fifo_usd = contexto.get("custo_fifo_usd")
    lucro_ref_usd = contexto.get("lucro_ref_usd")
    preco_compra_ref_usd = contexto.get("preco_compra_ref_usd")
    lucro_linha = ""
    observacoes_idx = "10"

    if tipo_operacao == "venda" and lucro_real_usd is not None:
        lucro_linha = f"10) Lucro real (FIFO): USD {lucro_real_usd} (custo: USD {custo_fifo_usd})\n"
        observacoes_idx = "11"
    elif tipo_operacao == "venda" and lucro_ref_usd is not None:
        lucro_linha = f"10) Lucro ref.: USD {lucro_ref_usd} (custo-base: USD {preco_compra_ref_usd}/g)\n"
        observacoes_idx = "11"

    if tipo_operacao == "compra":
        return (
            "📋 RESUMO FINAL - COMPRA\n"
            f"1) Tipo: {contexto.get('tipo_operacao')}\n"
            f"2) Origem: {contexto.get('origem')}\n"
            f"3) Teor: {contexto.get('teor')}%\n"
            f"4) Peso: {contexto.get('peso')}g\n"
            f"5) Preço base: {contexto.get('preco_moeda')} {contexto.get('preco_moeda')} / g\n"
            f"6) {pessoa_label}: {contexto.get('pessoa')}\n"
            f"7) Forma de pagamento: {contexto.get('forma_pagamento')}\n"
            f"8) Pagamentos por moeda:\n{linhas_pagamento_texto}\n"
            f"9) Observações: {contexto.get('observacoes') or '(nenhuma)'}\n"
            "════════════════════════════════\n"
            "Para confirmar o registro, responda: sim\n"
            "Para cancelar a operacao, responda: nao"
        )

    return (
        "📋 RESUMO FINAL - VENDA\n"
        f"1) Tipo: {contexto.get('tipo_operacao')}\n"
        f"2) Origem: {contexto.get('origem')}\n"
        f"3) Teor: {contexto.get('teor')}%\n"
        f"4) Peso: {contexto.get('peso')}g\n"
        f"5) Fechamento: {contexto.get('fechamento_gramas')}g ({contexto.get('fechamento_tipo')})\n"
        f"6) Preço base: {contexto.get('preco_moeda')} / g\n"
        f"7) {pessoa_label}: {contexto.get('pessoa')}\n"
        f"8) Forma de pagamento: {contexto.get('forma_pagamento')}\n"
        f"9) Pagamentos por moeda:\n{linhas_pagamento_texto}\n"
        f"{lucro_linha}"
        f"{observacoes_idx}) Observações: {contexto.get('observacoes') or '(nenhuma)'}\n"
        "════════════════════════════════\n"
        "Para confirmar o registro, responda: sim\n"
        "Para cancelar a operacao, responda: nao"
    )


def _build_day_range(date_str: Optional[str]) -> Dict[str, str]:
    # Use TZ_OFFSET_HOURS to convert UTC "now" to local date (default: Brazil UTC-3)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    if date_str:
        try:
            base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Data invalida. Use: AAAA-MM-DD") from exc
    else:
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now + timedelta(hours=tz_offset_hours)
        base_date = local_now.date()

    start_dt = datetime(base_date.year, base_date.month, base_date.day, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "date": str(base_date)}


def _build_week_range() -> Dict[str, str]:
    """ISO range from Monday of the current week to end of today (inclusive)."""
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    utc_now = datetime.now(timezone.utc)
    local_now = utc_now + timedelta(hours=tz_offset_hours)
    today = local_now.date()
    monday = today - timedelta(days=today.weekday())
    start_dt = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    end_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=1)
    return {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "label": f"{monday.isoformat()} a {today.isoformat()}",
    }


def _parse_date_user_input(text: str) -> Optional[str]:
    """Accept DD/MM/AAAA, DD/MM/AA, DD-MM-AAAA, or AAAA-MM-DD and return YYYY-MM-DD."""
    import re as _re
    s = text.strip()
    m = _re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", s)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year_raw = m.group(3)
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            from datetime import date as _date
            year = _date.today().year
        try:
            from datetime import date as _date
            _date(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            return None
    m2 = _re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m2:
        return s
    return None


def _build_custom_range(start: str, end: str) -> Dict[str, str]:
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data/hora invalida. Use formato ISO.") from exc

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="A data final deve ser maior que a inicial.")

    return {
        "start": start_dt.astimezone(timezone.utc).isoformat(),
        "end": end_dt.astimezone(timezone.utc).isoformat(),
    }


def _should_trigger_multi_agent_review(transaction: Dict[str, Any], force: bool = False) -> bool:
    if not _MULTI_AGENT_AUTO_ENABLED:
        return False
    if force:
        return True

    total_usd = Decimal(str(transaction.get("total_usd", transaction.get("valor_total", 0)) or 0))
    total_pago_usd = Decimal(str(transaction.get("total_pago_usd", total_usd) or total_usd))
    peso = Decimal(str(transaction.get("peso", transaction.get("quantidade", 0)) or 0))
    diferenca = abs(money(total_usd - total_pago_usd))
    tipo_operacao = str(transaction.get("tipo_operacao", "")).lower()

    return any(
        [
            diferenca >= _RISK_DIFF_LIMIT_USD,
            total_usd >= _MULTI_AGENT_AUTO_MIN_USD,
            peso >= _MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS,
            tipo_operacao in {"venda", "cambio"},
        ]
    )


def _run_automatic_multi_agent_review(
    db: DatabaseClient,
    *,
    objective: str,
    transaction: Dict[str, Any],
    operation_id: Optional[int],
    operation_kind: str,
    source_message_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    try:
        request = MultiAgentRequest(
            objective=objective,
            operation=transaction.get("tipo_operacao"),
            operation_id=operation_id,
            operation_kind=operation_kind,
            source_message_id=source_message_id,
            transaction=transaction,
            live_context=db.build_multi_agent_live_context(
                operation_id=operation_id if operation_kind == "gold_transaction" else None
            ),
            constraints={"trigger": "automatic_review"},
            rounds=2,
        )
        response = run_multi_agent_orchestration(request)
        persisted = db.save_multi_agent_run(
            objective=request.objective,
            operation_id=operation_id,
            operation_kind=operation_kind,
            source_message_id=source_message_id,
            request_payload=request.model_dump(mode="json"),
            response_payload=response.model_dump(mode="json"),
        )
        return {
            "run_id": persisted.get("id") if isinstance(persisted, dict) else None,
            "summary": response.summary,
            "decisions": response.decisions,
            "risks": response.risks,
            "recommendations": response.recommendations,
        }
    except Exception as exc:
        logger.exception("Falha na analise multiagente automatica")
        db.insert_log(
            nivel="warning",
            mensagem_recebida="AUTO_MULTI_AGENT_REVIEW_FAILED",
            contexto={
                "objective": objective,
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "transaction": transaction,
            },
            erro=str(exc),
        )
        return None


def _advance_after_payment_exchange(
    db: DatabaseClient,
    remetente: str,
    contexto: Dict[str, Any],
    pagamentos: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Advance the guided flow after a payment entry has been fully populated (amount + exchange rate)."""
    moedas = list(contexto.get("moedas", []))
    idx = int(contexto.get("moeda_index", 0)) + 1
    total_operacao = Decimal(str(contexto.get("total_usd", "0")))
    total_pago_parcial = sum((Decimal(str(p["valor_usd"])) for p in pagamentos), Decimal("0"))

    # Se ainda não temos total em USD (precificação em moeda não-USD sem câmbio-base),
    # avançamos sem calcular restante e pedimos o câmbio-base no final.
    if total_operacao <= 0:
        if idx < len(moedas):
            contexto["moeda_index"] = idx
            contexto["moeda_atual"] = moedas[idx]
            proxima_moeda = str(moedas[idx]).upper()
            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if proxima_moeda != preco_moeda:
                _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
                cambio_prompt = _build_pair_cambio_prompt(preco_moeda, proxima_moeda)
                return {
                    "mensagem": (
                        "Pagamento registrado.\n"
                        f"Câmbio {preco_moeda}/{proxima_moeda}: {cambio_prompt}"
                    ),
                    "dados": {"etapa": "await_cambio_moeda_pre_valor"},
                }

            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {
                "mensagem": (
                    "Pagamento registrado.\n"
                    "Ainda falta o câmbio da moeda-base para calcular o total em USD.\n"
                    f"Valor em {moedas[idx]}?"
                ),
                "dados": {"etapa": "await_valor_moeda"},
            }

        _save_session(db, remetente, "await_cambio_base_para_total", contexto)
        moeda_preco = str(contexto.get("preco_moeda", "EUR")).upper()
        return {
            "mensagem": (
                "Para fechar o total da operação em USD, informe o câmbio da moeda-base.\n"
                f"{_build_cambio_prompt(moeda_preco)}"
            ),
            "dados": {"etapa": "await_cambio_base_para_total"},
        }

    restante = money(total_operacao - total_pago_parcial)

    if idx < len(moedas):
        contexto["moeda_index"] = idx
        contexto["moeda_atual"] = moedas[idx]
        proxima_moeda = str(moedas[idx]).upper()
        preco_moeda_adv = str(contexto.get("preco_moeda", "USD")).upper()
        if proxima_moeda != preco_moeda_adv:
            _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
            cambio_prompt = _build_pair_cambio_prompt(preco_moeda_adv, proxima_moeda)
            return {
                "mensagem": (
                    f"Pago até agora: {money(total_pago_parcial)} USD. Restante: {restante} USD.\n"
                    f"Câmbio {preco_moeda_adv}/{proxima_moeda}: {cambio_prompt}"
                ),
                "dados": {"etapa": "await_cambio_moeda_pre_valor"},
            }

        _save_session(db, remetente, "await_valor_moeda", contexto)
        return {
            "mensagem": (
                f"Pago até agora: {money(total_pago_parcial)} USD. Restante: {restante} USD.\n"
                f"Valor em {moedas[idx]}?"
            ),
            "dados": {"etapa": "await_valor_moeda"},
        }

    total_pago = sum((Decimal(str(p["valor_usd"])) for p in pagamentos), Decimal("0"))
    contexto["total_pago_usd"] = str(money(total_pago))
    tipo_operacao_ctx = str(contexto.get("tipo_operacao", "compra"))
    fx_notice = "\nObs: referência em USD estimada (sem câmbio explícito informado)." if contexto.get("fx_auto_assumido") else ""

    # Determine display currency: use preco_moeda when all payments are in that currency.
    preco_moeda_disp = str(contexto.get("preco_moeda", "USD")).upper()
    total_moeda_disp = Decimal(str(contexto.get("total_moeda", "0")))
    all_in_preco_moeda = (
        preco_moeda_disp != "USD"
        and total_moeda_disp > 0
        and all(str(p.get("moeda", "")).upper() == preco_moeda_disp for p in pagamentos)
    )
    if all_in_preco_moeda:
        display_pago = sum((Decimal(str(p["valor_moeda"])) for p in pagamentos), Decimal("0"))
        display_diferenca = total_moeda_disp - display_pago
        display_moeda = preco_moeda_disp
    else:
        display_pago = total_pago
        display_diferenca = total_operacao - total_pago
        display_moeda = "USD"

    if tipo_operacao_ctx == "compra":
        peso_ctx = Decimal(str(contexto.get("peso", "0")))
        contexto["fechamento_gramas"] = str(money(peso_ctx))
        contexto["fechamento_tipo"] = "total"
        _save_session(db, remetente, "await_pessoa", contexto)
        return {
            "mensagem": (
                f"Total pago: {money(display_pago)} {display_moeda}.\n"
                f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                f"Nome do vendedor (de quem você comprou)?{fx_notice}"
            ),
            "dados": {"etapa": "await_pessoa"},
        }

    peso_ctx = Decimal(str(contexto.get("peso", "0")))
    if money(display_diferenca) == Decimal("0.00") and peso_ctx > 0:
        contexto["fechamento_gramas"] = str(money(peso_ctx))
        contexto["fechamento_tipo"] = "total"
        _save_session(db, remetente, "await_pessoa", contexto)
        return {
            "mensagem": (
                f"Total pago: {money(display_pago)} {display_moeda}.\n"
                f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                f"Venda fechada integralmente.\n"
                f"Nome do comprador?{fx_notice}"
            ),
            "dados": {"etapa": "await_pessoa"},
        }

    _save_session(db, remetente, "await_fechamento_gramas", contexto)
    return {
        "mensagem": (
            f"Total pago: {money(display_pago)} {display_moeda}.\n"
            f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
            f"Informe as gramas fechadas.{fx_notice}"
        ),
        "dados": {"etapa": "await_fechamento_gramas"},
    }


def _process_guided_flow(remetente: str, mensagem: str, db: DatabaseClient, session: Dict[str, Any]) -> Dict[str, Any]:
    estado = str(session.get("estado", ""))
    contexto = dict(session.get("contexto", {}))
    text = _normalize_text(mensagem)

    cancelable_states = _GUIDED_FLOW_STATES - {"await_menu_option", "await_menu_tipo_operacao", "await_nome_usuario"}

    if estado in cancelable_states and text in {"cancelar", "cancela", "cancel", "parar", "sair"}:
        _clear_session(db, remetente)
        return {
            "mensagem": "Certo, parei por aqui. Quando quiser retomar, me diga compra, venda ou descreva a operacao do seu jeito.",
            "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
        }

    if estado == "await_menu_option":
        menu_result = _handle_menu_option(remetente, mensagem, db)
        if menu_result is not None:
            return menu_result

    back_result = _guided_try_back_command(remetente, mensagem, estado, contexto, db)
    if back_result is not None and estado in _GUIDED_FLOW_STATES:
        return back_result

    if estado == "await_resume_confirmacao":
        if text in {"continuar", "retomar", "sim", "s"}:
            estado_anterior = str(contexto.get("estado_anterior", ""))
            contexto_anterior = dict(contexto.get("contexto_anterior", {}))
            if not estado_anterior or estado_anterior not in _GUIDED_FLOW_STATES:
                _clear_session(db, remetente)
                return {
                    "mensagem": "Sessão anterior expirada. Envie 'compra' ou 'venda' para iniciar novamente.",
                    "dados": {"acao": "sessao_expirada"},
                }

            _save_session(db, remetente, estado_anterior, contexto_anterior)
            if estado_anterior == "await_confirmacao":
                resumo = _format_resumo(contexto_anterior)
                return {
                    "mensagem": f"Retomando de onde parou.\n{resumo}",
                    "dados": {"etapa": estado_anterior, "acao": "retomar_fluxo"},
                }

            prompt = _guided_prompt_for_state(estado_anterior, contexto_anterior)
            return {
                "mensagem": f"Retomando de onde parou.\n{prompt}",
                "dados": {"etapa": estado_anterior, "acao": "retomar_fluxo"},
            }

        if text in {"cancelar", "cancela", "cancel", "nao", "não", "n", "parar", "sair"}:
            _clear_session(db, remetente)
            return {
                "mensagem": "Tudo certo, cancelei por aqui. Quando quiser voltar, me diga compra, venda ou escreva a operacao normalmente.",
                "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
            }

        return {
            "mensagem": "Quer continuar de onde parou ou prefere cancelar? Pode responder: continuar ou cancelar.",
            "dados": {"etapa": "await_resume_confirmacao"},
        }

    if estado == "await_nome_usuario":
        nome = _sanitize_nome(mensagem)
        if len(nome) < 2:
            return {
                "mensagem": "Nome inválido. Digite um nome com pelo menos 2 letras.",
                "dados": {"etapa": "await_nome_usuario"},
            }

        db.update_usuario_nome(remetente, nome)
        _clear_session(db, remetente)
        return {
            "mensagem": (
                f"Perfeito, {nome}. Seu cadastro ficou completo.\n"
                "Se quiser, posso te mostrar as opcoes. Basta enviar: menu."
            ),
            "dados": {"acao": "cadastro_nome", "nome": nome},
        }

    if estado == "await_menu_tipo_operacao":
        tipo_escolhido = {"1": "compra", "2": "venda"}.get(text, text)
        if tipo_escolhido not in {"compra", "venda"}:
            return {
                "mensagem": (
                    "Nao consegui identificar se voce quer compra ou venda.\n"
                    "Responda com uma destas opcoes:\n"
                    "1) compra\n"
                    "2) venda"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": "await_menu_tipo_operacao"},
            }

        contexto.update(
            {
                "tipo_operacao": tipo_escolhido,
                "pagamentos": [],
                "moedas": [],
                "moeda_index": 0,
                "moeda_atual": None,
            }
        )
        _save_session(db, remetente, "await_origem", contexto)
        return {
            "mensagem": (
                f"Operação: {tipo_escolhido}.\n"
                "Local da operação:\n"
                "1) balcão\n"
                "2) fora"
                f"{_navigation_hint()}"
            ),
            "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
        }

    if estado == "await_origem":
        origem = _parse_origem_choice(mensagem)
        if origem is None:
            return {
                "mensagem": (
                    "Origem inválida. Escolha uma opção:\n"
                    "1) balcão\n"
                    "2) fora"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["origem"] = origem
        _save_session(db, remetente, "await_teor", contexto)
        return {"mensagem": "Qual o teor do ouro em %? (0 a 99,99)", "dados": {"etapa": "await_teor"}}

    if estado == "await_teor":
        teor = _parse_decimal_from_text(mensagem, "teor")
        if teor < 0 or teor > Decimal("99.99"):
            return {"mensagem": "O teor deve estar entre 0 e 99,99.", "dados": {"etapa": estado}}
        contexto["teor"] = str(money(teor))
        _save_session(db, remetente, "await_peso", contexto)
        return {"mensagem": "Quantas gramas?", "dados": {"etapa": "await_peso"}}

    if estado == "await_peso":
        peso = _parse_decimal_from_text(mensagem, "peso")
        if peso <= 0:
            return {"mensagem": "O peso deve ser maior que zero.", "dados": {"etapa": estado}}
        contexto["peso"] = str(peso)
        _save_session(db, remetente, "await_preco_moeda", contexto)
        return {
            "mensagem": (
                "Moeda base para precificação:\n"
                "1) USD\n"
                "2) EUR\n"
                "3) SRD\n"
                "4) BRL\n"
                "Você também pode digitar: dólar, euro, srd ou real."
                f"{_navigation_hint()}"
            ),
            "dados": {"etapa": "await_preco_moeda"},
        }

    if estado == "await_preco_moeda":
        moeda_preco = _parse_single_currency_choice(mensagem)
        if moeda_preco not in _MOEDAS_SUPORTADAS:
            return {
                "mensagem": (
                    "Moeda inválida. Escolha uma opção:\n"
                    "1) USD\n"
                    "2) EUR\n"
                    "3) SRD\n"
                    "4) BRL\n"
                    "Você também pode digitar: dólar, euro, srd ou real."
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["preco_moeda"] = moeda_preco
        _save_session(db, remetente, "await_preco_usd", contexto)
        return {
            "mensagem": f"Informe o preço por grama em {moeda_preco}.",
            "dados": {"etapa": "await_preco_usd"},
        }

    if estado == "await_preco_usd":
        preco = _parse_decimal_from_text(mensagem, "preco_usd")
        if preco <= 0:
            return {"mensagem": "Preço deve ser maior que zero.", "dados": {"etapa": estado}}

        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda != "USD":
            contexto["preco_moeda_valor"] = str(money(preco))
            peso = Decimal(str(contexto.get("peso")))
            total_moeda = money(peso * preco)
            contexto["total_moeda"] = str(total_moeda)
            _save_session(db, remetente, "await_moedas", contexto)
            return {
                "mensagem": (
                    f"Preco recebido: {money(preco)} {preco_moeda}/g.\n"
                    f"Total da operação: {total_moeda} {preco_moeda}.\n"
                    "Informe as moedas de pagamento: USD, EUR, SRD, BRL\n"
                    "(o câmbio será pedido na etapa de pagamento, se necessário)"
                ),
                "dados": {"etapa": "await_moedas"},
            }

        peso = Decimal(str(contexto.get("peso")))
        total = money(peso * preco)
        contexto["preco_usd"] = str(money(preco))
        contexto["total_usd"] = str(total)
        _save_session(db, remetente, "await_moedas", contexto)
        return {
            "mensagem": (
                f"{peso}g x {money(preco)} USD/g = {total} USD.\n"
                "Informe as moedas de pagamento: USD, EUR, SRD, BRL"
            ),
            "dados": {"etapa": "await_moedas"},
        }

    if estado == "await_preco_cambio":
        cambio = _parse_decimal_from_text(mensagem, "cambio_preco")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        cambio_normalizado = _normalize_cambio_para_usd(preco_moeda, cambio)
        preco_moeda_valor = Decimal(str(contexto.get("preco_moeda_valor", "0")))
        preco_usd = money(preco_moeda_valor / cambio_normalizado)
        peso = Decimal(str(contexto.get("peso")))
        total = money(peso * preco_usd)

        contexto["preco_usd"] = str(preco_usd)
        contexto["cambio_preco_moeda"] = str(cambio_normalizado)
        contexto["total_usd"] = str(total)
        _save_session(db, remetente, "await_moedas", contexto)
        return {
            "mensagem": (
                f"Conversão feita: {preco_usd} USD/g.\n"
                f"Total da operação: {total} USD.\n"
                "Informe as moedas de pagamento: USD, EUR, SRD, BRL"
            ),
            "dados": {"etapa": "await_moedas"},
        }

    if estado == "await_moedas":
        moedas = _extract_moedas(mensagem)
        if not moedas:
            return {"mensagem": "Não entendi as moedas. Exemplo: USD e SRD", "dados": {"etapa": estado}}
        contexto["moedas"] = moedas
        contexto["moeda_index"] = 0
        contexto["pagamentos"] = []
        contexto["moeda_atual"] = moedas[0]
        _save_session(db, remetente, "await_valor_moeda", contexto)
        total_operacao = Decimal(str(contexto.get("total_usd", "0")))
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        total_moeda = Decimal(str(contexto.get("total_moeda", "0")))

        if total_operacao > 0:
            total_txt = f"Total da operação: {money(total_operacao)} USD."
        elif preco_moeda != "USD" and total_moeda > 0:
            total_txt = f"Total da operação: {money(total_moeda)} {preco_moeda}."
        else:
            total_txt = "Total da operação definido."

        primeira_moeda = str(moedas[0]).upper()
        if primeira_moeda != preco_moeda:
            _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
            cambio_prompt = _build_pair_cambio_prompt(preco_moeda, primeira_moeda)
            return {
                "mensagem": (
                    f"{total_txt}\n"
                    f"Câmbio {preco_moeda}/{primeira_moeda}: {cambio_prompt}"
                ),
                "dados": {"etapa": "await_cambio_moeda_pre_valor"},
            }

        return {
            "mensagem": (
                f"{total_txt}\n"
                f"Quanto será pago em {moedas[0]}?"
            ),
            "dados": {"etapa": "await_valor_moeda"},
        }

    if estado == "await_cambio_moeda_pre_valor":
        cambio = _parse_decimal_from_text(mensagem, "cambio_pre_valor")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

        moeda_atual = str(contexto.get("moeda_atual", "USD")).upper()
        preco_moeda_cp = str(contexto.get("preco_moeda", "USD")).upper()

        if moeda_atual == "USD" and preco_moeda_cp == "USD":
            # USD payment in USD operation: trivially no exchange needed.
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "Quanto será pago em USD?", "dados": {"etapa": "await_valor_moeda"}}

        if moeda_atual == "USD" and preco_moeda_cp != "USD":
            # Non-USD base, USD payment: prompt was "1 B = R USD".
            cambio_normalizado = _normalize_cambio_para_usd(preco_moeda_cp, cambio)
            _try_set_total_usd_from_base_rate(contexto, cambio_normalizado)
            total_usd_novo = Decimal(str(contexto.get("total_usd", "0")))
            total_moeda_cp = Decimal(str(contexto.get("total_moeda", "0")))
            lines = [f"Câmbio: 1 {preco_moeda_cp} = {money(cambio)} USD."]
            if total_usd_novo > 0:
                lines.append(f"Total equivalente: ~{money(total_usd_novo)} USD.")
            elif total_moeda_cp > 0:
                lines.append(f"Total da operação: {money(total_moeda_cp)} {preco_moeda_cp}.")
            lines.append("Quanto será pago em USD?")
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "\n".join(lines), "dados": {"etapa": "await_valor_moeda"}}

        if preco_moeda_cp != "USD" and moeda_atual != "USD":
            # Both non-USD (e.g. EUR base + SRD pay): prompt was the direct B/P pair.
            pay_per_usd, pair_p_per_b, c_base = _pair_rate_to_payment_per_usd(
                preco_moeda_cp, moeda_atual, cambio, db
            )
            total_moeda_base = Decimal(str(contexto.get("total_moeda", "0")))
            total_in_payment = money(total_moeda_base * pair_p_per_b) if total_moeda_base > 0 else None
            if _MOEDA_STRENGTH.get(preco_moeda_cp, 5) <= _MOEDA_STRENGTH.get(moeda_atual, 5):
                rate_echo = f"1 {preco_moeda_cp} = {money(pair_p_per_b)} {moeda_atual}"
            else:
                inv = fx_rate(Decimal("1") / pair_p_per_b) if pair_p_per_b > 0 else Decimal("0")
                rate_echo = f"1 {moeda_atual} = {money(inv)} {preco_moeda_cp}"
            lines = [f"Câmbio: {rate_echo}."]
            if total_in_payment and total_in_payment > 0:
                lines.append(f"Total estimado: {money(total_in_payment)} {moeda_atual}.")
            lines.append(f"Quanto será pago em {moeda_atual}?")
            if pay_per_usd is not None:
                contexto["cambio_moeda_atual_pre"] = str(pay_per_usd)
                contexto["fx_auto_assumido"] = True
            else:
                contexto.pop("cambio_moeda_atual_pre", None)
                contexto["fx_auto_assumido"] = True
            if c_base is not None:
                _try_set_total_usd_from_base_rate(contexto, c_base)
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "\n".join(lines), "dados": {"etapa": "await_valor_moeda"}}

        # USD base, non-USD payment: normalize to P_per_USD.
        cambio_normalizado = _normalize_cambio_para_usd(moeda_atual, cambio)
        contexto["cambio_moeda_atual_pre"] = str(cambio_normalizado)
        _save_session(db, remetente, "await_valor_moeda", contexto)
        return {
            "mensagem": f"Câmbio registrado. Quanto será pago em {moeda_atual}?",
            "dados": {"etapa": "await_valor_moeda"},
        }

    if estado == "await_valor_moeda":
        moeda_atual = str(contexto.get("moeda_atual"))
        valor_moeda = _parse_decimal_from_text(mensagem, "valor_moeda")
        if valor_moeda < 0:
            return {"mensagem": "Valor da moeda não pode ser negativo.", "dados": {"etapa": estado}}
        pagamento: Dict[str, Any] = {
            "moeda": moeda_atual,
            "valor_moeda": str(money(valor_moeda)),
            "cambio_para_usd": "1",
            "valor_usd": str(money(valor_moeda)),
            "forma_pagamento": None,
        }
        pagamentos = list(contexto.get("pagamentos", []))
        pagamentos.append(pagamento)
        contexto["pagamentos"] = pagamentos

        if moeda_atual == "USD":
            contexto.pop("cambio_moeda_atual_pre", None)
            return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        cambio_pre = contexto.get("cambio_moeda_atual_pre")
        if cambio_pre:
            cambio_pre_dec = Decimal(str(cambio_pre))
            valor_usd_pre = money(valor_moeda / cambio_pre_dec)
            pagamentos[-1]["cambio_para_usd"] = str(cambio_pre_dec)
            pagamentos[-1]["valor_usd"] = str(valor_usd_pre)
            contexto["pagamentos"] = pagamentos
            contexto.pop("cambio_moeda_atual_pre", None)

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if preco_moeda != "USD" and str(moeda_atual).upper() == preco_moeda:
                _try_set_total_usd_from_base_rate(contexto, cambio_pre_dec)

            return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        # Se for a mesma moeda-base da precificação, tenta usar último câmbio conhecido
        # para evitar pedir câmbio manual em operações diretas nessa moeda.
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda != "USD" and str(moeda_atual).upper() == preco_moeda:
            cambio_auto = db.get_last_cambio_para_usd(preco_moeda)
            cambio_auto_dec = Decimal(str(cambio_auto)) if (cambio_auto and cambio_auto > 0) else Decimal("1")
            # Paying in the same currency as the price: no FX assumption — cambio 1:1 is exact,
            # not an estimate. Only flag fx_auto_assumido for cross-currency fallbacks.
            contexto["fx_auto_assumido"] = False
            valor_usd_auto = money(valor_moeda / cambio_auto_dec)
            pagamentos[-1]["cambio_para_usd"] = str(cambio_auto_dec)
            pagamentos[-1]["valor_usd"] = str(valor_usd_auto)
            contexto["pagamentos"] = pagamentos
            _try_set_total_usd_from_base_rate(contexto, cambio_auto_dec)
            return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        # Câmbio de moeda não-USD sempre é pedido na etapa de pagamento.
        total_operacao = Decimal(str(contexto.get("total_usd", "0")))
        _save_session(db, remetente, "await_cambio_moeda", contexto)
        total_linha = f"Total da operação: {money(total_operacao)} USD.\n" if total_operacao > 0 else ""
        return {
            "mensagem": (
                f"{moeda_atual}: {money(valor_moeda)} registrado.\n"
                f"{total_linha}"
                f"Câmbio do {moeda_atual}: {_build_cambio_prompt(moeda_atual)}"
            ),
            "dados": {"etapa": "await_cambio_moeda"},
        }

    if estado == "await_cambio_moeda":
        cambio = _parse_decimal_from_text(mensagem, "cambio")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}
        pagamentos = list(contexto.get("pagamentos", []))
        if not pagamentos:
            _save_session(db, remetente, "await_moedas", contexto)
            return {"mensagem": "Pagamentos reiniciados. Informe as moedas novamente.", "dados": {"etapa": "await_moedas"}}

        ultimo = dict(pagamentos[-1])
        moeda_ult = str(ultimo.get("moeda", "USD")).upper()
        cambio_normalizado = _normalize_cambio_para_usd(moeda_ult, cambio)
        valor_moeda_ult = Decimal(str(ultimo["valor_moeda"]))
        valor_usd = money(valor_moeda_ult / cambio_normalizado)
        ultimo["cambio_para_usd"] = str(cambio_normalizado)
        ultimo["valor_usd"] = str(valor_usd)
        pagamentos[-1] = ultimo
        contexto["pagamentos"] = pagamentos

        # Se esta moeda for a base da precificação, usamos o câmbio para fechar total em USD automaticamente.
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda != "USD" and moeda_ult == preco_moeda:
            _try_set_total_usd_from_base_rate(contexto, cambio_normalizado)

        return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

    if estado == "await_cambio_base_para_total":
        cambio = _parse_decimal_from_text(mensagem, "cambio_base_total")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        cambio_normalizado = _normalize_cambio_para_usd(preco_moeda, cambio)
        if not _try_set_total_usd_from_base_rate(contexto, cambio_normalizado):
            _clear_session(db, remetente)
            return {
                "mensagem": "Não consegui retomar os dados da operação. Envie compra ou venda para reiniciar.",
                "dados": {"acao": "reiniciar"},
            }

        pagamentos = list(contexto.get("pagamentos", []))
        return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

    if estado == "await_fechamento_gramas":
        fechamento = _parse_decimal_from_text(mensagem, "fechamento_gramas")
        if fechamento < 0:
            return {"mensagem": "Fechamento em gramas não pode ser negativo.", "dados": {"etapa": estado}}
        contexto["fechamento_gramas"] = str(money(fechamento))
        _save_session(db, remetente, "await_fechamento_tipo", contexto)
        return {"mensagem": "Fechamento total ou parcial?", "dados": {"etapa": "await_fechamento_tipo"}}

    if estado == "await_fechamento_tipo":
        fechamento_tipo = _parse_fechamento_tipo_choice(mensagem)
        if fechamento_tipo is None:
            return {
                "mensagem": (
                    "Escolha o tipo de fechamento:\n"
                    "1) total\n"
                    "2) parcial"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["fechamento_tipo"] = fechamento_tipo
        _save_session(db, remetente, "await_pessoa", contexto)
        tipo_op_ft = str(contexto.get("tipo_operacao", "compra"))
        pergunta_pessoa = "Nome do vendedor (de quem você comprou)?" if tipo_op_ft == "compra" else "Nome do comprador?"
        return {"mensagem": pergunta_pessoa, "dados": {"etapa": "await_pessoa"}}

    if estado == "await_pessoa":
        if len(mensagem.strip()) < 2:
            return {"mensagem": "Informe um nome válido.", "dados": {"etapa": estado}}
        contexto["pessoa"] = mensagem.strip()
        _save_session(db, remetente, "await_forma_pagamento", contexto)
        return {
            "mensagem": (
                "Como foi o pagamento?\n"
                "1) dinheiro\n"
                "2) transferência\n"
                "3) cheque\n"
                "4) misto"
                f"{_navigation_hint()}"
            ),
            "dados": {"etapa": "await_forma_pagamento"},
        }

    if estado == "await_forma_pagamento":
        forma = _parse_forma_pagamento_choice(mensagem)
        if forma is None:
            return {
                "mensagem": (
                    "Forma inválida. Escolha uma opção:\n"
                    "1) dinheiro\n"
                    "2) transferência\n"
                    "3) cheque\n"
                    "4) misto"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["forma_pagamento"] = forma
        pagamentos = list(contexto.get("pagamentos", []))
        for pagamento in pagamentos:
            pagamento["forma_pagamento"] = forma
        contexto["pagamentos"] = pagamentos
        _save_session(db, remetente, "await_observacoes", contexto)
        return {"mensagem": "Quer adicionar observações? (ou digite 'nenhuma')", "dados": {"etapa": "await_observacoes"}}

    if estado == "await_observacoes":
        contexto["observacoes"] = "" if _normalize_text(mensagem) in {"nenhuma", "nao", "não"} else mensagem.strip()
        _attach_sale_profit_reference(db, contexto)
        resumo = _format_resumo(contexto)
        _save_session(db, remetente, "await_confirmacao", contexto)
        return {"mensagem": resumo, "dados": {"etapa": "await_confirmacao", "preview": contexto}}

    if estado == "await_confirmacao":
        text_confirm = _normalize_text(mensagem)
        confirm: Optional[bool]
        if contexto.get("risk_override_pending") and text_confirm in {"autorizar risco", "autorizar", "override"}:
            contexto["risk_override_approved"] = True
            contexto.pop("risk_override_pending", None)
            _save_session(db, remetente, "await_confirmacao", contexto)
            confirm = True
        else:
            confirm = _extract_confirmacao(mensagem)
        if confirm is None:
            if contexto.get("risk_override_pending"):
                return {
                    "mensagem": "Responda: autorizar risco, não ou voltar.",
                    "dados": {"etapa": estado, "risk_override_pending": True},
                }
            return {"mensagem": "Digite apenas: sim ou não.", "dados": {"etapa": estado}}

        if not confirm:
            _clear_session(db, remetente)
            return {"mensagem": "Operação cancelada com sucesso.", "dados": {"intencao": "fluxo_guiado_cancelado"}}

        peso = Decimal(str(contexto.get("peso")))
        preco = Decimal(str(contexto.get("preco_usd")))
        total = money(peso * preco)
        tipo_operacao_confirm = str(contexto.get("tipo_operacao", "compra"))
        if tipo_operacao_confirm == "venda":
            _attach_sale_profit_reference(db, contexto)

        pagamentos = list(contexto.get("pagamentos", []))
        projected = _project_caixa_balances(db.get_saldo_caixa(), tipo_operacao_confirm, peso, pagamentos)
        negative_balances = _find_negative_caixa_balances(projected)
        fifo_shortfall = Decimal(str(contexto.get("fifo_shortfall_grams", "0")))
        risk_lines: List[str] = []
        if negative_balances:
            risk_lines.append("Saldos projetados negativos:")
            risk_lines.extend(_format_negative_caixa_lines(negative_balances))
        if fifo_shortfall > 0:
            risk_lines.append(f"- Estoque FIFO insuficiente: faltam {fifo_shortfall} g")

        if risk_lines and not contexto.get("risk_override_approved"):
            usuario_confirm = db.get_usuario_by_telefone(remetente) or {}
            is_admin_confirm = str(usuario_confirm.get("tipo_usuario", "")).lower() == "admin"
            contexto["risk_override_pending"] = True
            _save_session(db, remetente, "await_confirmacao", contexto)
            if is_admin_confirm:
                return {
                    "mensagem": "⛔ Bloqueio de risco.\n" + "\n".join(risk_lines) + "\nResponda: autorizar risco, não ou voltar.",
                    "dados": {"etapa": estado, "risk_override_pending": True, "risk_blocked": True},
                }
            return {
                "mensagem": "⛔ Bloqueio de risco.\n" + "\n".join(risk_lines) + "\nSomente admin pode autorizar override. Use voltar ou cancelar.",
                "dados": {"etapa": estado, "risk_blocked": True},
            }
        return _persist_gold_operation_from_context(db, remetente, contexto, post_save_session=True)

    if estado == "await_preco_simples":
        cotacao = _parse_decimal_from_text(mensagem, "preco_usd")
        if cotacao <= 0:
            return {"mensagem": "Preço inválido. Exemplo: 65.50", "dados": {"etapa": estado}}

        quantidade = Decimal(str(contexto["quantidade"]))
        total_usd = money(quantidade * cotacao)
        contexto["cotacao_usd"] = str(cotacao)
        contexto["total_usd"] = str(total_usd)
        _save_session(db, remetente, "await_moeda_simples", contexto)
        return {
            "mensagem": "Em qual moeda foi pago?\nUSD / EUR / SRD / BRL",
            "dados": {"etapa": "await_moeda_simples"},
        }

    if estado == "await_moeda_simples":
        moeda = _parse_single_currency_choice(mensagem)
        _MOEDAS_VALIDAS = {"USD", "EUR", "SRD", "BRL"}
        if moeda not in _MOEDAS_VALIDAS:
            return {
                "mensagem": (
                    "Moeda inválida. Escolha uma opção:\n"
                    "1) USD\n"
                    "2) EUR\n"
                    "3) SRD\n"
                    "4) BRL\n"
                    "Você também pode digitar: dólar, euro, srd ou real."
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["moeda_liquidacao"] = moeda
        if moeda == "USD":
            contexto["cambio_para_usd"] = "1.0"
            return _finish_transacao_simples(db, remetente, mensagem, contexto)
        else:
            _save_session(db, remetente, "await_cambio_simples", contexto)
            return {
                "mensagem": f"Qual o câmbio?\n(1 USD = quantos {moeda})",
                "dados": {"etapa": "await_cambio_simples"},
            }

    if estado == "await_cambio_simples":
        cambio = _parse_decimal_from_text(mensagem, "cambio_para_usd")
        if cambio <= 0:
            return {
                "mensagem": "Câmbio inválido. Exemplo: 38",
                "dados": {"etapa": estado},
            }
        contexto["cambio_para_usd"] = str(cambio)
        return _finish_transacao_simples(db, remetente, mensagem, contexto)

    if estado == "await_caixa_detalhe":
        requested_currency = _extract_caixa_currency(mensagem)
        if not requested_currency:
            return {
                "mensagem": (
                    "Escolha um caixa para detalhar:\n"
                    "1 (ouro) | 2 (euro) | 3 (dolar) | 4 (surinames) | 5 (real)"
                ),
                "dados": {"etapa": "await_caixa_detalhe"},
            }
        day = _build_day_range(None)
        _clear_session(db, remetente)
        return _build_caixa_detail_response(db, requested_currency, day["start"], day["end"], f"Hoje ({day['date']})")

    # ── Extrato guided flow ──────────────────────────────────────────────────
    if estado == "await_extrato_periodo":
        escolha = _normalize_text(mensagem)
        if escolha in {"1", "hoje", "dia", "hoje (1)", "1)"}:
            day = _build_day_range(None)
            _clear_session(db, remetente)
            return _build_extrato_response(db, day["start"], day["end"], f"Hoje ({day['date']})")
        if escolha in {"2", "semana", "esta semana", "week", "2)"}:
            week = _build_week_range()
            _clear_session(db, remetente)
            return _build_extrato_response(db, week["start"], week["end"], week["label"])
        if escolha in {"3", "data", "datas", "informar", "informar datas", "outro", "3)"}:
            _save_session(db, remetente, "await_extrato_data_inicio", {})
            return {
                "mensagem": (
                    "Informe a data inicial:\n"
                    "Ex: 01/04/2026 ou 2026-04-01"
                ),
                "dados": {"etapa": "await_extrato_data_inicio"},
            }
        return {
            "mensagem": "Escolha inválida. Digite 1, 2 ou 3.",
            "dados": {"etapa": "await_extrato_periodo"},
        }

    if estado == "await_extrato_data_inicio":
        parsed = _parse_date_user_input(mensagem.strip())
        if not parsed:
            return {
                "mensagem": "Data inválida. Use o formato DD/MM/AAAA ou AAAA-MM-DD.",
                "dados": {"etapa": estado},
            }
        _save_session(db, remetente, "await_extrato_data_fim", {"data_inicio": parsed})
        return {
            "mensagem": (
                f"Data inicial: {parsed}\n"
                "Informe a data final:\n"
                "Ex: 04/04/2026 ou 2026-04-04"
            ),
            "dados": {"etapa": "await_extrato_data_fim"},
        }

    if estado == "await_extrato_data_fim":
        parsed = _parse_date_user_input(mensagem.strip())
        if not parsed:
            return {
                "mensagem": "Data inválida. Use o formato DD/MM/AAAA ou AAAA-MM-DD.",
                "dados": {"etapa": estado},
            }
        data_inicio = str(contexto.get("data_inicio", ""))
        if not data_inicio:
            _clear_session(db, remetente)
            return {"mensagem": "Erro interno. Tente novamente: extrato", "dados": {"etapa": "reiniciar"}}
        try:
            start_day = _build_day_range(data_inicio)
            end_day = _build_day_range(parsed)
        except HTTPException:
            return {
                "mensagem": "Datas inválidas. Use o formato AAAA-MM-DD.",
                "dados": {"etapa": estado},
            }
        if end_day["start"] < start_day["start"]:
            return {
                "mensagem": "A data final deve ser maior ou igual à data inicial.",
                "dados": {"etapa": estado},
            }
        label = f"{data_inicio} a {parsed}"
        _clear_session(db, remetente)
        return _build_extrato_response(db, start_day["start"], end_day["end"], label)

    return {"mensagem": "Não foi possível continuar o fluxo. Inicie novamente: compra ou venda.", "dados": {"etapa": "reiniciar"}}


def _finish_transacao_simples(
    db: DatabaseClient,
    remetente: str,
    mensagem: str,
    contexto: Dict[str, Any],
) -> Dict[str, Any]:
    """Persists the quick-flow transaction with moeda and câmbio, then clears session."""
    ativo_id_ctx = int(contexto["ativo_id"])
    quantidade = Decimal(str(contexto["quantidade"]))
    tipo_operacao = str(contexto["tipo_operacao"])
    nome_ativo = str(contexto.get("nome_ativo", ""))
    nome_ativo_display = "Ouro" if "ouro" in nome_ativo.lower() else nome_ativo
    source_msg_id = contexto.get("source_message_id")
    cotacao = Decimal(str(contexto["cotacao_usd"]))
    total_usd = money(Decimal(str(contexto["total_usd"])))
    moeda = str(contexto.get("moeda_liquidacao", "USD")).upper()
    cambio = Decimal(str(contexto.get("cambio_para_usd", "1.0")))
    valor_moeda = money(total_usd * cambio)

    transacao = db.insert_transacao(
        tipo_operacao=tipo_operacao,
        ativo_id=ativo_id_ctx,
        quantidade=quantidade,
        cotacao_usada=cotacao,
        valor_total=total_usd,
        operador_id=remetente,
        source_message_id=source_msg_id,
        status="registrada",
        moeda_liquidacao=moeda,
        valor_moeda=valor_moeda,
        cambio_para_usd=cambio,
    )

    # Generate unique operation ID
    transacao_id = transacao.get("id")
    tz_offset = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    data_agora = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
    data_str = data_agora.strftime("%Y%m%d")
    op_id = f"OP-{data_str}-{transacao_id:05d}" if transacao_id else "OP-UNKNOWN"

    review_payload: Optional[Dict[str, Any]] = None
    review_transaction: Dict[str, Any] = {
        "tipo_operacao": tipo_operacao,
        "ativo": nome_ativo_display,
        "quantidade": str(quantidade),
        "peso": str(quantidade),
        "preco_usd": str(money(cotacao)),
        "valor_total": str(total_usd),
        "total_usd": str(total_usd),
        "total_pago_usd": str(total_usd),
        "pagamentos": [
            {
                "moeda": moeda,
                "valor_moeda": str(valor_moeda),
                "cambio_para_usd": str(cambio),
                "valor_usd": str(total_usd),
            }
        ],
    }
    if _should_trigger_multi_agent_review(review_transaction):
        review_payload = _run_automatic_multi_agent_review(
            db,
            objective="avaliacao automatica de operacao via webhook",
            transaction=review_transaction,
            operation_id=transacao.get("id"),
            operation_kind="transacao",
            source_message_id=source_msg_id,
        )

    operacao_texto = {
        "compra": "Compra registrada",
        "venda": "Venda registrada",
        "cambio": "Câmbio registrado",
    }.get(tipo_operacao, "Operação registrada")

    _clear_session(db, remetente)

    if moeda == "USD":
        moeda_linha = f"${total_usd} USD"
    else:
        moeda_linha = f"{valor_moeda} {moeda} (câmbio: 1 USD = {cambio} {moeda})"

    # Didactic receipt format: short and easy to read.
    data_hora = datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "-3")))
    data_fmt = data_hora.strftime("%d/%m/%Y %H:%M:%S")

    response_payload: Dict[str, Any] = {
        "mensagem": (
            f"✅ {operacao_texto}\n"
            f"ID: {op_id}\n"
            f"Data: {data_fmt}\n"
            f"Tipo: {tipo_operacao}\n"
            f"Ativo: {nome_ativo_display}\n"
            f"Quantidade: {quantidade}g\n"
            f"Preço: ${money(cotacao)}/g\n"
            f"Total USD: ${total_usd}\n"
            f"Pagamento: {moeda_linha}\n"
            "Operação registrada com sucesso."
        ),
        "dados": {
            "intencao": "registrar_operacao",
            "tipo_operacao": tipo_operacao,
            "ativo": nome_ativo_display,
            "operacao_id": op_id,
            "quantidade": str(quantidade),
            "cotacao_usada": str(money(cotacao)),
            "valor_total_usd": str(total_usd),
            "moeda_liquidacao": moeda,
            "valor_moeda": str(valor_moeda),
            "cambio_para_usd": str(cambio),
        },
    }
    if review_payload:
        response_payload["dados"]["analise_multiagente"] = review_payload
    db.insert_log(
        nivel="info",
        remetente=remetente,
        mensagem_recebida=mensagem,
        resposta_enviada=response_payload["mensagem"],
        contexto=response_payload["dados"],
    )
    return response_payload
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    x_provider_message_id: Optional[str] = Header(default=None, alias="X-Provider-Message-Id"),
    x_twilio_message_sid: Optional[str] = Header(default=None, alias="X-Twilio-MessageSid"),
) -> Dict[str, Any]:
    provider_message_id = x_provider_message_id or x_twilio_message_sid
    raw_body: Any = {}
    raw_text = ""
    body_data: Dict[str, Any] = {}
    payload: Optional[WhatsAppWebhookPayload] = None

    try:
        raw_text = (await request.body()).decode("utf-8", errors="ignore")
    except Exception:
        raw_text = ""

    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            body_data = cast(Dict[str, Any], raw_body)
    except Exception:
        body_data = {}

    # Twilio/Pipedream frequently send application/x-www-form-urlencoded.
    if not body_data:
        try:
            form = await request.form()
            body_data = dict(form)
        except Exception:
            body_data = {}

    # Fallback parser for form-urlencoded when request.form() is unavailable.
    if not body_data:
        try:
            parsed = parse_qs(raw_text)
            body_data = {k: v[0] for k, v in parsed.items() if v}
        except Exception:
            body_data = {}

    try:
        payload = WhatsAppWebhookPayload(
            remetente=str(body_data.get("remetente") or body_data.get("From") or "").strip(),
            mensagem=str(body_data.get("mensagem") or body_data.get("Body") or "").strip(),
        )
    except ValidationError:
        raise HTTPException(status_code=400, detail="Mensagem inválida")

    # Allow token from header, query (?token=...), or body field for easy Pipedream wiring.
    token = x_webhook_token or request.query_params.get("token") or body_data.get("token")
    provider_message_id = (
        provider_message_id
        or str(body_data.get("provider_message_id") or "").strip()
        or str(body_data.get("MessageSid") or "").strip()
        or None
    )

    remetente = payload.remetente.strip().replace("whatsapp:", "")
    mensagem = payload.mensagem.strip()
    db: Optional[DatabaseClient] = None

    try:
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()

        if provider_message_id:
            existing = db.get_processed_message(provider_message_id)
            if existing and isinstance(existing.get("resposta_payload"), dict):
                return existing["resposta_payload"]
            cached = _IDEMPOTENCY_CACHE.get(provider_message_id)
            if cached:
                return cached

        response_payload = _processar_webhook(payload, db, provider_message_id)

        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response_payload,
                status_code=200,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response_payload

        return response_payload
    except HTTPException as exc:
        msg = _ERROS_AMIGAVEIS.get(exc.status_code, "Não consegui processar. Envie: menu")
        response: Dict[str, Any] = {
            "mensagem": f"⚠️ {msg}",
            "dados": {"erro": exc.status_code, "detalhe": exc.detail},
        }
        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=exc.status_code,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response
        return response
    except Exception:
        logger.exception("Erro inesperado no webhook")
        response = {
                "mensagem": "⚠️ Erro inesperado. Tente novamente.",
            "dados": {"erro": 500},
        }
        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=500,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response
        return response


@app.post("/webhook/twilio")
async def whatsapp_webhook_twilio(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    x_twilio_message_sid: Optional[str] = Header(default=None, alias="X-Twilio-MessageSid"),
) -> Response:
    body_data: Dict[str, Any] = {}
    try:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        parsed = parse_qs(raw)
        body_data = {k: v[0] for k, v in parsed.items() if v}
    except Exception:
        body_data = {}

    token = x_webhook_token or request.query_params.get("token") or body_data.get("token")
    provider_message_id = (
        x_twilio_message_sid
        or str(body_data.get("MessageSid") or "").strip()
        or None
    )

    remetente = str(body_data.get("From") or "").strip().replace("whatsapp:", "")
    mensagem = str(body_data.get("Body") or "").strip()

    if not remetente or not mensagem:
        return _twiml_message("⚠️ Mensagem inválida. Tente novamente.")

    payload = WhatsAppWebhookPayload(remetente=remetente, mensagem=mensagem)
    suppress_reply = _should_suppress_twilio_reply(mensagem)
    db: Optional[DatabaseClient] = None

    try:
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()

        if provider_message_id:
            existing = db.get_processed_message(provider_message_id)
            if existing and isinstance(existing.get("resposta_payload"), dict):
                if suppress_reply:
                    return _twiml_empty_response()
                return _twiml_message(str(existing["resposta_payload"].get("mensagem") or ""))
            cached = _IDEMPOTENCY_CACHE.get(provider_message_id)
            if cached:
                if suppress_reply:
                    return _twiml_empty_response()
                return _twiml_message(str(cached.get("mensagem") or ""))

        response = _processar_webhook(payload, db, provider_message_id)

        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=200,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response

        if suppress_reply:
            return _twiml_empty_response()
        return _twiml_message(str(response.get("mensagem") or "Operação processada."))
    except HTTPException as exc:
        msg = _ERROS_AMIGAVEIS.get(exc.status_code, "Não consegui processar. Envie: menu")
        response_payload: Dict[str, Any] = {
            "mensagem": f"⚠️ {msg}",
            "dados": {"erro": exc.status_code, "detalhe": exc.detail},
        }
        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response_payload,
                status_code=exc.status_code,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response_payload
        if suppress_reply:
            return _twiml_empty_response()
        return _twiml_message(response_payload["mensagem"])
    except Exception:
        logger.exception("Erro inesperado no webhook Twilio")
        if suppress_reply:
            return _twiml_empty_response()
        return _twiml_message("⚠️ Erro inesperado. Tente novamente.")


@app.get("/reports/daily-closure")
def daily_closure_report(date: Optional[str] = None) -> Dict[str, Any]:
    db = get_db()
    day = _build_day_range(date)
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    by_operator = db.get_daily_gold_summary_by_operator(day["start"], day["end"])
    return {
        "date": day["date"],
        "summary": summary,
        "by_operator": by_operator,
    }


def _build_inventory_status_report_payload(db: DatabaseClient) -> Dict[str, Any]:
    return reporting_service._build_inventory_status_report_payload(
        db,
        get_cached_payload=_get_inventory_status_report_cached,
        set_cached_payload=_set_inventory_status_report_cached,
        get_market_snapshot=_get_market_snapshot,
        build_open_lot_market_context=_build_open_lot_market_context,
        compute_inventory_metrics=_compute_inventory_metrics,
        build_fifo_inventory_lots=_build_fifo_inventory_lots,
    )


@app.get("/reports/inventory-status")
def inventory_status_report() -> Dict[str, Any]:
    db = get_db()
    return _build_inventory_status_report_payload(db)


@app.get("/saas/market-snapshot")
def saas_market_snapshot(request: Request) -> Dict[str, Any]:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        raise HTTPException(status_code=401, detail="Sessao expirada")
    snapshot = _get_market_snapshot()
    return {
        "ok": True,
        "snapshot": snapshot,
        "cache_ttl_seconds": _MARKET_CACHE_TTL_SECONDS,
    }


@app.get("/saas/market-stream")
async def saas_market_stream(request: Request) -> StreamingResponse:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        raise HTTPException(status_code=401, detail="Sessao expirada")
    return StreamingResponse(
        _market_stream_events(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/saas/market-news")
def saas_market_news(request: Request) -> Dict[str, Any]:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        raise HTTPException(status_code=401, detail="Sessao expirada")
    return {"ok": True, "items": _get_market_news()}


@app.get("/saas/fragments/dashboard-news")
def saas_dashboard_news_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    cache_key = _build_dashboard_fragment_cache_key(_DASHBOARD_FRAGMENT_NEWS_NAME)
    return _render_cached_dashboard_fragment(
        cache_key,
        lambda: _render_market_news_panel_html(_get_market_news(), limit=3),
    )


@app.get("/saas/fragments/dashboard-monitors")
def saas_dashboard_monitors_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    default_alert_phone = _normalize_user_phone(str(session_user.get("telefone") or ""))
    cache_key = _build_dashboard_fragment_cache_key(
        _DASHBOARD_FRAGMENT_MONITORS_NAME,
        scope=default_alert_phone or "default",
    )

    def _render_fragment() -> str:
        inventory = db.get_gold_inventory_status(open_only=True)
        if not inventory.get("has_any_lots"):
            db.sync_gold_inventory_ledger()
            inventory = db.get_gold_inventory_status(open_only=True)

        market_snapshot = _get_market_snapshot()
        lot_market_context = _build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)
        market_trend = _build_market_trend_context()
        lot_monitor_entries = cast(
            List[Dict[str, Any]],
            _build_web_lot_monitor_view_model(
                lot_market_context,
                market_trend,
                default_alert_phone=default_alert_phone,
                entry_limit=24,
                alert_limit=0,
            ).get("entries")
            or [],
        )
        enabled_lot_monitor_entries = [item for item in lot_monitor_entries if item.get("enabled")]
        return _render_lot_monitor_cards(
            enabled_lot_monitor_entries,
            "dashboard",
            "Nenhum lote foi selecionado para monitoramento no dashboard.",
            default_alert_phone,
        )

    return _render_cached_dashboard_fragment(cache_key, _render_fragment, use_shared=False)


@app.get("/saas/fragments/dashboard-inventory")
def saas_dashboard_inventory_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    cache_key = _build_dashboard_fragment_cache_key(_DASHBOARD_FRAGMENT_INVENTORY_NAME)

    def _render_fragment() -> str:
        inventory = db.get_gold_inventory_status(open_only=True)
        if not inventory.get("has_any_lots"):
            db.sync_gold_inventory_ledger()
            inventory = db.get_gold_inventory_status(open_only=True)

        market_snapshot = _get_market_snapshot()
        lot_market_context = _build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)
        return _render_dashboard_inventory_html(inventory, lot_market_context)

    return _render_cached_dashboard_fragment(cache_key, _render_fragment)


@app.get("/saas/fragments/dashboard-trend")
def saas_dashboard_trend_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    cache_key = _build_dashboard_fragment_cache_key(_DASHBOARD_FRAGMENT_TREND_NAME)
    return _render_cached_dashboard_fragment(
        cache_key,
        lambda: _render_dashboard_trend_html(db.get_gold_inventory_transactions()),
    )


@app.get("/saas/fragments/dashboard-summary")
def saas_dashboard_summary_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    cache_key = _build_dashboard_fragment_cache_key(_DASHBOARD_FRAGMENT_SUMMARY_NAME)

    def _render_fragment() -> str:
        day = _build_day_range(None)
        day_transactions = db.get_extrato_transactions(day["start"], day["end"])
        summary = _build_statement_summary(day_transactions)
        gross_grams_today = sum((Decimal(str(item.get("peso") or "0")) for item in day_transactions), Decimal("0"))

        saldo = db.get_saldo_caixa()
        gold_caixa_metrics = _build_gold_caixa_metrics_from_pending_grams(Decimal(str(saldo.get("XAU", "0"))), db.get_gold_pending_closure_grams())
        return _render_dashboard_summary_html(summary, gross_grams_today, gold_caixa_metrics["ouro_proprio"])

    return _render_cached_dashboard_fragment(cache_key, _render_fragment)


@app.get("/saas/fragments/dashboard-pending-closings")
def saas_dashboard_pending_closings_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    cache_key = _build_dashboard_fragment_cache_key(_DASHBOARD_FRAGMENT_PENDING_CLOSINGS_NAME)

    def _render_fragment() -> str:
        week = _build_week_range()
        week_transactions = db.get_extrato_transactions(week["start"], week["end"])
        return _render_dashboard_pending_closings_html(week_transactions)

    return _render_cached_dashboard_fragment(cache_key, _render_fragment)


@app.get("/saas/fragments/dashboard-recent-operations")
def saas_dashboard_recent_operations_fragment(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content="Sessao expirada.", media_type="text/plain", status_code=401)
    cache_key = _build_dashboard_fragment_cache_key(_DASHBOARD_FRAGMENT_RECENT_OPERATIONS_NAME)

    def _render_fragment() -> str:
        week = _build_week_range()
        week_transactions = db.get_extrato_transactions(week["start"], week["end"])
        recent_ops = week_transactions[-12:]
        return _render_dashboard_recent_operations_html(recent_ops)

    return _render_cached_dashboard_fragment(cache_key, _render_fragment)


@app.get("/saas/lot-monitor-snapshot")
def saas_lot_monitor_snapshot(request: Request) -> Dict[str, Any]:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        raise HTTPException(status_code=401, detail="Sessao expirada")
    return _build_lot_monitor_snapshot_payload(db, session_user)


@app.get("/saas/lot-monitor-stream")
async def saas_lot_monitor_stream(request: Request) -> StreamingResponse:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        raise HTTPException(status_code=401, detail="Sessao expirada")
    return StreamingResponse(
        _lot_monitor_stream_events(request, session_user, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/saas/lots/{lot_id}/monitor")
async def saas_update_lot_monitor(lot_id: int, request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content=_render_saas_login_html(), media_type="text/html")

    form = await _request_form_dict(request)
    try:
        target_raw = str(form.get("target_price_usd") or "").strip()
        min_profit_raw = str(form.get("min_profit_pct") or "4").strip()
        notify_phone = _normalize_user_phone(str(form.get("notify_phone") or ""))
        monitor_payload = {
            "enabled": bool(form.get("enabled")),
            "notify_phone": notify_phone,
            "target_price_usd": str(_parse_decimal_web_field(target_raw, "target_price_usd")) if target_raw else "",
            "min_profit_pct": str(_parse_decimal_web_field(min_profit_raw, "min_profit_pct")) if min_profit_raw else "4.00",
            "updated_by": str(session_user.get("telefone") or ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        updated = db.update_gold_inventory_lot_monitor(lot_id, monitor_payload)
        if not updated:
            raise HTTPException(status_code=404, detail="Lote nao encontrado para monitoramento")
        _invalidate_dashboard_monitors_fragment_cache()
        _invalidate_lot_monitor_snapshot_cache()
        html = _render_saas_dashboard_html(
            db,
            session_user,
            notice=f"Monitor do lote GT-{lot_id} atualizado.",
            notice_kind="info",
            current_page="dashboard",
        )
        return Response(content=html, media_type="text/html")
    except HTTPException as exc:
        html = _render_saas_dashboard_html(
            db,
            session_user,
            notice=str(exc.detail),
            notice_kind="error",
            current_page="dashboard",
        )
        return Response(content=html, media_type="text/html", status_code=exc.status_code)


def _build_admin_dashboard_html(db: DatabaseClient) -> str:
    return reporting_service._build_admin_dashboard_html(
        db,
        build_day_range=_build_day_range,
        build_cache_key=_build_admin_dashboard_cache_key,
        get_cached_html=_get_admin_dashboard_cached,
        set_cached_html=_set_admin_dashboard_cached,
        compute_inventory_metrics=_compute_inventory_metrics,
        build_fifo_inventory_lots=_build_fifo_inventory_lots,
        format_caixa_movement=_format_caixa_movement,
    )


@app.get("/admin/dashboard")
def admin_dashboard(x_webhook_token: Optional[str] = Header(default=None)) -> Response:
    validate_webhook_token(x_webhook_token)
    db = get_db()
    return Response(content=_build_admin_dashboard_html(db), media_type="text/html")


@app.get("/saas")
@app.get("/saas/dashboard")
@app.get("/saas/operation")
@app.get("/saas/monitores")
@app.get("/saas/noticias")
@app.get("/saas/clientes")
@app.get("/saas/extrato")
@app.get("/saas/profile")
def saas_dashboard(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content=_render_saas_login_html(), media_type="text/html")
    prefill_values: Optional[Dict[str, str]] = None
    current_page = _normalize_saas_page(request.query_params.get("page"))
    if request.url.path.endswith("/operation"):
        current_page = "operation"
    elif request.url.path.endswith("/monitores"):
        current_page = "monitors"
    elif request.url.path.endswith("/noticias"):
        current_page = "news_hub"
    elif request.url.path.endswith("/clientes"):
        current_page = "clients"
    elif request.url.path.endswith("/extrato"):
        current_page = "statement"
    elif request.url.path.endswith("/profile"):
        current_page = "profile"

    statement_context: Optional[Dict[str, Any]] = None
    clients_context: Optional[Dict[str, Any]] = None
    if current_page == "operation":
        raw_client_id = str(request.query_params.get("client_id") or "").strip()
        if raw_client_id.isdigit():
            cliente = db.get_cliente_by_id(int(raw_client_id))
            if cliente:
                prefill_values = {
                    "cliente_id": str(cliente.get("id") or ""),
                    "pessoa": str(cliente.get("nome") or ""),
                    "cliente_lookup_meta": _build_cliente_lookup_meta(cliente),
                }
    if current_page == "statement":
        try:
            statement_context = _build_saas_statement_context(
                db,
                request.query_params.get("start_date"),
                request.query_params.get("end_date"),
            )
        except HTTPException as exc:
            statement_context = _build_saas_statement_context(db, None, None)
            html = _render_saas_dashboard_html(
                db,
                session_user,
                notice=str(exc.detail),
                notice_kind="error",
                current_page="statement",
                statement_context=statement_context,
            )
            return Response(content=html, media_type="text/html", status_code=exc.status_code)
    elif current_page == "clients":
        selected_client_id: Optional[int] = None
        raw_client_id = str(request.query_params.get("client_id") or "").strip()
        if raw_client_id.isdigit():
            selected_client_id = int(raw_client_id)
        clients_context = _build_saas_clients_context(db, selected_client_id=selected_client_id, search_term=request.query_params.get("q"))

    return Response(
        content=_render_saas_dashboard_html(db, session_user, current_page=current_page, statement_context=statement_context, clients_context=clients_context, form_values=prefill_values),
        media_type="text/html",
    )


@app.get("/saas/clientes/search")
def saas_client_search(request: Request, q: str = "") -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(
            content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False),
            media_type="application/json",
            status_code=401,
        )
    items = [
        {
            "id": item.get("id"),
            "nome": str(item.get("nome") or ""),
            "meta": _build_cliente_lookup_meta(item),
        }
        for item in db.search_clientes(q, limit=8)
    ]
    return Response(content=json.dumps({"ok": True, "items": items}, ensure_ascii=False), media_type="application/json")


@app.get("/saas/clientes/{cliente_id}")
def saas_client_detail(request: Request, cliente_id: int) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content=_render_saas_login_html(), media_type="text/html")
    clients_context = _build_saas_clients_context(db, selected_client_id=cliente_id, search_term=request.query_params.get("q"))
    return Response(
        content=_render_saas_dashboard_html(db, session_user, current_page="clients", clients_context=clients_context),
        media_type="text/html",
    )


@app.post("/saas/clientes")
async def saas_create_client(request: Request) -> Response:
    form = await _request_form_dict(request)
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response

    values = {k: str(v) for k, v in form.items()}
    current_page = _normalize_saas_page(form.get("page") or "clients")
    try:
        nome = str(form.get("client_nome") or "").strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Nome do cliente é obrigatório")
        opening_balances = _parse_cliente_opening_balances(values, "client_opening")
        cliente = db.create_cliente(
            nome=nome,
            telefone=str(form.get("client_telefone") or "").strip() or None,
            documento=str(form.get("client_documento") or "").strip() or None,
            apelido=str(form.get("client_apelido") or "").strip() or None,
            observacoes=str(form.get("client_observacoes") or "").strip() or None,
            opening_balances=opening_balances,
        )
        if not cliente:
            raise HTTPException(status_code=409, detail="Cadastro de clientes indisponível. Aplique a migração do banco antes de usar esta rotina.")

        selected_client_id = int(cliente.get("id") or 0)
        clients_context = _build_saas_clients_context(db, selected_client_id=selected_client_id)
        if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
            return Response(
                content=json.dumps(
                    {
                        "ok": True,
                        "item": {
                            "id": cliente.get("id"),
                            "nome": str(cliente.get("nome") or ""),
                            "meta": _build_cliente_lookup_meta(cliente),
                        },
                    },
                    ensure_ascii=False,
                ),
                media_type="application/json",
            )
        return Response(
            content=_render_saas_dashboard_html(
                db,
                session_user,
                notice=f"Cliente registrado com sucesso. {_format_cliente_code(cliente.get('id'))}",
                current_page=current_page,
                form_values=values,
                clients_context=clients_context,
            ),
            media_type="text/html",
        )
    except HTTPException as exc:
        clients_context = _build_saas_clients_context(db)
        if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
            return Response(
                content=json.dumps({"ok": False, "notice": _ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail))}, ensure_ascii=False),
                media_type="application/json",
                status_code=exc.status_code,
            )
        return Response(
            content=_render_saas_dashboard_html(
                db,
                session_user,
                notice=_ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail)),
                notice_kind="error",
                current_page=current_page,
                form_values=values,
                clients_context=clients_context,
            ),
            media_type="text/html",
            status_code=exc.status_code,
        )


@app.post("/saas/login")
async def saas_login(request: Request) -> Response:
    form = await _request_form_dict(request)
    telefone = _normalize_user_phone(str(form.get("telefone") or ""))
    pin = str(form.get("pin") or "")
    if not telefone or not pin:
        return Response(content=_render_saas_login_html("Informe telefone e PIN.", telefone=telefone), media_type="text/html", status_code=400)

    db = get_db()
    usuario = db.verify_usuario_web_pin(telefone, pin)
    if not usuario:
        return Response(content=_render_saas_login_html("Credenciais inválidas.", telefone=telefone), media_type="text/html", status_code=401)

    _set_saas_authenticated_user_cached(telefone, dict(usuario))
    response = Response(content=_render_saas_dashboard_html(db, usuario), media_type="text/html")
    _set_saas_session(response, telefone)
    return response


@app.post("/saas/logout")
def saas_logout(request: Request) -> Response:
    telefone = _decode_saas_session(request.cookies.get(_SAAS_SESSION_COOKIE))
    if telefone:
        _invalidate_saas_authenticated_user_cache(telefone)
    response = Response(content=_render_saas_login_html("Sessão encerrada."), media_type="text/html")
    _clear_saas_session(response)
    return response


@app.post("/saas/profile/pin")
async def saas_profile_pin(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
            return Response(
                content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False),
                media_type="application/json",
                status_code=401,
            )
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response

    form = await _request_form_dict(request)
    current_page = _normalize_saas_page(form.get("page"))
    current_pin = str(form.get("current_pin") or "")
    new_pin = str(form.get("new_pin") or "")
    confirm_pin = str(form.get("confirm_pin") or "")
    try:
        _validate_web_pin_format(current_pin)
        validated_new_pin = _validate_web_pin_format(new_pin)
    except HTTPException as exc:
        html = _render_saas_dashboard_html(db, session_user, notice=str(exc.detail), notice_kind="error", current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=exc.status_code)

    if validated_new_pin != confirm_pin:
        html = _render_saas_dashboard_html(db, session_user, notice="Confirmação do novo PIN não confere.", notice_kind="error", current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=400)
    if not db.verify_usuario_web_pin(str(session_user.get("telefone") or ""), current_pin):
        html = _render_saas_dashboard_html(db, session_user, notice="PIN atual inválido.", notice_kind="error", current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=401)
    update_result = db.set_usuario_web_pin(str(session_user.get("telefone") or ""), validated_new_pin)
    if not update_result:
        html = _render_saas_dashboard_html(db, session_user, notice="Não foi possível atualizar o PIN.", notice_kind="error", current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=500)
    if not bool(update_result.get("web_pin_schema_ready", True)):
        html = _render_saas_dashboard_html(
            db,
            session_user,
            notice="Troca de PIN indisponível: aplique a migração do banco que adiciona web_pin_hash e web_pin_updated_em na tabela usuarios.",
            notice_kind="error",
            current_page=current_page,
        )
        return Response(content=html, media_type="text/html", status_code=409)

    _invalidate_saas_authenticated_user_cache(str(session_user.get("telefone") or ""))
    refreshed_user = db.get_usuario_web_auth(str(session_user.get("telefone") or "")) or session_user
    _set_saas_authenticated_user_cached(str(session_user.get("telefone") or ""), dict(refreshed_user))
    response = Response(content=_render_saas_dashboard_html(db, refreshed_user, notice="PIN web atualizado com sucesso.", current_page=current_page), media_type="text/html")
    _set_saas_session(response, str(session_user.get("telefone") or ""))
    return response


@app.post("/saas/console")
async def saas_console(request: Request) -> Response:
    form = await _request_form_dict(request)
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response
    current_page = _normalize_saas_page(form.get("page"))
    remetente = str(form.get("console_remetente") or "").strip()
    mensagem = str(form.get("console_mensagem") or "").strip()
    values = {k: str(v) for k, v in form.items()}
    if not remetente or not mensagem:
        if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
            return Response(
                content=json.dumps({"ok": False, "notice": "Preencha remetente e mensagem no chat."}, ensure_ascii=False),
                media_type="application/json",
                status_code=400,
            )
        html = _render_saas_dashboard_html(db, session_user, notice="Preencha remetente e mensagem no console.", notice_kind="error", form_values=values, current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=400)

    if str(session_user.get("tipo_usuario") or "").lower() != "admin":
        remetente = str(session_user.get("telefone") or remetente)
        values["console_remetente"] = remetente

    mensagem_norm = _normalize_text(mensagem)
    web_session = _get_session(db, remetente)
    if web_session:
        estado = str(web_session.get("estado", ""))
        if estado in _GUIDED_FLOW_STATES and _is_guided_session_stale(web_session):
            if mensagem_norm not in {"continuar", "continue", "cancelar", "cancela", "cancel", "parar", "sair"}:
                _clear_session(db, remetente)

    try:
        result = _processar_webhook(WhatsAppWebhookPayload(remetente=remetente, mensagem=mensagem), db, None)
        if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
            return Response(
                content=json.dumps(
                    {
                        "ok": True,
                        "message": str(result.get("mensagem") or ""),
                        "notice": "Mensagem processada pelo motor do WhatsApp.",
                    },
                    ensure_ascii=False,
                ),
                media_type="application/json",
            )
        html = _render_saas_dashboard_html(db, session_user, notice="Mensagem processada pelo motor do WhatsApp.", notice_kind="info", assistant_result=result, form_values=values, current_page=current_page)
        return Response(content=html, media_type="text/html")
    except HTTPException as exc:
        if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
            return Response(
                content=json.dumps({"ok": False, "notice": _ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail))}, ensure_ascii=False),
                media_type="application/json",
                status_code=exc.status_code,
            )
        html = _render_saas_dashboard_html(db, session_user, notice=_ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail)), notice_kind="error", form_values=values, current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=exc.status_code)


@app.post("/saas/operations/draft")
async def saas_operation_draft(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(
            content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False),
            media_type="application/json",
            status_code=401,
        )

    form = await _request_form_dict(request)
    draft_message = str(form.get("draft_message") or "").strip()
    try:
        payload = _build_operation_draft_from_message(db, session_user, draft_message)
        return Response(
            content=json.dumps({"ok": True, **payload}, ensure_ascii=False),
            media_type="application/json",
        )
    except HTTPException as exc:
        return Response(
            content=json.dumps({"ok": False, "notice": _ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail))}, ensure_ascii=False),
            media_type="application/json",
            status_code=exc.status_code,
        )


@app.post("/saas/operations/quick")
async def saas_quick_operation(request: Request) -> Response:
    form = await _request_form_dict(request)
    is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        if is_ajax:
            return Response(
                content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False),
                media_type="application/json",
                status_code=401,
            )
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response
    current_page = _normalize_saas_page(form.get("page"))
    values = {k: str(v) for k, v in form.items()}
    try:
        operador_id = _normalize_user_phone(str(form.get("operador_id") or session_user.get("telefone") or ""))
        tipo_operacao = _normalize_text(str(form.get("tipo_operacao") or "compra"))
        origem = _normalize_text(str(form.get("origem") or "balcao"))
        gold_type, quebra = _parse_gold_trade_profile(tipo_operacao, form.get("gold_type"), form.get("quebra"))
        teor = _parse_decimal_web_field(str(form.get("teor") or "0"), "teor")
        peso = _parse_decimal_web_field(str(form.get("peso") or "0"), "peso")
        preco_usd = _parse_decimal_web_field(str(form.get("preco_usd") or "0"), "preco_usd")
        pessoa = str(form.get("pessoa") or "").strip()
        cliente_id_raw = str(form.get("cliente_id") or "").strip()
        observacoes = str(form.get("observacoes") or "").strip()
        if tipo_operacao not in {"compra", "venda"}:
            raise HTTPException(status_code=400, detail="Tipo de operação inválido")
        if origem not in {"balcao", "fora"}:
            raise HTTPException(status_code=400, detail="Origem inválida")
        if teor < 0 or teor > Decimal("99.99"):
            raise HTTPException(status_code=400, detail="Teor inválido")
        if peso <= 0 or preco_usd <= 0:
            raise HTTPException(status_code=400, detail="Peso e preço devem ser maiores que zero")

        cliente: Optional[Dict[str, Any]] = None
        if cliente_id_raw:
            if not cliente_id_raw.isdigit():
                raise HTTPException(status_code=400, detail="Cliente inválido")
            cliente = db.get_cliente_by_id(int(cliente_id_raw))
            if not cliente:
                raise HTTPException(status_code=404, detail="Cliente não encontrado")
        else:
            inline_mode = str(form.get("inline_cliente_mode") or "0") == "1"
            inline_nome = str(form.get("inline_cliente_nome") or pessoa).strip()
            inline_phone = str(form.get("inline_cliente_telefone") or "").strip()
            inline_document = str(form.get("inline_cliente_documento") or "").strip()
            inline_apelido = str(form.get("inline_cliente_apelido") or "").strip()
            inline_observacoes = str(form.get("inline_cliente_observacoes") or "").strip()
            opening_balances: Dict[str, Decimal] = {}
            inline_saldo_xau = str(form.get("inline_cliente_saldo_xau") or "").strip()
            if inline_saldo_xau:
                opening_balances["XAU"] = _parse_decimal_web_field(inline_saldo_xau, "inline_cliente_saldo_xau")
            if inline_mode or inline_nome or inline_phone or inline_document:
                if not inline_nome:
                    raise HTTPException(status_code=400, detail="Nome do cliente é obrigatório no cadastro rápido")
                cliente = db.create_cliente(
                    nome=inline_nome,
                    telefone=inline_phone or None,
                    documento=inline_document or None,
                    apelido=inline_apelido or None,
                    observacoes=inline_observacoes or None,
                    opening_balances=opening_balances,
                )
                if not cliente:
                    raise HTTPException(status_code=409, detail="Cadastro de clientes indisponível. Aplique a migração do banco antes de usar esta rotina.")
                values["cliente_id"] = str(cliente.get("id") or "")
                values["inline_cliente_mode"] = "0"
            else:
                raise HTTPException(status_code=400, detail="Selecione ou cadastre o cliente da operação")

        pessoa = str((cliente or {}).get("nome") or pessoa).strip()
        if not pessoa:
            raise HTTPException(status_code=400, detail="Cliente da operação é obrigatório")
        cliente_id = int((cliente or {}).get("id") or 0)
        values["cliente_id"] = str(cliente_id)
        values["pessoa"] = pessoa
        values["cliente_lookup_meta"] = _build_cliente_lookup_meta(cliente or {"id": cliente_id, "nome": pessoa})

        session_phone = str(session_user.get("telefone") or "")
        is_admin = str(session_user.get("tipo_usuario", "")).lower() == "admin"
        if not operador_id:
            operador_id = session_phone
        if not is_admin and operador_id != session_phone:
            raise HTTPException(status_code=403, detail="Operador web só pode lançar em seu próprio usuário")

        usuario = db.get_usuario_by_telefone(operador_id)
        if not usuario:
            raise HTTPException(status_code=403, detail="Operador não autorizado")

        total_usd = money(peso * preco_usd)
        pagamentos = _parse_web_payments_from_form(db, values)
        total_pago_usd = sum((Decimal(str(item.get("valor_usd") or "0")) for item in pagamentos), Decimal("0"))
        forma_pagamento = _derive_forma_pagamento_summary(pagamentos)

        fechamento_raw = str(form.get("fechamento_gramas") or "").strip()
        fechamento_gramas = peso if not fechamento_raw else _parse_decimal_web_field(fechamento_raw, "fechamento_gramas")
        fechamento_tipo = _normalize_text(str(form.get("fechamento_tipo") or "total"))
        if fechamento_tipo not in {"total", "parcial"}:
            raise HTTPException(status_code=400, detail="Fechamento inválido")
        if fechamento_gramas < 0 or fechamento_gramas > peso:
            raise HTTPException(status_code=400, detail="Fechamento em gramas inválido")

        contexto: Dict[str, Any] = {
            "tipo_operacao": tipo_operacao,
            "origem": origem,
            "gold_type": gold_type,
            "quebra": str(quebra) if quebra is not None else None,
            "teor": str(money(teor)),
            "peso": str(peso),
            "preco_moeda": "USD",
            "preco_usd": str(money(preco_usd)),
            "total_usd": str(total_usd),
            "total_pago_usd": str(money(total_pago_usd)),
            "fechamento_gramas": str(money(fechamento_gramas)),
            "fechamento_tipo": fechamento_tipo,
            "cliente_id": cliente_id,
            "pessoa": pessoa,
            "forma_pagamento": forma_pagamento,
            "observacoes": observacoes,
            "source_message_id": None,
            "pagamentos": pagamentos,
        }
        if tipo_operacao == "venda":
            _attach_sale_profit_reference(db, contexto)

        projected = _project_caixa_balances(db.get_saldo_caixa(), tipo_operacao, peso, cast(List[Dict[str, Any]], contexto["pagamentos"]))
        negative_balances = _find_negative_caixa_balances(projected)
        fifo_shortfall = Decimal(str(contexto.get("fifo_shortfall_grams", "0")))
        risk_lines: List[str] = []
        if negative_balances:
            risk_lines.append("Saldos projetados negativos:")
            risk_lines.extend(_format_negative_caixa_lines(negative_balances))
        if fifo_shortfall > 0:
            risk_lines.append(f"- Estoque FIFO insuficiente: faltam {fifo_shortfall} g")

        wants_override = str(form.get("risk_override") or "") == "1"
        if risk_lines and not (is_admin and wants_override):
            if is_ajax:
                return Response(
                    content=json.dumps({"ok": False, "notice": "⛔ " + " | ".join(risk_lines)}, ensure_ascii=False),
                    media_type="application/json",
                    status_code=400,
                )
            html = _render_saas_dashboard_html(db, session_user, notice="⛔ " + " | ".join(risk_lines), notice_kind="error", form_values=values, current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=400)

        result = _persist_gold_operation_from_context(db, operador_id, contexto, post_save_session=False)
        gt_id_raw = result.get("dados", {}).get("gold_transaction_id")
        if not gt_id_raw:
            ok_msg = "Operação web salva com sucesso."
            if is_ajax:
                return Response(
                    content=json.dumps({"ok": True, "notice": ok_msg, "receipt_url": None}, ensure_ascii=False),
                    media_type="application/json",
                )
            html = _render_saas_dashboard_html(db, session_user, notice=ok_msg, notice_kind="info", assistant_result=result, form_values=values, current_page=current_page)
            return Response(content=html, media_type="text/html")

        gt_id = int(gt_id_raw)
        receipt_url = str(request.url_for("saas_receipt_view", operation_id=gt_id))
        if is_ajax:
            return Response(
                content=json.dumps(
                    {
                        "ok": True,
                        "notice": f"Operacao web salva com sucesso. Recibo GT-{gt_id} aberto em outra pagina.",
                        "receipt_url": receipt_url,
                        "operation_id": gt_id,
                    },
                    ensure_ascii=False,
                ),
                media_type="application/json",
            )
        receipt = _build_gold_receipt_context(db, gt_id)
        pdf_url = str(request.url_for("saas_receipt_pdf", operation_id=gt_id))
        back_url = "/saas?page=operations"
        html = _render_saas_receipt_html(receipt, pdf_url=pdf_url, back_url=back_url)
        return Response(content=html, media_type="text/html")
    except HTTPException as exc:
        if is_ajax:
            return Response(
                content=json.dumps({"ok": False, "notice": _ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail))}, ensure_ascii=False),
                media_type="application/json",
                status_code=exc.status_code,
            )
        html = _render_saas_dashboard_html(db, session_user, notice=_ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail)), notice_kind="error", form_values=values, current_page=current_page)
        return Response(content=html, media_type="text/html", status_code=exc.status_code)


@app.get("/saas/recibos/{operation_id}", name="saas_receipt_view")
def saas_receipt_view(operation_id: int, request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response

    receipt = _build_gold_receipt_context(db, operation_id)
    pdf_url = str(request.url_for("saas_receipt_pdf", operation_id=operation_id))
    html = _render_saas_receipt_html(receipt, pdf_url=pdf_url, back_url="/saas?page=operations")
    return Response(content=html, media_type="text/html")


@app.get("/saas/recibos/{operation_id}/pdf", name="saas_receipt_pdf")
def saas_receipt_pdf(operation_id: int, request: Request) -> StreamingResponse:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")

    pdf_url = str(request.url_for("saas_receipt_pdf", operation_id=operation_id))
    receipt = _build_gold_receipt_context(db, operation_id)
    pdf_bytes = _build_gold_receipt_pdf(receipt, pdf_url=pdf_url)
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="recibo-gt-{operation_id}.pdf"'},
    )


@app.get("/reports/risk-alerts")
def risk_alerts_report(date: Optional[str] = None) -> Dict[str, Any]:
    db = get_db()
    day = _build_day_range(date)
    alerts = db.get_risk_alerts(day["start"], day["end"])
    return {
        "date": day["date"],
        "total_alertas": len(alerts),
        "alerts": alerts,
    }


@app.get("/reports/closure-range")
def closure_range_report(start: str, end: str) -> Dict[str, Any]:
    db = get_db()
    rng = _build_custom_range(start, end)
    summary = db.get_gold_summary_range(rng["start"], rng["end"])
    by_operator = db.get_daily_gold_summary_by_operator(rng["start"], rng["end"])
    return {
        "range": rng,
        "summary": summary,
        "by_operator": by_operator,
    }


@app.get("/reports/reconciliation-by-currency")
def reconciliation_by_currency_report(start: str, end: str) -> Dict[str, Any]:
    db = get_db()
    rng = _build_custom_range(start, end)
    by_currency = db.get_gold_summary_by_currency(rng["start"], rng["end"])
    return {
        "range": rng,
        "by_currency": by_currency,
    }


@app.get("/reports/closure-csv")
def closure_csv_report(start: str, end: str) -> Response:
    db = get_db()
    rng = _build_custom_range(start, end)
    summary = db.get_gold_summary_range(rng["start"], rng["end"])
    by_operator = db.get_daily_gold_summary_by_operator(rng["start"], rng["end"])
    by_currency = db.get_gold_summary_by_currency(rng["start"], rng["end"])

    lines: List[str] = [
        "section,key,value",
        f"summary,total_operacoes,{summary.get('total_operacoes', 0)}",
        f"summary,total_usd,{summary.get('total_usd', '0')}",
        f"summary,total_pago_usd,{summary.get('total_pago_usd', '0')}",
        f"summary,total_diferenca_usd,{summary.get('total_diferenca_usd', '0')}",
        "",
        "operators,operador_id,total_operacoes,total_usd,total_pago_usd,total_diferenca_usd",
    ]

    for row in by_operator:
        lines.append(
            "operators,"
            f"{row.get('operador_id', '')},"
            f"{row.get('total_operacoes', 0)},"
            f"{row.get('total_usd', '0')},"
            f"{row.get('total_pago_usd', '0')},"
            f"{row.get('total_diferenca_usd', '0')}"
        )

    lines.extend([
        "",
        "currency,moeda,total_pagamentos,total_valor_moeda,total_valor_usd",
    ])

    for row in by_currency:
        lines.append(
            "currency,"
            f"{row.get('moeda', '')},"
            f"{row.get('total_pagamentos', 0)},"
            f"{row.get('total_valor_moeda', '0')},"
            f"{row.get('total_valor_usd', '0')}"
        )

    csv_content = "\n".join(lines)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=closure_report.csv",
        },
    )


@app.get("/reports/top-divergences")
def top_divergences_report(start: str, end: str, limit: int = 10) -> Dict[str, Any]:
    db = get_db()
    rng = _build_custom_range(start, end)
    rows = db.get_top_divergences(rng["start"], rng["end"], limit=limit)
    return {
        "range": rng,
        "limit": max(limit, 1),
        "items": rows,
    }


@app.get("/reports/audit/operation/{operation_id}")
def operation_audit_report(operation_id: int) -> Dict[str, Any]:
    db = get_db()
    result = db.get_gold_operation_audit(operation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    return result


@app.post("/ai/multi-agent/analyze", response_model=MultiAgentResponse)
def multi_agent_analyze(request: MultiAgentRequest) -> MultiAgentResponse:
    db = get_db()
    live_context = db.build_multi_agent_live_context(operation_id=request.operation_id)
    merged_live_context = dict(request.live_context)
    merged_live_context.update(live_context)

    enriched_request = request.model_copy(update={"live_context": merged_live_context})
    response = run_multi_agent_orchestration(enriched_request)

    db.save_multi_agent_run(
        objective=enriched_request.objective,
        operation_id=enriched_request.operation_id,
        operation_kind=enriched_request.operation_kind,
        source_message_id=enriched_request.source_message_id,
        request_payload=enriched_request.model_dump(mode="json"),
        response_payload=response.model_dump(mode="json"),
    )
    return response


@app.get("/ai/multi-agent/runs")
def multi_agent_recent_runs(limit: int = 10) -> Dict[str, Any]:
    db = get_db()
    safe_limit = max(1, min(limit, 50))
    return {
        "limit": safe_limit,
        "items": db.get_recent_multi_agent_runs(limit=safe_limit),
    }


def _compute_ai_window_metrics(db: DatabaseClient, days: int) -> Dict[str, Any]:
    window_days = max(1, days)
    now_utc = datetime.now(timezone.utc)
    start_iso = (now_utc - timedelta(days=window_days)).isoformat()
    end_iso = now_utc.isoformat()

    runs = db.get_multi_agent_runs_range(start_iso, end_iso, limit=1000)
    learning_snapshot = db.get_transaction_learning_snapshot(lookback_days=window_days)
    alerts = db.get_risk_alerts(start_iso, end_iso)

    runs_with_risk = 0
    runs_with_fail_safe = 0
    total_risks = 0

    for run in runs:
        response_payload = cast(Dict[str, Any], run.get("response_payload") or {})
        risks = cast(List[Any], response_payload.get("risks") or [])
        transcript = cast(List[Any], response_payload.get("transcript") or [])

        if risks:
            runs_with_risk += 1
            total_risks += len(risks)

        has_fail_safe = False
        for item in transcript:
            if isinstance(item, dict):
                item_dict = cast(Dict[str, Any], item)
                if str(item_dict.get("role", "")).lower() == "fail-safe":
                    has_fail_safe = True
                    break
        if has_fail_safe:
            runs_with_fail_safe += 1

    total_runs = len(runs)
    risk_ratio = round(runs_with_risk / total_runs, 4) if total_runs else 0.0
    fail_safe_ratio = round(runs_with_fail_safe / total_runs, 4) if total_runs else 0.0
    avg_risks_per_run = round(total_risks / total_runs, 4) if total_runs else 0.0
    confidence = _compute_ai_confidence_score(
        total_samples=int(learning_snapshot.get("total_samples", 0) or 0),
        risk_ratio=risk_ratio,
        fail_safe_ratio=fail_safe_ratio,
        risk_alerts=len(alerts),
        total_runs=total_runs,
    )
    total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
    learning_phase = "seed"
    if total_samples >= 300:
        learning_phase = "advanced"
    elif total_samples >= 30:
        learning_phase = "learning_stable"

    return {
        "window_days": window_days,
        "range": {"start": start_iso, "end": end_iso},
        "runs": total_runs,
        "runs_with_risk": runs_with_risk,
        "runs_with_fail_safe": runs_with_fail_safe,
        "risk_ratio": risk_ratio,
        "fail_safe_ratio": fail_safe_ratio,
        "avg_risks_per_run": avg_risks_per_run,
        "risk_alerts": len(alerts),
        "learning_samples": total_samples,
        "learning_phase": learning_phase,
        "confidence_score": confidence["score"],
        "confidence_band": confidence["band"],
        "confidence_profile": confidence["profile"],
        "confidence_profile_mode": confidence["profile_mode"],
    }


def _trend_label(delta: float, good_when_negative: bool = True) -> str:
    eps = 0.0001
    if abs(delta) <= eps:
        return "stable"
    if good_when_negative:
        return "improving" if delta < 0 else "worsening"
    return "improving" if delta > 0 else "worsening"


def _phase_transition_label(from_phase: str, to_phase: str) -> str:
    order = {
        "seed": 0,
        "learning_stable": 1,
        "advanced": 2,
    }
    if from_phase == to_phase:
        return "stable"
    from_rank = order.get(from_phase, 0)
    to_rank = order.get(to_phase, 0)
    if to_rank > from_rank:
        return "maturing"
    if to_rank < from_rank:
        return "regressing"
    return "stable"


def _profile_transition_label(from_profile: str, to_profile: str) -> str:
    if from_profile == to_profile:
        return "stable"
    return f"{from_profile}_to_{to_profile}"


def _compute_ai_confidence_score(
    *,
    total_samples: int,
    risk_ratio: float,
    fail_safe_ratio: float,
    risk_alerts: int,
    total_runs: int,
) -> Dict[str, Any]:
    cfg = _get_ai_conf_config(total_samples)

    weight_total = float(cfg["weight_maturity"]) + float(cfg["weight_stability"]) + float(cfg["weight_alerts"])
    if weight_total <= 0:
        normalized_maturity = 0.45
        normalized_stability = 0.45
        normalized_alerts = 0.10
    else:
        normalized_maturity = float(cfg["weight_maturity"]) / weight_total
        normalized_stability = float(cfg["weight_stability"]) / weight_total
        normalized_alerts = float(cfg["weight_alerts"]) / weight_total

    sample_maturity = min(max(total_samples, 0) / float(cfg["samples_target"]), 1.0)
    stability_penalty = min(max((risk_ratio * float(cfg["risk_weight"])) + (fail_safe_ratio * float(cfg["failsafe_weight"])), 0.0), 1.0)
    stability = 1.0 - stability_penalty
    alerts_per_run = (risk_alerts / max(total_runs, 1)) if total_runs >= 0 else 0.0
    alert_pressure = min(max(alerts_per_run, 0.0), 1.0)

    score_raw = (
        (sample_maturity * normalized_maturity * 100.0)
        + (stability * normalized_stability * 100.0)
        + ((1.0 - alert_pressure) * normalized_alerts * 100.0)
    )
    score = max(0.0, min(100.0, score_raw))

    cut_excellent = max(1, min(100, int(cfg["band_excellent"])))
    cut_good = max(1, min(cut_excellent, int(cfg["band_good"])))
    cut_moderate = max(1, min(cut_good, int(cfg["band_moderate"])))

    band = "low"
    if score >= cut_excellent:
        band = "excellent"
    elif score >= cut_good:
        band = "good"
    elif score >= cut_moderate:
        band = "moderate"

    return {
        "score": round(score, 2),
        "band": band,
        "profile": str(cfg["profile_effective"]),
        "profile_mode": str(cfg["profile_setting"]),
        "components": {
            "sample_maturity": round(sample_maturity, 4),
            "stability": round(stability, 4),
            "alert_pressure": round(alert_pressure, 4),
        },
    }


def _parse_trend_windows_param(windows: str) -> List[int]:
    """Parse query string like '7,30,90' into sanitized unique sorted windows."""
    default_windows = [7, 30]
    if not windows.strip():
        return default_windows

    parsed: List[int] = []
    for raw in windows.split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if 1 <= value <= 365 and value not in parsed:
            parsed.append(value)

    if not parsed:
        return default_windows

    parsed.sort()
    return parsed[:6]


@app.get("/ai/health")
def ai_health_report() -> Dict[str, Any]:
    db = get_db()
    live_context = db.build_multi_agent_live_context(operation_id=None)
    learning_snapshot = cast(Dict[str, Any], live_context.get("learning_snapshot") or {})
    recent_runs = db.get_recent_multi_agent_runs(limit=50)

    total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
    ops_stats = cast(Dict[str, Any], learning_snapshot.get("operations") or {})
    operator_profiles = cast(Dict[str, Any], learning_snapshot.get("operator_profiles") or {})

    runs_24h = 0
    runs_with_risk = 0
    runs_with_fail_safe = 0
    now_utc = datetime.now(timezone.utc)

    for run in recent_runs:
        created_raw = str(run.get("criado_em") or "")
        response_payload = cast(Dict[str, Any], run.get("response_payload") or {})
        risks = cast(List[Any], response_payload.get("risks") or [])
        transcript = cast(List[Any], response_payload.get("transcript") or [])

        if risks:
            runs_with_risk += 1

        has_fail_safe = False
        for item in transcript:
            if isinstance(item, dict):
                item_dict = cast(Dict[str, Any], item)
                if str(item_dict.get("role", "")).lower() == "fail-safe":
                    has_fail_safe = True
                    break
        if has_fail_safe:
            runs_with_fail_safe += 1

        if created_raw:
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_hours = (now_utc - created_dt.astimezone(timezone.utc)).total_seconds() / 3600
                if age_hours <= 24:
                    runs_24h += 1
            except Exception:
                pass

    model_maturity = "seed"
    if total_samples >= 300:
        model_maturity = "advanced"
    elif total_samples >= 100:
        model_maturity = "stable"
    elif total_samples >= 30:
        model_maturity = "learning"

    risk_ratio = 0.0
    fail_safe_ratio = 0.0
    if recent_runs:
        risk_ratio = round(runs_with_risk / len(recent_runs), 4)
        fail_safe_ratio = round(runs_with_fail_safe / len(recent_runs), 4)

    risk_alerts_today = len(cast(List[Any], live_context.get("risk_alerts") or []))
    daily_operations = int(cast(Dict[str, Any], live_context.get("daily_summary") or {}).get("total_operacoes", 0) or 0)
    confidence = _compute_ai_confidence_score(
        total_samples=total_samples,
        risk_ratio=risk_ratio,
        fail_safe_ratio=fail_safe_ratio,
        risk_alerts=risk_alerts_today,
        total_runs=max(len(recent_runs), daily_operations),
    )

    readiness = "ok"
    readiness_reasons: List[str] = []
    if total_samples < 30:
        readiness = "attention"
        readiness_reasons.append("base_historica_baixa")
    if fail_safe_ratio > 0.05:
        readiness = "attention"
        readiness_reasons.append("falha_interna_agentes")
    if risk_ratio > 0.5:
        readiness = "attention"
        readiness_reasons.append("alta_taxa_alertas_risco")

    if not readiness_reasons:
        readiness_reasons.append("operacao_dentro_do_esperado")

    return {
        "status": readiness,
        "confidence": confidence,
        "readiness_reasons": readiness_reasons,
        "learning": {
            "maturity": model_maturity,
            "lookback_days": int(learning_snapshot.get("lookback_days", 0) or 0),
            "total_samples": total_samples,
            "operation_profiles": len(ops_stats),
            "operator_profiles": len(operator_profiles),
            "currency_mix": cast(Dict[str, Any], learning_snapshot.get("currency_mix") or {}),
        },
        "multi_agent": {
            "recent_runs": len(recent_runs),
            "runs_24h": runs_24h,
            "risk_ratio": risk_ratio,
            "fail_safe_ratio": fail_safe_ratio,
            "risk_alerts_today": risk_alerts_today,
        },
        "observability": {
            "top_divergences_today": len(cast(List[Any], live_context.get("top_divergences") or [])),
            "daily_operations": daily_operations,
        },
    }


@app.get("/ai/health/trends")
def ai_health_trends(windows: str = "7,30") -> Dict[str, Any]:
    db = get_db()

    selected_windows = _parse_trend_windows_param(windows)
    metrics_by_window: Dict[int, Dict[str, Any]] = {}
    for days in selected_windows:
        metrics_by_window[days] = _compute_ai_window_metrics(db, days=days)

    short_window = selected_windows[0]
    long_window = selected_windows[-1]
    short_metrics = metrics_by_window[short_window]
    long_metrics = metrics_by_window[long_window]

    risk_ratio_delta = round(short_metrics["risk_ratio"] - long_metrics["risk_ratio"], 4)
    fail_safe_delta = round(short_metrics["fail_safe_ratio"] - long_metrics["fail_safe_ratio"], 4)
    avg_risk_delta = round(short_metrics["avg_risks_per_run"] - long_metrics["avg_risks_per_run"], 4)
    alerts_delta = int(short_metrics["risk_alerts"]) - int(long_metrics["risk_alerts"])
    learning_delta = int(short_metrics["learning_samples"]) - int(long_metrics["learning_samples"])
    confidence_delta = round(float(short_metrics["confidence_score"]) - float(long_metrics["confidence_score"]), 4)

    trend_summary: Dict[str, Dict[str, Any]] = {
        "risk_ratio": {
            "delta": risk_ratio_delta,
            "trend": _trend_label(risk_ratio_delta, good_when_negative=True),
        },
        "fail_safe_ratio": {
            "delta": fail_safe_delta,
            "trend": _trend_label(fail_safe_delta, good_when_negative=True),
        },
        "avg_risks_per_run": {
            "delta": avg_risk_delta,
            "trend": _trend_label(avg_risk_delta, good_when_negative=True),
        },
        "risk_alerts": {
            "delta": alerts_delta,
            "trend": _trend_label(float(alerts_delta), good_when_negative=True),
        },
        "learning_samples": {
            "delta": learning_delta,
            "trend": _trend_label(float(learning_delta), good_when_negative=False),
        },
        "confidence_score": {
            "delta": confidence_delta,
            "trend": _trend_label(confidence_delta, good_when_negative=False),
        },
        "learning_phase": {
            "from": long_metrics["learning_phase"],
            "to": short_metrics["learning_phase"],
            "trend": _phase_transition_label(
                str(long_metrics["learning_phase"]),
                str(short_metrics["learning_phase"]),
            ),
            "transition": f"{long_metrics['learning_phase']} -> {short_metrics['learning_phase']}",
        },
        "confidence_profile": {
            "from": long_metrics["confidence_profile"],
            "to": short_metrics["confidence_profile"],
            "trend": _profile_transition_label(
                str(long_metrics["confidence_profile"]),
                str(short_metrics["confidence_profile"]),
            ),
            "transition": f"{long_metrics['confidence_profile']} -> {short_metrics['confidence_profile']}",
        },
    }

    windows_payload: Dict[str, Any] = {}
    for days in selected_windows:
        windows_payload[f"last_{days}_days"] = metrics_by_window[days]

    return {
        "selected_windows": selected_windows,
        "comparison": {
            "short_window_days": short_window,
            "long_window_days": long_window,
            "short_learning_phase": short_metrics["learning_phase"],
            "long_learning_phase": long_metrics["learning_phase"],
            "short_confidence_profile": short_metrics["confidence_profile"],
            "long_confidence_profile": long_metrics["confidence_profile"],
        },
        "windows": windows_payload,
        "trend_summary": trend_summary,
    }


def _processar_webhook(
    payload: WhatsAppWebhookPayload,
    db: DatabaseClient,
    provider_message_id: Optional[str],
) -> Dict[str, Any]:
    remetente = payload.remetente.strip()
    mensagem = payload.mensagem.strip()
    raw_ai_data: Dict[str, Any] = {}
    usuario = db.get_usuario_by_telefone(remetente)
    mensagem_norm = _normalize_text(mensagem)

    if not usuario:
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            erro="Remetente não autorizado",
        )
        raise HTTPException(status_code=403, detail="Remetente não autorizado.")

    session = _get_session(db, remetente)
    if session:
        estado = str(session.get("estado", ""))
        if estado in _GUIDED_FLOW_STATES:
            if _should_reset_guided_session_for_message(mensagem):
                _clear_session(db, remetente)
                session = None
                estado = ""
            elif estado != "await_resume_confirmacao" and _is_guided_session_stale(session):
                if mensagem_norm in {"cancelar", "cancela", "cancel", "parar", "sair"}:
                    _clear_session(db, remetente)
                    return {
                        "mensagem": "Certo, parei por aqui. Quando quiser retomar, me diga compra, venda ou descreva a operacao do seu jeito.",
                        "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
                    }

                idle_min = _guided_session_idle_minutes(session) or _GUIDED_SESSION_IDLE_MINUTES
                contexto_atual = dict(session.get("contexto", {}))
                _save_session(
                    db,
                    remetente,
                    "await_resume_confirmacao",
                    {
                        "estado_anterior": estado,
                        "contexto_anterior": contexto_atual,
                    },
                )
                return {
                    "mensagem": (
                        f"Ficamos {idle_min} minutos sem conversar. "
                        "Quer continuar de onde parou ou prefere cancelar esse atendimento? "
                        "Pode responder: continuar ou cancelar."
                    ),
                    "dados": {"etapa": "await_resume_confirmacao", "idle_minutos": idle_min},
                }

            # If user sends a fresh operation sentence, reset stale flow and re-interpret.
            if _should_reset_guided_session_for_message(mensagem):
                _clear_session(db, remetente)
            else:
                active_session = cast(Dict[str, Any], session)
                return _process_guided_flow(remetente, mensagem, db, active_session)

    session = _get_session(db, remetente)
    if session:
        estado = str(session.get("estado", ""))
        if estado in _GUIDED_FLOW_STATES:
            return _process_guided_flow(remetente, mensagem, db, session)

    maybe_start = _start_guided_flow_if_requested(remetente, mensagem, db, provider_message_id)
    if maybe_start:
        return maybe_start

    if _is_greeting(mensagem) and _needs_name_onboarding(usuario):
        _save_session(db, remetente, "await_nome_usuario", {"source": "onboarding"})
        return {
            "mensagem": "Olá. Para começar, informe seu nome.",
            "dados": {"etapa": "await_nome_usuario"},
        }

    command_response = _try_handle_whatsapp_commands(db, usuario, remetente, mensagem)
    if command_response is not None:
        return command_response

    try:
        raw_ai_data = extract_message_data(mensagem)
        ai_data = AIExtractedData.model_validate(raw_ai_data)
    except AIServiceError as exc:
        logger.warning("Falha ao extrair dados da IA; aplicando fallback seguro")
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            erro=str(exc),
        )
        ai_data = AIExtractedData(
            intencao="conversar",
            ativo=None,
            quantidade=None,
            valor_informado=None,
            resposta=(
                "Não foi possível interpretar a mensagem. "
                "Tente: 'compra', 'venda', 'caixa', 'extrato' ou 'taxa ouro 70.00'."
            ),
        )
    except ValidationError as exc:
        logger.warning("Payload da IA inválido; aplicando fallback seguro")
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            contexto={"ia_payload": raw_ai_data},
            erro=str(exc),
        )
        ai_data = AIExtractedData(
            intencao="conversar",
            ativo=None,
            quantidade=None,
            valor_informado=None,
            resposta=(
                "Dados insuficientes. "
                "Informe o ativo e a quantidade, por exemplo: 'venda ouro 3g'."
            ),
        )

    intencao = ai_data.intencao
    ativo_extraido = ai_data.ativo

    if intencao == "conversar":
        nome_usuario = str(usuario.get("nome") or "").strip()
        keep_menu_state = False
        if _is_help_menu_request(mensagem):
            resposta = _build_whatsapp_checklist_menu()
            _save_session(
                remetente=remetente,
                db=db,
                estado="await_menu_option",
                contexto={"origem": "menu"},
            )
            keep_menu_state = True
        else:
            resposta = ai_data.resposta or (
                "Posso ajudar com operações de ouro, câmbio e consulta de caixa.\n"
                "Digite 'menu' para ver as opções."
            )

        if _is_greeting(mensagem) and nome_usuario:
            resposta = (
                f"Olá, {nome_usuario}.\n"
                "Como posso ajudar?\n"
                "Digite 'menu' para ver as opções."
            )
        response_payload: Dict[str, Any] = {
            "mensagem": resposta,
            "dados": {"intencao": intencao},
        }
        if not keep_menu_state:
            _save_session(
                db=db,
                remetente=remetente,
                estado="conversando",
                contexto={"ultima_mensagem": mensagem, "ultima_intencao": intencao},
            )
        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida=mensagem,
            resposta_enviada=resposta,
            contexto={"intencao": intencao},
        )
        return response_payload

    if intencao == "consultar_relatorio":
        requested_currency = _extract_caixa_currency(mensagem)
        if requested_currency:
            day = _build_day_range(None)
            response_payload = _build_caixa_detail_response(
                db,
                requested_currency,
                day["start"],
                day["end"],
                f"Hoje ({day['date']})",
            )
            _clear_session(db, remetente)
        else:
            response_payload = _build_caixa_response(db, requested_currency=requested_currency)
            _save_session(
                db=db,
                remetente=remetente,
                estado="await_caixa_detalhe",
                contexto={"source": "caixa_summary"},
            )
        resposta = response_payload["mensagem"]
        day = {"date": str(response_payload["dados"].get("date", ""))}
        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida=mensagem,
            resposta_enviada=resposta,
            contexto={"intencao": intencao, "date": day["date"]},
        )
        return response_payload

    nome_ativo = normalize_ativo_nome(ativo_extraido or "")
    ativo = db.get_ativo_by_nome(nome_ativo)

    if not ativo:
        raise HTTPException(status_code=404, detail="Ativo não encontrado")

    ativo_id = int(ativo["id"])

    if intencao == "registrar_operacao":
        quantidade = parse_decimal(ai_data.quantidade, "quantidade")
        if quantidade <= 0:
            raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

        tipo_operacao = infer_tipo_operacao(mensagem)
        valor_informado = ai_data.valor_informado

        contexto: Dict[str, Any] = {
            "ativo_id": ativo_id,
            "nome_ativo": ativo["nome"],
            "quantidade": str(quantidade),
            "tipo_operacao": tipo_operacao,
            "source_message_id": provider_message_id,
        }

        # Se o preço já foi informado, pula direto para perguntar moeda
        if valor_informado is not None and valor_informado > 0:
            cotacao = parse_decimal(valor_informado, "valor_informado")
            total_usd = money(quantidade * cotacao)
            contexto["cotacao_usd"] = str(cotacao)
            contexto["total_usd"] = str(total_usd)
            db.save_conversation_session(
                remetente=remetente,
                estado="await_moeda_simples",
                contexto=contexto,
            )
            return {
                "mensagem": "Em qual moeda foi pago?\nUSD / EUR / SRD / BRL",
                "dados": {"etapa": "await_moeda_simples"},
            }

        # Senão, pede o preço
        db.save_conversation_session(
            remetente=remetente,
            estado="await_preco_simples",
            contexto=contexto,
        )

        operacao_texto = {
            "compra": "compra",
            "venda": "venda",
            "cambio": "câmbio",
        }.get(tipo_operacao, "operação")

        return {
            "mensagem": f"Informe o preço por grama em USD ({operacao_texto} de {quantidade}g).",
            "dados": {"etapa": "await_preco_simples"},
        }

    raise HTTPException(status_code=400, detail=f"Intenção não suportada: {intencao}")


@app.post("/operations/{operation_id}/edit")
async def edit_operation(
    operation_id: int,
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
) -> Dict[str, Any]:
    """Edit an operation (only by the operator who created it)."""
    token = x_webhook_token or request.query_params.get("token")
    validate_webhook_token(str(token) if token is not None else None)
    db = get_db()

    transacao = (
        db.client.table("transacoes")
        .select("*")
        .eq("id", operation_id)
        .limit(1)
        .execute()
    )
    rows = cast(List[Dict[str, Any]], transacao.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    body = await request.json()
    
    # Allow editing: quantidade, cotacao_usada, moeda_liquidacao, valor_moeda
    update_payload: Dict[str, Any] = {}
    if "quantidade" in body:
        update_payload["quantidade"] = str(body["quantidade"])
    if "cotacao_usada" in body:
        update_payload["cotacao_usada"] = str(body["cotacao_usada"])
    if "moeda_liquidacao" in body:
        update_payload["moeda_liquidacao"] = str(body["moeda_liquidacao"])
    if "valor_moeda" in body:
        update_payload["valor_moeda"] = str(body["valor_moeda"])

    if update_payload:
        db.client.table("transacoes").update(update_payload).eq("id", operation_id).execute()
        _invalidate_operation_related_view_caches()

    return {
        "mensagem": f"✅ Operação OP-{operation_id} editada com sucesso",
        "dados": {"id": operation_id, "updated_fields": list(update_payload.keys())},
    }


@app.delete("/operations/{operation_id}")
async def delete_operation(
    operation_id: int,
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
) -> Dict[str, Any]:
    """Delete/cancel an operation."""
    token = x_webhook_token or request.query_params.get("token")
    validate_webhook_token(str(token) if token is not None else None)
    db = get_db()
    kind = str(request.query_params.get("kind") or "transacao").strip().lower()

    if kind == "gold":
        ok = db.cancel_gold_transaction(operation_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Operação guiada não encontrada")
        _invalidate_operation_related_view_caches()
        return {
            "mensagem": f"✅ Operação GT-{operation_id} cancelada",
            "dados": {"id": operation_id, "status": "cancelada", "kind": "gold"},
        }

    transacao = (
        db.client.table("transacoes")
        .select("*")
        .eq("id", operation_id)
        .limit(1)
        .execute()
    )
    rows = cast(List[Dict[str, Any]], transacao.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    # Mark as cancelled instead of deleting
    db.client.table("transacoes").update({"status": "cancelada"}).eq("id", operation_id).execute()
    _invalidate_operation_related_view_caches()

    return {
        "mensagem": f"✅ Operação OP-{operation_id} cancelada",
        "dados": {"id": operation_id, "status": "cancelada"},
    }
