from types import SimpleNamespace
from typing import Any, Dict

from app.multi_agent_system import MultiAgentRequest, run_multi_agent_orchestration
from app.services import guided_flow_confirmation as guided_flow_confirmation_service
from app.services import guided_flow_entry as guided_flow_entry_service
from app.services import guided_flow_navigation as guided_flow_navigation_service
from app.services import guided_flow_payments as guided_flow_payments_service
from app.services import guided_flow_runtime as guided_flow_runtime_service
from app.services import guided_flow_setup as guided_flow_setup_service
from app.services import guided_flow_summary as guided_flow_summary_service
from app.services import guided_flow_tail as guided_flow_tail_service
from app.services import guided_navigation_runtime as guided_navigation_runtime_service
from app.services import bank_accounts_ui as bank_accounts_ui_service
from app.services import multi_agent_review as multi_agent_review_service
from app.services import operation_persistence as operation_persistence_service
from app.services import operation_risk as operation_risk_service
from app.services import runtime_saas_context as runtime_saas_context_service
from app.services import runtime_saas_document as runtime_saas_document_service
from app.services import runtime_saas_layout as runtime_saas_layout_service
from app.services import runtime_saas_operation_page as runtime_saas_operation_page_service
from app.services import runtime_saas_pages as runtime_saas_pages_service
from app.services import whatsapp_caixa_details as whatsapp_caixa_details_service
from app.services import whatsapp_commands as whatsapp_commands_service
from app.services import whatsapp_message_patterns as whatsapp_message_patterns_service
from app.services import whatsapp_reports as whatsapp_reports_service
from app.services import whatsapp_sessions as whatsapp_sessions_service
from app.services import whatsapp_transactions as whatsapp_transactions_service
from app.services import whatsapp_webhook_orchestrator as whatsapp_webhook_orchestrator_service


def build_app_composition_support_helpers(
    *,
    money: Any,
    session_cache: Dict[str, Dict[str, Any]],
    guided_session_idle_minutes: int,
    guided_flow_states: Any,
    runtime_support_helpers: Any,
    guided_flow_fx_helpers: Any,
    inventory_metric_helpers: Any,
    multi_agent_auto_enabled: bool,
    risk_diff_limit_usd: Any,
    multi_agent_auto_min_usd: Any,
    multi_agent_auto_min_weight_grams: Any,
    logger: Any,
) -> SimpleNamespace:
    runtime_saas_layout_helpers = runtime_saas_layout_service.build_runtime_saas_layout_helpers(
        money=money,
    )
    bank_accounts_ui_helpers = bank_accounts_ui_service.build_bank_accounts_ui_helpers()
    runtime_saas_page_helpers = runtime_saas_pages_service.build_runtime_saas_page_helpers()
    runtime_saas_context_helpers = runtime_saas_context_service.build_runtime_saas_context_helpers()
    runtime_saas_document_helpers = runtime_saas_document_service.build_runtime_saas_document_helpers()
    runtime_saas_operation_page_helpers = runtime_saas_operation_page_service.build_runtime_saas_operation_page_helpers()
    operation_persistence_helpers = operation_persistence_service.build_operation_persistence_helpers()

    guided_flow_tail_helpers = guided_flow_tail_service.build_guided_flow_tail_helpers()
    guided_flow_confirmation_helpers = guided_flow_confirmation_service.build_guided_flow_confirmation_helpers()
    guided_flow_entry_helpers = guided_flow_entry_service.build_guided_flow_entry_helpers()
    guided_flow_payment_helpers = guided_flow_payments_service.build_guided_flow_payment_helpers()
    guided_flow_setup_helpers = guided_flow_setup_service.build_guided_flow_setup_helpers()
    guided_flow_runtime_helpers = guided_flow_runtime_service.build_guided_flow_runtime_helpers()
    guided_flow_navigation_helpers = guided_flow_navigation_service.build_guided_flow_navigation_helpers()
    guided_flow_summary_helpers = guided_flow_summary_service.build_guided_flow_summary_helpers()

    whatsapp_command_helpers = whatsapp_commands_service.build_whatsapp_command_helpers()
    whatsapp_caixa_detail_helpers = whatsapp_caixa_details_service.build_whatsapp_caixa_detail_helpers()
    whatsapp_message_pattern_helpers = whatsapp_message_patterns_service.build_whatsapp_message_pattern_helpers(
        normalize_text=runtime_support_helpers.normalize_text,
    )
    whatsapp_report_helpers = whatsapp_reports_service.build_whatsapp_report_helpers()
    whatsapp_session_helpers = whatsapp_sessions_service.build_whatsapp_session_helpers(
        session_cache=session_cache,
        guided_session_idle_minutes=guided_session_idle_minutes,
    )
    whatsapp_transaction_helpers = whatsapp_transactions_service.build_whatsapp_transaction_helpers()
    whatsapp_webhook_orchestrator_helpers = whatsapp_webhook_orchestrator_service.build_whatsapp_webhook_orchestrator_helpers()

    guided_navigation_runtime_helpers = guided_navigation_runtime_service.build_guided_navigation_runtime_helpers(
        guided_flow_navigation_helpers=guided_flow_navigation_helpers,
        normalize_text=runtime_support_helpers.normalize_text,
        save_session=whatsapp_session_helpers.save_session,
        build_cambio_prompt=guided_flow_fx_helpers.build_cambio_prompt,
    )
    operation_risk_helpers = operation_risk_service.build_operation_risk_helpers(
        money=money,
        build_fifo_inventory_lots=lambda transactions: inventory_metric_helpers.build_fifo_inventory_lots(transactions),
        preview_fifo_sale_consumption=lambda lots, peso: inventory_metric_helpers.preview_fifo_sale_consumption(lots, peso),
        format_caixa_movement=runtime_support_helpers.format_caixa_movement,
    )
    multi_agent_review_helpers = multi_agent_review_service.build_multi_agent_review_helpers(
        multi_agent_auto_enabled=multi_agent_auto_enabled,
        risk_diff_limit_usd=risk_diff_limit_usd,
        multi_agent_auto_min_usd=multi_agent_auto_min_usd,
        multi_agent_auto_min_weight_grams=multi_agent_auto_min_weight_grams,
        money=money,
        multi_agent_request_cls=MultiAgentRequest,
        run_multi_agent_orchestration=run_multi_agent_orchestration,
        logger=logger,
    )

    return SimpleNamespace(
        runtime_saas_layout_helpers=runtime_saas_layout_helpers,
        bank_accounts_ui_helpers=bank_accounts_ui_helpers,
        runtime_saas_page_helpers=runtime_saas_page_helpers,
        runtime_saas_context_helpers=runtime_saas_context_helpers,
        runtime_saas_document_helpers=runtime_saas_document_helpers,
        runtime_saas_operation_page_helpers=runtime_saas_operation_page_helpers,
        operation_persistence_helpers=operation_persistence_helpers,
        guided_flow_tail_helpers=guided_flow_tail_helpers,
        guided_flow_confirmation_helpers=guided_flow_confirmation_helpers,
        guided_flow_entry_helpers=guided_flow_entry_helpers,
        guided_flow_payment_helpers=guided_flow_payment_helpers,
        guided_flow_setup_helpers=guided_flow_setup_helpers,
        guided_flow_runtime_helpers=guided_flow_runtime_helpers,
        guided_flow_navigation_helpers=guided_flow_navigation_helpers,
        guided_flow_summary_helpers=guided_flow_summary_helpers,
        whatsapp_command_helpers=whatsapp_command_helpers,
        whatsapp_caixa_detail_helpers=whatsapp_caixa_detail_helpers,
        whatsapp_message_pattern_helpers=whatsapp_message_pattern_helpers,
        whatsapp_report_helpers=whatsapp_report_helpers,
        whatsapp_session_helpers=whatsapp_session_helpers,
        whatsapp_transaction_helpers=whatsapp_transaction_helpers,
        whatsapp_webhook_orchestrator_helpers=whatsapp_webhook_orchestrator_helpers,
        guided_navigation_runtime_helpers=guided_navigation_runtime_helpers,
        operation_risk_helpers=operation_risk_helpers,
        multi_agent_review_helpers=multi_agent_review_helpers,
        guided_flow_states=guided_flow_states,
    )