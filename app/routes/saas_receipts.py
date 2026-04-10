from io import BytesIO
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse


def register_saas_receipt_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    get_saas_authenticated_user: Callable[[Request, Any], Optional[Dict[str, Any]]],
    render_saas_login_html: Callable[..., str],
    clear_saas_session: Callable[[Response], None],
    build_gold_receipt_context: Callable[[Any, int], Dict[str, Any]],
    render_saas_receipt_html: Callable[[Dict[str, Any], str, str], str],
    build_gold_receipt_pdf: Callable[[Dict[str, Any], str], bytes],
) -> None:
    @app.get("/saas/recibos/{operation_id}", name="saas_receipt_view")
    def saas_receipt_view(operation_id: int, request: Request) -> Response:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            response = Response(content=render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
            clear_saas_session(response)
            return response
        receipt = build_gold_receipt_context(db, operation_id)
        pdf_url = str(request.url_for("saas_receipt_pdf", operation_id=operation_id))
        return Response(content=render_saas_receipt_html(receipt, pdf_url=pdf_url, back_url="/saas?page=operations"), media_type="text/html")

    @app.get("/saas/recibos/{operation_id}/pdf", name="saas_receipt_pdf")
    def saas_receipt_pdf(operation_id: int, request: Request) -> StreamingResponse:
        db = get_db()
        session_user = get_saas_authenticated_user(request, db)
        if not session_user:
            raise HTTPException(status_code=401, detail="Faça login para continuar.")
        pdf_url = str(request.url_for("saas_receipt_pdf", operation_id=operation_id))
        receipt = build_gold_receipt_context(db, operation_id)
        return StreamingResponse(BytesIO(build_gold_receipt_pdf(receipt, pdf_url=pdf_url)), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="recibo-gt-{operation_id}.pdf"'})