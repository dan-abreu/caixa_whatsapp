from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response


def register_saas_supplier_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    render_saas_login_html: Callable[..., str],
    render_saas_dashboard_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    build_saas_suppliers_context: Callable[..., Dict[str, Any]],
    request_form_dict: Callable[[Request], Any],
    parse_cliente_opening_balances: Callable[[Dict[str, str], str], Dict[str, Any]],
    parse_decimal_web_field: Callable[[str, str], Decimal],
    normalize_saas_page: Callable[[Optional[str]], str],
    format_fornecedor_code: Callable[[Any], str],
    friendly_errors: Dict[int, str],
) -> None:
    @app.get("/saas/fornecedores/{fornecedor_id}")
    def saas_supplier_detail(request: Request, fornecedor_id: int) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=render_saas_login_html(), media_type="text/html")
        suppliers_context = build_saas_suppliers_context(db, selected_supplier_id=fornecedor_id, search_term=request.query_params.get("q"))
        return Response(content=render_saas_dashboard_html(db, session_user, current_page="suppliers", suppliers_context=suppliers_context), media_type="text/html")

    @app.post("/saas/fornecedores")
    async def saas_create_supplier(request: Request) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        values = {k: str(v) for k, v in form.items()}
        current_page = normalize_saas_page(form.get("page") or "suppliers")
        try:
            nome = str(form.get("supplier_nome") or "").strip()
            if not nome:
                raise HTTPException(status_code=400, detail="Nome do fornecedor é obrigatório")
            fornecedor = db.create_fornecedor(nome=nome, telefone=str(form.get("supplier_telefone") or "").strip() or None, documento=str(form.get("supplier_documento") or "").strip() or None, apelido=str(form.get("supplier_apelido") or "").strip() or None, observacoes=str(form.get("supplier_observacoes") or "").strip() or None, opening_balances=parse_cliente_opening_balances(values, "supplier_opening"))
            if not fornecedor:
                raise HTTPException(status_code=409, detail="Cadastro de fornecedores indisponível. Aplique a migração do banco antes de usar esta rotina.")
            suppliers_context = build_saas_suppliers_context(db, selected_supplier_id=int(fornecedor.get("id") or 0))
            html = render_saas_dashboard_html(db, session_user, notice=f"Fornecedor registrado com sucesso. {format_fornecedor_code(fornecedor.get('id'))}", current_page=current_page, suppliers_context=suppliers_context)
            return Response(content=html, media_type="text/html")
        except HTTPException as exc:
            suppliers_context = build_saas_suppliers_context(db)
            html = render_saas_dashboard_html(db, session_user, notice=friendly_errors.get(exc.status_code, str(exc.detail)), notice_kind="error", current_page=current_page, form_values=values, suppliers_context=suppliers_context)
            return Response(content=html, media_type="text/html", status_code=exc.status_code)

    @app.post("/saas/fornecedores/{fornecedor_id}/movimentos")
    async def saas_supplier_movement(request: Request, fornecedor_id: int) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        current_page = normalize_saas_page(form.get("page") or "suppliers")
        suppliers_context = build_saas_suppliers_context(db, selected_supplier_id=fornecedor_id)
        try:
            movement_type = str(form.get("supplier_movement_type") or "adiantamento").strip().lower()
            moeda = str(form.get("supplier_movement_currency") or "USD").strip().upper()
            amount = parse_decimal_web_field(str(form.get("supplier_movement_amount") or "0"), "supplier_movement_amount")
            description = str(form.get("supplier_movement_description") or "").strip()
            ok = db.record_fornecedor_manual_movement(fornecedor_id, moeda, movement_type, amount, description or None, {"origem": "painel_web"})
        except HTTPException as exc:
            html = render_saas_dashboard_html(db, session_user, notice=friendly_errors.get(exc.status_code, str(exc.detail)), notice_kind="error", current_page=current_page, suppliers_context=suppliers_context)
            return Response(content=html, media_type="text/html", status_code=exc.status_code)
        if not ok:
            html = render_saas_dashboard_html(db, session_user, notice="Nao foi possivel registrar o movimento do fornecedor.", notice_kind="error", current_page=current_page, suppliers_context=suppliers_context)
            return Response(content=html, media_type="text/html", status_code=400)
        html = render_saas_dashboard_html(db, session_user, notice="Movimento do fornecedor registrado com sucesso.", current_page=current_page, suppliers_context=suppliers_context)
        return Response(content=html, media_type="text/html")
