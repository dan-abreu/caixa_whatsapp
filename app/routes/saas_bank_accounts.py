import json
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response


def _bank_account_form_payload(form: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "currency_code": str(form.get("bank_currency_code") or "").strip().upper(),
        "country_code": str(form.get("bank_country_code") or "").strip().upper() or None,
        "label": str(form.get("bank_label") or "").strip(),
        "holder_name": str(form.get("bank_holder_name") or "").strip(),
        "bank_name": str(form.get("bank_bank_name") or "").strip() or None,
        "branch_name": str(form.get("bank_branch_name") or "").strip() or None,
        "branch_code": str(form.get("bank_branch_code") or "").strip() or None,
        "account_number": str(form.get("bank_account_number") or "").strip() or None,
        "pix_key": str(form.get("bank_pix_key") or "").strip() or None,
        "document_number": str(form.get("bank_document_number") or "").strip() or None,
        "notes": str(form.get("bank_notes") or "").strip() or None,
        "is_default": str(form.get("bank_is_default") or "") == "1",
    }


def _json_bank_account_item(account: Dict[str, Any]) -> Dict[str, Any]:
    reference = str(account.get("pix_key") or account.get("account_number") or account.get("branch_code") or "")
    summary = " | ".join(
        bit
        for bit in [
            str(account.get("label") or "Conta salva"),
            str(account.get("currency_code") or "").upper(),
            str(account.get("country_code") or "").upper(),
            str(account.get("holder_name") or ""),
            str(account.get("bank_name") or ""),
            reference,
        ]
        if bit
    )
    return {
        "id": account.get("id"),
        "currency_code": str(account.get("currency_code") or "").upper(),
        "country_code": str(account.get("country_code") or "").upper(),
        "label": str(account.get("label") or ""),
        "holder_name": str(account.get("holder_name") or ""),
        "bank_name": str(account.get("bank_name") or ""),
        "branch_name": str(account.get("branch_name") or ""),
        "branch_code": str(account.get("branch_code") or ""),
        "account_number": str(account.get("account_number") or ""),
        "pix_key": str(account.get("pix_key") or ""),
        "document_number": str(account.get("document_number") or ""),
        "summary": summary,
    }


def register_saas_bank_account_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    request_form_dict: Callable[[Request], Any],
    render_saas_login_html: Callable[..., str],
    render_saas_dashboard_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    normalize_saas_page: Callable[[Optional[str]], str],
    build_saas_clients_context: Callable[..., Dict[str, Any]],
    build_saas_suppliers_context: Callable[..., Dict[str, Any]],
) -> None:
    @app.get("/saas/clientes/{cliente_id}/bank-accounts")
    def saas_client_bank_accounts(request: Request, cliente_id: int) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False), media_type="application/json", status_code=401)
        accounts = [_json_bank_account_item(item) for item in db.list_cliente_bank_accounts(cliente_id)]
        return Response(content=json.dumps({"ok": True, "items": accounts}, ensure_ascii=False), media_type="application/json")

    @app.post("/saas/clientes/{cliente_id}/bank-accounts")
    async def saas_create_client_bank_account(request: Request, cliente_id: int) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        current_page = normalize_saas_page(form.get("page") or "clients")
        account = db.create_saved_bank_account(owner_kind="cliente", owner_id=cliente_id, created_by_phone=str(session_user.get("telefone") or ""), **_bank_account_form_payload(form))
        clients_context = build_saas_clients_context(db, selected_client_id=cliente_id)
        if not account:
            html = render_saas_dashboard_html(db, session_user, notice="Nao foi possivel salvar a conta bancaria do cliente.", notice_kind="error", current_page=current_page, clients_context=clients_context)
            return Response(content=html, media_type="text/html", status_code=400)
        html = render_saas_dashboard_html(db, session_user, notice="Conta bancaria do cliente salva com sucesso.", current_page=current_page, clients_context=clients_context)
        return Response(content=html, media_type="text/html")

    @app.post("/saas/fornecedores/{fornecedor_id}/bank-accounts")
    async def saas_create_supplier_bank_account(request: Request, fornecedor_id: int) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        current_page = normalize_saas_page(form.get("page") or "suppliers")
        account = db.create_saved_bank_account(owner_kind="fornecedor", owner_id=fornecedor_id, created_by_phone=str(session_user.get("telefone") or ""), **_bank_account_form_payload(form))
        suppliers_context = build_saas_suppliers_context(db, selected_supplier_id=fornecedor_id)
        if not account:
            html = render_saas_dashboard_html(db, session_user, notice="Nao foi possivel salvar a conta bancaria do fornecedor.", notice_kind="error", current_page=current_page, suppliers_context=suppliers_context)
            return Response(content=html, media_type="text/html", status_code=400)
        html = render_saas_dashboard_html(db, session_user, notice="Conta bancaria do fornecedor salva com sucesso.", current_page=current_page, suppliers_context=suppliers_context)
        return Response(content=html, media_type="text/html")

    @app.post("/saas/profile/company-bank-accounts")
    async def saas_create_company_bank_account(request: Request) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        current_page = normalize_saas_page(form.get("page") or "profile")
        if str(session_user.get("tipo_usuario") or "").lower() != "admin":
            html = render_saas_dashboard_html(db, session_user, notice="Somente administradores podem cadastrar contas corporativas.", notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=403)
        account = db.create_saved_bank_account(owner_kind="empresa", owner_id=None, created_by_phone=str(session_user.get("telefone") or ""), **_bank_account_form_payload(form))
        if not account:
            html = render_saas_dashboard_html(db, session_user, notice="Nao foi possivel salvar a conta corporativa.", notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=400)
        html = render_saas_dashboard_html(db, session_user, notice="Conta corporativa salva com sucesso.", current_page=current_page)
        return Response(content=html, media_type="text/html")
