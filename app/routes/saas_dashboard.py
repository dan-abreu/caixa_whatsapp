from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response


def register_saas_dashboard_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    render_saas_login_html: Callable[..., str],
    normalize_saas_page: Callable[[Optional[str]], str],
    build_saas_statement_context: Callable[[Any, Optional[str], Optional[str]], Dict[str, Any]],
    render_saas_dashboard_html: Callable[..., str],
    build_saas_clients_context: Callable[..., Dict[str, Any]],
    build_saas_suppliers_context: Callable[..., Dict[str, Any]],
    build_cliente_lookup_meta: Callable[[Dict[str, Any]], str],
) -> None:
    @app.get("/saas")
    @app.get("/saas/dashboard")
    @app.get("/saas/operation")
    @app.get("/saas/monitores")
    @app.get("/saas/noticias")
    @app.get("/saas/clientes")
    @app.get("/saas/fornecedores")
    @app.get("/saas/extrato")
    @app.get("/saas/profile")
    def saas_dashboard(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=render_saas_login_html(), media_type="text/html")

        prefill_values: Optional[Dict[str, str]] = None
        current_page = normalize_saas_page(request.query_params.get("page"))
        if request.url.path.endswith("/operation"):
            current_page = "operation"
        elif request.url.path.endswith("/monitores"):
            current_page = "monitors"
        elif request.url.path.endswith("/noticias"):
            current_page = "news_hub"
        elif request.url.path.endswith("/clientes"):
            current_page = "clients"
        elif request.url.path.endswith("/fornecedores"):
            current_page = "suppliers"
        elif request.url.path.endswith("/extrato"):
            current_page = "statement"
        elif request.url.path.endswith("/profile"):
            current_page = "profile"

        statement_context: Optional[Dict[str, Any]] = None
        clients_context: Optional[Dict[str, Any]] = None
        suppliers_context: Optional[Dict[str, Any]] = None
        if current_page == "operation":
            raw_client_id = str(request.query_params.get("client_id") or "").strip()
            if raw_client_id.isdigit():
                cliente = db.get_cliente_by_id(int(raw_client_id))
                if cliente:
                    prefill_values = {
                        "cliente_id": str(cliente.get("id") or ""),
                        "pessoa": str(cliente.get("nome") or ""),
                        "cliente_lookup_meta": build_cliente_lookup_meta(cliente),
                    }
        if current_page == "statement":
            try:
                statement_context = build_saas_statement_context(
                    db,
                    request.query_params.get("start_date"),
                    request.query_params.get("end_date"),
                )
            except HTTPException as exc:
                statement_context = build_saas_statement_context(db, None, None)
                html = render_saas_dashboard_html(
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
            clients_context = build_saas_clients_context(db, selected_client_id=selected_client_id, search_term=request.query_params.get("q"))
        elif current_page == "suppliers":
            selected_supplier_id: Optional[int] = None
            raw_supplier_id = str(request.query_params.get("supplier_id") or "").strip()
            if raw_supplier_id.isdigit():
                selected_supplier_id = int(raw_supplier_id)
            suppliers_context = build_saas_suppliers_context(db, selected_supplier_id=selected_supplier_id, search_term=request.query_params.get("q"))

        return Response(
            content=render_saas_dashboard_html(
                db,
                session_user,
                current_page=current_page,
                statement_context=statement_context,
                clients_context=clients_context,
                suppliers_context=suppliers_context,
                form_values=prefill_values,
            ),
            media_type="text/html",
        )