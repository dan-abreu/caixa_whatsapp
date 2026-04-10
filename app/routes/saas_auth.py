import json
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response


def register_saas_auth_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    request_form_dict: Callable[[Request], Any],
    normalize_user_phone: Callable[[str], str],
    render_saas_login_html: Callable[..., str],
    render_saas_dashboard_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    set_saas_authenticated_user_cached: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    set_saas_session: Callable[[Response, str], None],
    decode_saas_session: Callable[[Optional[str]], Optional[str]],
    saas_session_cookie: str,
    invalidate_saas_authenticated_user_cache: Callable[[Optional[str]], None],
    validate_web_pin_format: Callable[[str], str],
    normalize_saas_page: Callable[[Optional[str]], str],
) -> None:
    @app.post("/saas/login")
    async def saas_login(request: Request) -> Response:
        form = await request_form_dict(request)
        telefone = normalize_user_phone(str(form.get("telefone") or ""))
        pin = str(form.get("pin") or "")
        if not telefone or not pin:
            return Response(content=render_saas_login_html("Informe telefone e PIN.", telefone=telefone), media_type="text/html", status_code=400)
        db = get_db()
        usuario = db.verify_usuario_web_pin(telefone, pin)
        if not usuario:
            return Response(content=render_saas_login_html("Credenciais inválidas.", telefone=telefone), media_type="text/html", status_code=401)
        set_saas_authenticated_user_cached(telefone, dict(usuario))
        response = Response(content=render_saas_dashboard_html(db, usuario), media_type="text/html")
        set_saas_session(response, telefone)
        return response

    @app.post("/saas/logout")
    def saas_logout(request: Request) -> Response:
        telefone = decode_saas_session(request.cookies.get(saas_session_cookie))
        if telefone:
            invalidate_saas_authenticated_user_cache(telefone)
        response = Response(content=render_saas_login_html("Sessão encerrada."), media_type="text/html")
        clear_saas_session(response)
        return response

    @app.post("/saas/profile/pin")
    async def saas_profile_pin(request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
                return Response(content=json.dumps({"ok": False, "notice": "Faça login para continuar."}, ensure_ascii=False), media_type="application/json", status_code=401)
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        form = await request_form_dict(request)
        current_page = normalize_saas_page(form.get("page"))
        current_pin = str(form.get("current_pin") or "")
        new_pin = str(form.get("new_pin") or "")
        confirm_pin = str(form.get("confirm_pin") or "")
        try:
            validate_web_pin_format(current_pin)
            validated_new_pin = validate_web_pin_format(new_pin)
        except HTTPException as exc:
            html = render_saas_dashboard_html(db, session_user, notice=str(exc.detail), notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=exc.status_code)
        if validated_new_pin != confirm_pin:
            html = render_saas_dashboard_html(db, session_user, notice="Confirmação do novo PIN não confere.", notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=400)
        if not db.verify_usuario_web_pin(str(session_user.get("telefone") or ""), current_pin):
            html = render_saas_dashboard_html(db, session_user, notice="PIN atual inválido.", notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=401)
        update_result = db.set_usuario_web_pin(str(session_user.get("telefone") or ""), validated_new_pin)
        if not update_result:
            html = render_saas_dashboard_html(db, session_user, notice="Não foi possível atualizar o PIN.", notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=500)
        if not bool(update_result.get("web_pin_schema_ready", True)):
            html = render_saas_dashboard_html(db, session_user, notice="Troca de PIN indisponível: aplique a migração do banco que adiciona web_pin_hash e web_pin_updated_em na tabela usuarios.", notice_kind="error", current_page=current_page)
            return Response(content=html, media_type="text/html", status_code=409)
        invalidate_saas_authenticated_user_cache(str(session_user.get("telefone") or ""))
        refreshed_user = db.get_usuario_web_auth(str(session_user.get("telefone") or "")) or session_user
        set_saas_authenticated_user_cached(str(session_user.get("telefone") or ""), dict(refreshed_user))
        response = Response(content=render_saas_dashboard_html(db, refreshed_user, notice="PIN web atualizado com sucesso.", current_page=current_page), media_type="text/html")
        set_saas_session(response, str(session_user.get("telefone") or ""))
        return response