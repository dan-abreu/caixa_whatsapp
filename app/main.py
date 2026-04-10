from contextlib import asynccontextmanager
import os
import logging
import threading
from types import SimpleNamespace
from pathlib import Path
from decimal import Decimal
from typing import Any, Dict, Optional, cast
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.database import DatabaseClient, DatabaseError
from app.core.formatting import fx_rate, money
from app.services import app_market_runtime_setup as app_market_runtime_setup_service
from app.services import app_route_registration as app_route_registration_service
from app.services import app_runtime_foundation as app_runtime_foundation_service
from app.services import app_runtime_literals as app_runtime_literals_service
from app.services import dashboard_trends as dashboard_trends_service
from app.services import app_composition_runtime as app_composition_runtime_service
from app.services import app_composition_support as app_composition_support_service
from app.services import app_main_compat as app_main_compat_service
from app.services import runtime_http as runtime_http_service


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

logger = logging.getLogger("caixa_whatsapp")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
_STATIC_DIR = Path(__file__).with_name("static"); _STATIC_ASSET_VERSIONS: Dict[str, str] = {}
_SAAS_SESSION_COOKIE = os.getenv("SAAS_SESSION_COOKIE", "caixa_saas_session"); _SAAS_SESSION_TTL_SECONDS = int(os.getenv("SAAS_SESSION_TTL_SECONDS", "43200"))
_SAAS_COOKIE_SECURE = os.getenv("SAAS_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}; _SAAS_AUTH_USER_CACHE_TTL_SECONDS = int(os.getenv("SAAS_AUTH_USER_CACHE_TTL_SECONDS", "15"))
_SAAS_AUTH_USER_CACHE: Dict[str, Dict[str, Any]] = {}
_MARKET_ALERT_THRESHOLD_PCT = Decimal(os.getenv("MARKET_ALERT_THRESHOLD_PCT", "0.50")); _DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS = int(os.getenv("DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS", "15"))
_DASHBOARD_FRAGMENT_CACHE: Dict[str, Dict[str, Any]] = {}
_SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS", "15"))
_SAAS_STATEMENT_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}
_SAAS_RECENT_FX_CACHE_TTL_SECONDS = 15; _SAAS_RECENT_FX_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS", "30"))
_SAAS_RECEIPT_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}
_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS = float(os.getenv("SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS", "2"))
_SAAS_LOT_MONITOR_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS = int(os.getenv("REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS", "5")); _REPORT_INVENTORY_STATUS_CACHE: Dict[str, Any] = {"expires_at": None, "data": None}
_ADMIN_DASHBOARD_CACHE_TTL_SECONDS = int(os.getenv("ADMIN_DASHBOARD_CACHE_TTL_SECONDS", "5"))
_ADMIN_DASHBOARD_CACHE: Dict[str, Dict[str, Any]] = {}
_MARKET_STREAM_INTERVAL_SECONDS = float(os.getenv("MARKET_STREAM_INTERVAL_SECONDS", "1")); _LOT_MONITOR_INTERVAL_SECONDS = int(os.getenv("LOT_MONITOR_INTERVAL_SECONDS", "300"))
_LOT_MONITOR_STREAM_INTERVAL_SECONDS = float(os.getenv("LOT_MONITOR_STREAM_INTERVAL_SECONDS", "1"))
_LOT_MONITOR_ENABLED = os.getenv("LOT_MONITOR_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
_DASHBOARD_FRAGMENT_CACHE_KEY_PREFIX = "saas:fragment"
(
    _DASHBOARD_FRAGMENT_NEWS_NAME,
    _DASHBOARD_FRAGMENT_MONITORS_NAME,
    _DASHBOARD_FRAGMENT_INVENTORY_NAME,
    _DASHBOARD_FRAGMENT_TREND_NAME,
    _DASHBOARD_FRAGMENT_SUMMARY_NAME,
    _DASHBOARD_FRAGMENT_PENDING_CLOSINGS_NAME,
    _DASHBOARD_FRAGMENT_RECENT_OPERATIONS_NAME,
) = ("dashboard:news", "dashboard:monitors", "dashboard:inventory", "dashboard:trend", "dashboard:summary", "dashboard:pending-closings", "dashboard:recent-operations")
(_SAAS_STATEMENT_CONTEXT_CACHE_KEY_PREFIX, _SAAS_RECEIPT_CONTEXT_CACHE_KEY_PREFIX, _SAAS_LOT_MONITOR_SNAPSHOT_CACHE_KEY_PREFIX, _ADMIN_DASHBOARD_CACHE_KEY_PREFIX) = ("saas:statement", "saas:receipt", "saas:lot-monitor", "admin:dashboard")
_DB_INSTANCE: Optional[DatabaseClient] = None
_DB_INSTANCE_LOCK = threading.Lock()
_LOT_MONITOR_STOP = threading.Event(); _LOT_MONITOR_LOCK = threading.Lock()
_LOT_MONITOR_STATE: Dict[str, Any] = {"thread": None}
_market_runtime_helpers: Any = None

@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    if _market_runtime_helpers is not None:
        _market_runtime_helpers.start_lot_monitor_background()
    try:
        yield
    finally:
        if _market_runtime_helpers is not None:
            _market_runtime_helpers.stop_lot_monitor_background()

app = FastAPI(title="Caixa Inteligente WhatsApp API", version="1.0.0", lifespan=_app_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_runtime_http_helpers = runtime_http_service.build_runtime_http_helpers(
    static_asset_versions=_STATIC_ASSET_VERSIONS,
    static_dir=_STATIC_DIR,
    quote=quote,
    os_getenv=os.getenv,
    http_exception_cls=HTTPException,
)
app.middleware("http")(_runtime_http_helpers.add_performance_headers)
validate_webhook_token = _runtime_http_helpers.validate_webhook_token

def get_db() -> DatabaseClient:
    db_instance = cast(Optional[DatabaseClient], globals().get("_DB_INSTANCE"))
    if db_instance is not None:
        return db_instance
    try:
        with _DB_INSTANCE_LOCK:
            if globals().get("_DB_INSTANCE") is None:
                globals()["_DB_INSTANCE"] = DatabaseClient()
            return cast(DatabaseClient, globals()["_DB_INSTANCE"])
    except DatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

# Fallback de idempotência para ambiente sem migração aplicada.
_IDEMPOTENCY_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}

_RISK_DIFF_LIMIT_USD = Decimal(os.getenv("RISK_DIFF_LIMIT_USD", "250"))
_GUIDED_SESSION_IDLE_MINUTES = int(os.getenv("GUIDED_SESSION_IDLE_MINUTES", "5"))
_MULTI_AGENT_AUTO_ENABLED = os.getenv("MULTI_AGENT_AUTO_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
_MULTI_AGENT_AUTO_MIN_USD = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_USD", "500")); _MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS", "10"))
_runtime_foundation_helpers: SimpleNamespace = app_runtime_foundation_service.build_app_runtime_foundation(
    http_exception_cls=HTTPException,
    money=money,
    fx_rate=fx_rate,
    asset_url=_runtime_http_helpers.asset_url,
    saas_session_ttl_seconds=_SAAS_SESSION_TTL_SECONDS,
    saas_session_cookie=_SAAS_SESSION_COOKIE,
    saas_cookie_secure=_SAAS_COOKIE_SECURE,
    saas_auth_user_cache=_SAAS_AUTH_USER_CACHE,
    saas_auth_user_cache_ttl_seconds=_SAAS_AUTH_USER_CACHE_TTL_SECONDS,
    dashboard_fragment_cache=_DASHBOARD_FRAGMENT_CACHE,
    dashboard_fragment_cache_ttl_seconds=_DASHBOARD_FRAGMENT_CACHE_TTL_SECONDS,
    dashboard_fragment_cache_key_prefix=_DASHBOARD_FRAGMENT_CACHE_KEY_PREFIX,
    dashboard_fragment_monitors_name=_DASHBOARD_FRAGMENT_MONITORS_NAME,
    dashboard_fragment_inventory_name=_DASHBOARD_FRAGMENT_INVENTORY_NAME,
    dashboard_fragment_trend_name=_DASHBOARD_FRAGMENT_TREND_NAME,
    dashboard_fragment_summary_name=_DASHBOARD_FRAGMENT_SUMMARY_NAME,
    dashboard_fragment_pending_closings_name=_DASHBOARD_FRAGMENT_PENDING_CLOSINGS_NAME,
    dashboard_fragment_recent_operations_name=_DASHBOARD_FRAGMENT_RECENT_OPERATIONS_NAME,
    saas_statement_context_cache_key_prefix=_SAAS_STATEMENT_CONTEXT_CACHE_KEY_PREFIX,
    saas_statement_context_cache=_SAAS_STATEMENT_CONTEXT_CACHE,
    saas_statement_context_cache_ttl_seconds=_SAAS_STATEMENT_CONTEXT_CACHE_TTL_SECONDS,
    saas_recent_fx_cache=_SAAS_RECENT_FX_CACHE,
    saas_recent_fx_cache_ttl_seconds=_SAAS_RECENT_FX_CACHE_TTL_SECONDS,
    saas_receipt_context_cache_key_prefix=_SAAS_RECEIPT_CONTEXT_CACHE_KEY_PREFIX,
    saas_receipt_context_cache=_SAAS_RECEIPT_CONTEXT_CACHE,
    saas_receipt_context_cache_ttl_seconds=_SAAS_RECEIPT_CONTEXT_CACHE_TTL_SECONDS,
    saas_lot_monitor_snapshot_cache_key_prefix=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_KEY_PREFIX,
    saas_lot_monitor_snapshot_cache=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE,
    saas_lot_monitor_snapshot_cache_ttl_seconds=_SAAS_LOT_MONITOR_SNAPSHOT_CACHE_TTL_SECONDS,
    admin_dashboard_cache_key_prefix=_ADMIN_DASHBOARD_CACHE_KEY_PREFIX,
    inventory_status_cache=_REPORT_INVENTORY_STATUS_CACHE,
    inventory_status_cache_ttl_seconds=_REPORT_INVENTORY_STATUS_CACHE_TTL_SECONDS,
    admin_dashboard_cache=_ADMIN_DASHBOARD_CACHE,
    admin_dashboard_cache_ttl_seconds=_ADMIN_DASHBOARD_CACHE_TTL_SECONDS,
)
(
    _ai_conf_helpers, _runtime_support_helpers, _runtime_view_helpers, _runtime_saas_ui_helpers,
    _whatsapp_input_parser_helpers, _inventory_metric_helpers, _runtime_saas_auth_helpers, _guided_flow_fx_helpers,
    _runtime_saas_payment_helpers, _runtime_saas_date_helpers, _operation_rule_helpers, _runtime_saas_form_helpers,
    normalize_ativo_nome,
) = (
    _runtime_foundation_helpers.ai_conf_helpers, _runtime_foundation_helpers.runtime_support_helpers,
    _runtime_foundation_helpers.runtime_view_helpers, _runtime_foundation_helpers.runtime_saas_ui_helpers,
    _runtime_foundation_helpers.whatsapp_input_parser_helpers, _runtime_foundation_helpers.inventory_metric_helpers,
    _runtime_foundation_helpers.runtime_saas_auth_helpers, _runtime_foundation_helpers.guided_flow_fx_helpers,
    _runtime_foundation_helpers.runtime_saas_payment_helpers, _runtime_foundation_helpers.runtime_saas_date_helpers,
    _runtime_foundation_helpers.operation_rule_helpers, _runtime_foundation_helpers.runtime_saas_form_helpers,
    _runtime_foundation_helpers.normalize_ativo_nome,
)

_market_setup_helpers: SimpleNamespace = app_market_runtime_setup_service.build_app_market_runtime_setup(
    get_db=get_db,
    logger=logger,
    inventory_metric_helpers=_inventory_metric_helpers,
    runtime_support_helpers=_runtime_support_helpers,
    runtime_saas_form_helpers=_runtime_saas_form_helpers,
    runtime_view_helpers=_runtime_view_helpers,
    market_monitor_cards=app_runtime_literals_service.MARKET_MONITOR_CARDS,
    market_alert_threshold_pct=_MARKET_ALERT_THRESHOLD_PCT,
    lot_monitor_stop=_LOT_MONITOR_STOP,
    lot_monitor_interval_seconds=_LOT_MONITOR_INTERVAL_SECONDS,
    market_stream_interval_seconds=_MARKET_STREAM_INTERVAL_SECONDS,
    lot_monitor_stream_interval_seconds=_LOT_MONITOR_STREAM_INTERVAL_SECONDS,
    lot_monitor_enabled=_LOT_MONITOR_ENABLED,
    lot_monitor_lock=_LOT_MONITOR_LOCK,
    lot_monitor_state=_LOT_MONITOR_STATE,
    threading_module=threading,
)
_runtime_web_helpers, _market_runtime_helpers = _market_setup_helpers.runtime_web_helpers, _market_setup_helpers.market_runtime_helpers

_support_helpers: SimpleNamespace = app_composition_support_service.build_app_composition_support_helpers(
    money=money,
    session_cache=_SESSION_CACHE,
    guided_session_idle_minutes=_GUIDED_SESSION_IDLE_MINUTES,
    guided_flow_states=app_runtime_literals_service.GUIDED_FLOW_STATES,
    runtime_support_helpers=_runtime_support_helpers,
    guided_flow_fx_helpers=_guided_flow_fx_helpers,
    inventory_metric_helpers=_inventory_metric_helpers,
    multi_agent_auto_enabled=_MULTI_AGENT_AUTO_ENABLED,
    risk_diff_limit_usd=_RISK_DIFF_LIMIT_USD,
    multi_agent_auto_min_usd=_MULTI_AGENT_AUTO_MIN_USD,
    multi_agent_auto_min_weight_grams=_MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS,
    logger=logger,
)
_runtime_composition_helpers: SimpleNamespace = app_composition_runtime_service.build_app_composition_runtime_helpers(
    ai_extracted_data_cls=AIExtractedData,
    money=money,
    fx_rate=fx_rate,
    logger=logger,
    risk_diff_limit_usd=_RISK_DIFF_LIMIT_USD,
    supported_currencies=app_runtime_literals_service.MOEDAS_SUPORTADAS,
    guided_flow_states=app_runtime_literals_service.GUIDED_FLOW_STATES,
    guided_session_idle_limit=_GUIDED_SESSION_IDLE_MINUTES,
    dashboard_trends_service=dashboard_trends_service,
    runtime_support_helpers=_runtime_support_helpers,
    runtime_saas_date_helpers=_runtime_saas_date_helpers,
    runtime_saas_form_helpers=_runtime_saas_form_helpers,
    runtime_saas_ui_helpers=_runtime_saas_ui_helpers,
    runtime_saas_payment_helpers=_runtime_saas_payment_helpers,
    runtime_view_helpers=_runtime_view_helpers,
    runtime_http_helpers=_runtime_http_helpers,
    runtime_web_helpers=_runtime_web_helpers,
    inventory_metric_helpers=_inventory_metric_helpers,
    market_runtime_helpers=_market_runtime_helpers,
    guided_flow_fx_helpers=_guided_flow_fx_helpers,
    whatsapp_input_parser_helpers=_whatsapp_input_parser_helpers,
    operation_rule_helpers=_operation_rule_helpers,
    support_helpers=_support_helpers,
)
app_route_registration_service.register_app_route_bundles(
    app,
    get_db=get_db,
    auth_helpers=_runtime_saas_auth_helpers,
    runtime_saas_helpers=_runtime_composition_helpers.runtime_saas_helpers,
    market_runtime_helpers=_market_runtime_helpers,
    runtime_web_helpers=_runtime_web_helpers,
    runtime_view_helpers=_runtime_view_helpers,
    runtime_saas_date_helpers=_runtime_saas_date_helpers,
    runtime_saas_payment_helpers=_runtime_saas_payment_helpers,
    runtime_support_helpers=_runtime_support_helpers,
    reporting_runtime_helpers=_runtime_composition_helpers.reporting_runtime_helpers,
    saas_dashboard_page_helpers=_runtime_composition_helpers.saas_dashboard_page_helpers,
    runtime_saas_ui_helpers=_runtime_saas_ui_helpers,
    inventory_metric_helpers=_inventory_metric_helpers,
    ai_conf_helpers=_ai_conf_helpers,
    validate_webhook_token=validate_webhook_token,
    whatsapp_payload_cls=WhatsAppWebhookPayload,
    whatsapp_runtime_binding_helpers=_runtime_composition_helpers.whatsapp_runtime_binding_helpers,
    idempotency_cache=_IDEMPOTENCY_CACHE,
    market_cache_ttl_seconds=_market_setup_helpers.market_cache_ttl_seconds,
    dashboard_fragment_news_name=_DASHBOARD_FRAGMENT_NEWS_NAME,
    dashboard_fragment_monitors_name=_DASHBOARD_FRAGMENT_MONITORS_NAME,
    dashboard_fragment_inventory_name=_DASHBOARD_FRAGMENT_INVENTORY_NAME,
    dashboard_fragment_trend_name=_DASHBOARD_FRAGMENT_TREND_NAME,
    dashboard_fragment_summary_name=_DASHBOARD_FRAGMENT_SUMMARY_NAME,
    dashboard_fragment_pending_closings_name=_DASHBOARD_FRAGMENT_PENDING_CLOSINGS_NAME,
    dashboard_fragment_recent_operations_name=_DASHBOARD_FRAGMENT_RECENT_OPERATIONS_NAME,
    friendly_errors=app_runtime_literals_service.ERROS_AMIGAVEIS,
    logger=logger,
    ui_helpers=_runtime_saas_ui_helpers,
    runtime_saas_form_helpers=_runtime_saas_form_helpers,
    operation_rule_helpers=_operation_rule_helpers,
    operation_risk_helpers=_support_helpers.operation_risk_helpers,
    operation_runtime_helpers=_runtime_composition_helpers.operation_runtime_helpers,
    whatsapp_session_helpers=_support_helpers.whatsapp_session_helpers,
    money=money,
    guided_flow_states=app_runtime_literals_service.GUIDED_FLOW_STATES,
    saas_session_cookie=_SAAS_SESSION_COOKIE,
)
globals().update(
    app_main_compat_service.build_main_compat_exports(
        module_globals=globals(), runtime_composition_helpers=_runtime_composition_helpers, runtime_view_helpers=_runtime_view_helpers,
        market_runtime_helpers=_market_runtime_helpers, inventory_metric_helpers=_inventory_metric_helpers, guided_flow_fx_helpers=_guided_flow_fx_helpers,
        runtime_support_helpers=_runtime_support_helpers, whatsapp_input_parser_helpers=_whatsapp_input_parser_helpers, runtime_saas_payment_helpers=_runtime_saas_payment_helpers,
        runtime_saas_form_helpers=_runtime_saas_form_helpers, operation_rule_helpers=_operation_rule_helpers, support_helpers=_support_helpers,
        lot_monitor_stream_interval_seconds=_LOT_MONITOR_STREAM_INTERVAL_SECONDS,
    )
)


