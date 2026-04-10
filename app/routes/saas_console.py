import json
from typing import Any, Callable, Dict, Optional, Set

from fastapi import FastAPI, HTTPException, Request, Response


def register_saas_console_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    request_form_dict: Callable[[Request], Any],
    render_saas_login_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    normalize_saas_page: Callable[[Optional[str]], str],
    render_saas_dashboard_html: Callable[..., str],
    normalize_text: Callable[[str], str],
    get_session: Callable[[Any, str], Optional[Dict[str, Any]]],
    guided_flow_states: Set[str],
    is_guided_session_stale: Callable[[Dict[str, Any]], bool],
    clear_session: Callable[[Any, str], None],
    whatsapp_payload_cls: Any,
    processar_webhook: Callable[[Any, Any, Optional[str]], Dict[str, Any]],
    friendly_errors: Dict[int, str],
    build_operation_draft_from_message: Callable[[Any, Dict[str, Any], str], Dict[str, Any]],
) -> None:
    @app.post("/saas/console")
    async def saas_console(request: Request) -> Response:
        form = await request_form_dict(request)
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        current_page = normalize_saas_page(form.get("page"))
        remetente = str(form.get("console_remetente") or "").strip()
        mensagem = str(form.get("console_mensagem") or "").strip()
        values = {k: str(v) for k, v in form.items()}
        if not remetente or not mensagem:
            if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
                return Response(content=json.dumps({"ok": False, "notice": "Preencha remetente e mensagem no chat."}, ensure_ascii=False), media_type="application/json", status_code=400)
            html = render_saas_dashboard_html(db, session_user, notice="Preencha remetente e mensagem no console.", notice_kind="error", form_values=values, current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=400)
        if str(session_user.get("tipo_usuario") or "").lower() != "admin":
            remetente = str(session_user.get("telefone") or remetente)
            values["console_remetente"] = remetente
        mensagem_norm = normalize_text(mensagem)
        web_session = get_session(db, remetente)
        if web_session:
            estado = str(web_session.get("estado", ""))
            if estado in guided_flow_states and is_guided_session_stale(web_session) and mensagem_norm not in {"continuar", "continue", "cancelar", "cancela", "cancel", "parar", "sair"}:
                clear_session(db, remetente)
        try:
            result = processar_webhook(whatsapp_payload_cls(remetente=remetente, mensagem=mensagem), db, None)
            if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
                return Response(content=json.dumps({"ok": True, "message": str(result.get("mensagem") or ""), "notice": "Mensagem processada pelo motor do WhatsApp."}, ensure_ascii=False), media_type="application/json")
            html = render_saas_dashboard_html(db, session_user, notice="Mensagem processada pelo motor do WhatsApp.", notice_kind="info", assistant_result=result, form_values=values, current_page=current_page)
            return Response(content=html, media_type="text/html")
        except HTTPException as exc:
            if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
                return Response(content=json.dumps({"ok": False, "notice": friendly_errors.get(exc.status_code, str(exc.detail))}, ensure_ascii=False), media_type="application/json", status_code=exc.status_code)
            html = render_saas_dashboard_html(db, session_user, notice=friendly_errors.get(exc.status_code, str(exc.detail)), notice_kind="error", form_values=values, current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=exc.status_code)

    @app.post("/saas/operations/draft")
    async def saas_operation_draft(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            return Response(content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False), media_type="application/json", status_code=401)
        form = await request_form_dict(request)
        draft_message = str(form.get("draft_message") or "").strip()
        try:
            payload = build_operation_draft_from_message(db, session_user, draft_message)
            return Response(content=json.dumps({"ok": True, **payload}, ensure_ascii=False), media_type="application/json")
        except HTTPException as exc:
            return Response(content=json.dumps({"ok": False, "notice": friendly_errors.get(exc.status_code, str(exc.detail))}, ensure_ascii=False), media_type="application/json", status_code=exc.status_code)