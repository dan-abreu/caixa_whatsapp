import json
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response


def register_saas_client_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    render_saas_login_html: Callable[..., str],
    render_saas_dashboard_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    build_saas_clients_context: Callable[..., Dict[str, Any]],
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
    request_form_dict: Callable[[Request], Any],
    parse_cliente_opening_balances: Callable[[Dict[str, str], str], Dict[str, Any]],
    normalize_saas_page: Callable[[Optional[str]], str],
    format_cliente_code: Callable[[Any], str],
    friendly_errors: Dict[int, str],
) -> None:
    @app.get("/saas/clientes/search")
    def saas_client_search(request: Request, q: str = "") -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False), media_type="application/json", status_code=401)
        items = [{"id": item.get("id"), "nome": str(item.get("nome") or ""), "meta": build_cliente_lookup_meta(item)} for item in db.search_clientes(q, limit=8)]
        return Response(content=json.dumps({"ok": True, "items": items}, ensure_ascii=False), media_type="application/json")

    @app.get("/saas/clientes/{cliente_id}")
    def saas_client_detail(request: Request, cliente_id: int) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=render_saas_login_html(), media_type="text/html")
        clients_context = build_saas_clients_context(db, selected_client_id=cliente_id, search_term=request.query_params.get("q"))
        return Response(content=render_saas_dashboard_html(db, session_user, current_page="clients", clients_context=clients_context), media_type="text/html")

    @app.post("/saas/clientes")
    async def saas_create_client(request: Request) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        values = {k: str(v) for k, v in form.items()}
        current_page = normalize_saas_page(form.get("page") or "clients")
        try:
            nome = str(form.get("client_nome") or "").strip()
            if not nome:
                raise HTTPException(status_code=400, detail="Nome do cliente é obrigatório")
            cliente = db.create_cliente(nome=nome, telefone=str(form.get("client_telefone") or "").strip() or None, documento=str(form.get("client_documento") or "").strip() or None, apelido=str(form.get("client_apelido") or "").strip() or None, observacoes=str(form.get("client_observacoes") or "").strip() or None, opening_balances=parse_cliente_opening_balances(values, "client_opening"))
            if not cliente:
                raise HTTPException(status_code=409, detail="Cadastro de clientes indisponível. Aplique a migração do banco antes de usar esta rotina.")
            selected_client_id = int(cliente.get("id") or 0)
            clients_context = build_saas_clients_context(db, selected_client_id=selected_client_id)
            if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
                return Response(content=json.dumps({"ok": True, "item": {"id": cliente.get("id"), "nome": str(cliente.get("nome") or ""), "meta": build_cliente_lookup_meta(cliente)}}, ensure_ascii=False), media_type="application/json")
            return Response(content=render_saas_dashboard_html(db, session_user, notice=f"Cliente registrado com sucesso. {format_cliente_code(cliente.get('id'))}", current_page=current_page, form_values=values, clients_context=clients_context), media_type="text/html")
        except HTTPException as exc:
            clients_context = build_saas_clients_context(db)
            notice = friendly_errors.get(exc.status_code, str(exc.detail))
            if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
                return Response(content=json.dumps({"ok": False, "notice": notice}, ensure_ascii=False), media_type="application/json", status_code=exc.status_code)
            return Response(content=render_saas_dashboard_html(db, session_user, notice=notice, notice_kind="error", current_page=current_page, form_values=values, clients_context=clients_context), media_type="text/html", status_code=exc.status_code)