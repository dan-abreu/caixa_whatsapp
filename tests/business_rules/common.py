import asyncio
import unittest
from decimal import Decimal

import app.main as main_module
from fastapi import HTTPException

from app.database import (
    DatabaseClient,
    _aggregate_cliente_movements,
    _aggregate_cliente_movements_by_client,
    _hash_web_pin,
    _verify_web_pin,
)
from app.main import (
    _MARKET_TICK_HISTORY,
    _build_admin_dashboard_html,
    _build_fechamento_status,
    _build_fifo_inventory_lots,
    _build_gold_caixa_metrics,
    _build_gold_caixa_metrics_from_pending_grams,
    _build_gold_receipt_context,
    _build_inventory_status_report_payload,
    _build_lot_monitor_snapshot_payload,
    _build_lot_sell_signal,
    _build_market_snapshot_from_rates,
    _build_market_trend_context,
    _build_open_lot_market_context,
    _build_operation_draft_from_message,
    _build_operation_lot_market_context,
    _build_saas_recent_fx_map,
    _build_saas_statement_context,
    _build_web_lot_ai_alert_summary,
    _build_web_lot_ai_alerts,
    _build_web_lot_monitor_entries,
    _derive_forma_pagamento_summary,
    _display_cambio_for_web_input,
    _extract_awesomeapi_gold_price,
    _extract_gold_api_xau_usd,
    _find_negative_caixa_balances,
    _invalidate_lot_monitor_snapshot_cache,
    _invalidate_receipt_context_cache,
    _invalidate_recent_fx_map_cache,
    _invalidate_reporting_cache,
    _invalidate_statement_context_cache,
    _lot_monitor_stream_events,
    _normalize_cambio_para_usd,
    _parse_decimal_from_text,
    _parse_google_news_feed,
    _parse_gold_trade_profile,
    _parse_operation_reference,
    _parse_web_payments_from_form,
    _preview_fifo_sale_consumption,
    _project_caixa_balances,
    _render_saas_dashboard_html,
    _should_reset_guided_session_for_message,
    _sum_open_fechamento_grams,
)
from app.services.runtime_saas_ui import build_runtime_saas_ui_helpers
from app.services.view_caches import (
    _invalidate_lot_monitor_snapshot_cache as _invalidate_lot_monitor_snapshot_cache_service,
    _invalidate_receipt_context_cache as _invalidate_receipt_context_cache_service,
    _invalidate_statement_context_cache as _invalidate_statement_context_cache_service,
)
from app.services.whatsapp_sessions import build_whatsapp_session_helpers


class BusinessRulesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _MARKET_TICK_HISTORY.clear()
        DatabaseClient._USUARIOS_WEB_PIN_SCHEMA_READY = None
        DatabaseClient._RUNTIME_CACHE = {}
        _invalidate_statement_context_cache()
        _invalidate_recent_fx_map_cache()
        _invalidate_receipt_context_cache()
        _invalidate_lot_monitor_snapshot_cache()
        _invalidate_reporting_cache()
