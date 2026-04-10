import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, cast

import requests


def _normalize_whatsapp_to(
    raw_phone: str,
    *,
    normalize_user_phone: Callable[[str], str],
) -> str:
    normalized = normalize_user_phone(raw_phone)
    return f"whatsapp:{normalized}" if normalized else ""


def _send_outbound_whatsapp_alert(
    phone: str,
    message: str,
    *,
    normalize_whatsapp_to: Callable[[str], str],
    logger: logging.Logger,
) -> bool:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = str(os.getenv("TWILIO_WHATSAPP_FROM") or "").strip()
    to_number = normalize_whatsapp_to(phone)
    if not account_sid or not auth_token or not from_number or not to_number:
        logger.warning("Monitor de lotes sem credenciais completas do Twilio; alerta nao enviado.")
        return False
    if not from_number.startswith("whatsapp:"):
        from_number = f"whatsapp:{from_number}"

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={"From": from_number, "To": to_number, "Body": message},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Falha ao enviar alerta outbound do lote via Twilio: %s", exc)
        return False


def _build_lot_alert_message(lot: Dict[str, Any], signal: Dict[str, Any], market_trend: Dict[str, Any]) -> str:
    return (
        "ALERTA DE LOTE DE OURO\n"
        f"Lote: GT-{lot.get('source_transaction_id', lot.get('source_id', '-'))}\n"
        f"Teor: {lot.get('teor', '-')}%\n"
        f"Saldo: {lot.get('remaining_grams', '0')} g\n"
        f"Mercado: USD {lot.get('market_unit_usd', '0')}/g\n"
        f"Lucro aberto: USD {lot.get('unrealized_pnl_usd', '0')} ({signal.get('profit_pct', '0')}%)\n"
        f"Sinal: {str(signal.get('status') or 'aguardar').replace('_', ' ').title()}\n"
        f"Leitura: {market_trend.get('summary', '-')}.\n"
        f"Motivo: {signal.get('reason', '-')}"
    )


def _run_lot_monitor_cycle(
    *,
    get_db: Callable[[], Any],
    get_market_snapshot: Callable[[], Dict[str, str]],
    build_market_trend_context: Callable[[], Dict[str, Any]],
    build_open_lot_market_context: Callable[[List[Dict[str, Any]], Dict[str, str]], Dict[str, Any]],
    build_lot_sell_signal: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    extract_lot_monitor_config: Callable[[Dict[str, Any]], Dict[str, Any]],
    build_lot_alert_message: Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], str],
    send_outbound_whatsapp_alert: Callable[[str, str], bool],
    logger: logging.Logger,
) -> None:
    db = get_db()
    inventory = db.get_gold_inventory_status(open_only=True)
    if not inventory.get("has_any_lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status(open_only=True)
    market_snapshot = get_market_snapshot()
    market_trend = build_market_trend_context()
    lot_market_context = build_open_lot_market_context(cast(List[Dict[str, Any]], inventory.get("open_lots") or []), market_snapshot)
    for lot in cast(List[Dict[str, Any]], lot_market_context.get("lots") or []):
        signal = build_lot_sell_signal(lot, market_trend)
        if not signal.get("should_alert"):
            continue
        monitor_cfg = extract_lot_monitor_config(lot)
        notify_phone = str(signal.get("notify_phone") or "")
        if not notify_phone:
            continue
        if str(monitor_cfg.get("last_alert_signature") or "") == str(signal.get("alert_signature") or ""):
            continue
        alert_message = build_lot_alert_message(lot, signal, market_trend)
        if not send_outbound_whatsapp_alert(notify_phone, alert_message):
            continue
        updated_monitor = {
            **monitor_cfg,
            "enabled": bool(signal.get("enabled")),
            "notify_phone": notify_phone,
            "target_price_usd": signal.get("target_price_usd"),
            "min_profit_pct": signal.get("min_profit_pct"),
            "last_alert_signature": signal.get("alert_signature"),
            "last_alert_status": signal.get("status"),
            "last_alert_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            db.update_gold_inventory_lot_monitor(int(lot.get("id") or 0), updated_monitor)
        except Exception as exc:
            logger.warning("Falha ao persistir estado do monitor do lote %s: %s", lot.get("id"), exc)


def _lot_monitor_worker(
    *,
    stop_event: threading.Event,
    interval_seconds: int,
    run_lot_monitor_cycle: Callable[[], None],
    logger: logging.Logger,
) -> None:
    while not stop_event.is_set():
        try:
            run_lot_monitor_cycle()
        except Exception as exc:
            logger.warning("Falha no ciclo do monitor de lotes: %s", exc)
        stop_event.wait(interval_seconds)