from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def build_runtime_saas_helpers(
    *,
    statements_service: Any,
    operation_drafts_service: Any,
    clients_service: Any,
    suppliers_service: Any,
    receipts_service: Any,
    dashboard_trends_service: Any,
    normalize_text: Callable[[str], str],
    build_recent_fx_map: Callable[[Any], Dict[str, str]],
    ai_extracted_data_cls: Any,
    dashboard_default_form_values: Callable[[Dict[str, Any]], Dict[str, str]],
    infer_tipo_operacao: Callable[[str], str],
    parse_decimal_from_text: Callable[[str, str], Any],
    format_decimal_for_form: Callable[[Any], str],
    payment_input_to_usd: Callable[[str, Any, Any], Any],
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
    build_fornecedor_lookup_meta: Callable[[Dict[str, Any]], str],
    format_caixa_movement: Callable[[str, Any], str],
    render_bank_account_section: Callable[..., str],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_saas_receipt_context_cache_key: Callable[[int], str],
    get_saas_receipt_context_cached: Callable[[str], Optional[Dict[str, Any]]],
    set_saas_receipt_context_cached: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    build_saas_statement_context_cache_key: Callable[[str, str], str],
    get_saas_statement_context_cached: Callable[[str], Optional[Dict[str, Any]]],
    set_saas_statement_context_cached: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    build_extrato_response: Callable[..., Dict[str, Any]],
    format_datetime_pt_br: Callable[[Any], str],
) -> SimpleNamespace:
    def build_statement_summary(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
        return statements_service._build_statement_summary(transactions)

    def build_statement_summary_for_window(
        transactions: List[Dict[str, Any]],
        start_iso: str,
        end_iso: str,
    ) -> Dict[str, Any]:
        return statements_service._build_statement_summary_for_window(transactions, start_iso, end_iso)

    def build_operation_draft_from_message(db: Any, session_user: Dict[str, Any], message: str) -> Dict[str, Any]:
        return operation_drafts_service._build_operation_draft_from_message(
            db,
            session_user,
            message,
            normalize_text=normalize_text,
            build_recent_fx_map=build_recent_fx_map,
            ai_extracted_data_cls=ai_extracted_data_cls,
            dashboard_default_form_values=dashboard_default_form_values,
            infer_tipo_operacao=infer_tipo_operacao,
            parse_decimal_from_text=parse_decimal_from_text,
            format_decimal_for_form=format_decimal_for_form,
            payment_input_to_usd=payment_input_to_usd,
            build_cliente_lookup_meta=build_cliente_lookup_meta,
        )

    def build_saas_clients_context(
        db: Any,
        selected_client_id: Optional[int] = None,
        search_term: Optional[str] = None,
    ) -> Dict[str, Any]:
        return clients_service._build_saas_clients_context(db, selected_client_id=selected_client_id, search_term=search_term)

    def render_saas_clients_page(clients_context: Dict[str, Any], values: Dict[str, str]) -> str:
        return clients_service._render_saas_clients_page(
            clients_context,
            values,
            build_cliente_lookup_meta=build_cliente_lookup_meta,
            format_caixa_movement=format_caixa_movement,
            render_bank_account_section=render_bank_account_section,
        )

    def build_saas_suppliers_context(
        db: Any,
        selected_supplier_id: Optional[int] = None,
        search_term: Optional[str] = None,
    ) -> Dict[str, Any]:
        return suppliers_service._build_saas_suppliers_context(db, selected_supplier_id=selected_supplier_id, search_term=search_term)

    def render_saas_suppliers_page(suppliers_context: Dict[str, Any], values: Dict[str, str]) -> str:
        return suppliers_service._render_saas_suppliers_page(
            suppliers_context,
            values,
            build_fornecedor_lookup_meta=build_fornecedor_lookup_meta,
            format_caixa_movement=format_caixa_movement,
            render_bank_account_section=render_bank_account_section,
        )

    def build_saas_statement_context(db: Any, start_date: Optional[str], end_date: Optional[str]) -> Dict[str, Any]:
        return statements_service._build_saas_statement_context(
            db,
            start_date,
            end_date,
            build_day_range=build_day_range,
            build_cache_key=build_saas_statement_context_cache_key,
            get_cached_context=get_saas_statement_context_cached,
            set_cached_context=set_saas_statement_context_cached,
            build_extrato_response=build_extrato_response,
        )

    def build_saas_dashboard_trend(transactions: List[Dict[str, Any]], days: int = 7) -> List[Dict[str, Any]]:
        return dashboard_trends_service._build_saas_dashboard_trend(transactions, days=days)

    def render_saas_trend_chart(points: List[Dict[str, Any]]) -> str:
        return dashboard_trends_service._render_saas_trend_chart(points)

    def build_gold_receipt_context(db: Any, operation_id: int) -> Dict[str, Any]:
        return receipts_service._build_gold_receipt_context(
            db,
            operation_id,
            build_cache_key=build_saas_receipt_context_cache_key,
            get_cached_context=get_saas_receipt_context_cached,
            set_cached_context=set_saas_receipt_context_cached,
            format_datetime_pt_br=format_datetime_pt_br,
        )

    def render_saas_receipt_html(receipt: Dict[str, Any], pdf_url: str, back_url: str) -> str:
        return receipts_service._render_saas_receipt_html(
            receipt,
            pdf_url,
            back_url,
            build_cliente_lookup_meta=build_cliente_lookup_meta,
        )

    def build_gold_receipt_pdf(receipt: Dict[str, Any], pdf_url: str) -> bytes:
        return receipts_service._build_gold_receipt_pdf(
            receipt,
            pdf_url,
            build_cliente_lookup_meta=build_cliente_lookup_meta,
        )

    return SimpleNamespace(
        build_statement_summary=build_statement_summary,
        build_statement_summary_for_window=build_statement_summary_for_window,
        build_operation_draft_from_message=build_operation_draft_from_message,
        build_saas_clients_context=build_saas_clients_context,
        render_saas_clients_page=render_saas_clients_page,
        build_saas_suppliers_context=build_saas_suppliers_context,
        render_saas_suppliers_page=render_saas_suppliers_page,
        build_saas_statement_context=build_saas_statement_context,
        build_saas_dashboard_trend=build_saas_dashboard_trend,
        render_saas_trend_chart=render_saas_trend_chart,
        build_gold_receipt_context=build_gold_receipt_context,
        render_saas_receipt_html=render_saas_receipt_html,
        build_gold_receipt_pdf=build_gold_receipt_pdf,
    )