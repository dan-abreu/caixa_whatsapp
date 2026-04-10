from typing import Any, Callable, Dict, List, Optional, cast

from fastapi import FastAPI, Header, HTTPException, Request


def register_operation_management_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    validate_webhook_token: Callable[[Optional[str]], None],
    invalidate_operation_related_view_caches: Callable[[], None],
) -> None:
    @app.post("/operations/{operation_id}/edit")
    async def edit_operation(
        operation_id: int,
        request: Request,
        x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    ) -> Dict[str, Any]:
        token = x_webhook_token or request.query_params.get("token")
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()

        transacao = db.client.table("transacoes").select("*").eq("id", operation_id).limit(1).execute()
        rows = cast(List[Dict[str, Any]], transacao.data or [])
        if not rows:
            raise HTTPException(status_code=404, detail="Operação não encontrada")

        body = await request.json()
        update_payload: Dict[str, Any] = {}
        if "quantidade" in body:
            update_payload["quantidade"] = str(body["quantidade"])
        if "cotacao_usada" in body:
            update_payload["cotacao_usada"] = str(body["cotacao_usada"])
        if "moeda_liquidacao" in body:
            update_payload["moeda_liquidacao"] = str(body["moeda_liquidacao"])
        if "valor_moeda" in body:
            update_payload["valor_moeda"] = str(body["valor_moeda"])

        if update_payload:
            db.client.table("transacoes").update(update_payload).eq("id", operation_id).execute()
            invalidate_operation_related_view_caches()

        return {
            "mensagem": f"✅ Operação OP-{operation_id} editada com sucesso",
            "dados": {"id": operation_id, "updated_fields": list(update_payload.keys())},
        }

    @app.delete("/operations/{operation_id}")
    async def delete_operation(
        operation_id: int,
        request: Request,
        x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    ) -> Dict[str, Any]:
        token = x_webhook_token or request.query_params.get("token")
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()
        kind = str(request.query_params.get("kind") or "transacao").strip().lower()

        if kind == "gold":
            ok = db.cancel_gold_transaction(operation_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Operação guiada não encontrada")
            invalidate_operation_related_view_caches()
            return {
                "mensagem": f"✅ Operação GT-{operation_id} cancelada",
                "dados": {"id": operation_id, "status": "cancelada", "kind": "gold"},
            }

        transacao = db.client.table("transacoes").select("*").eq("id", operation_id).limit(1).execute()
        rows = cast(List[Dict[str, Any]], transacao.data or [])
        if not rows:
            raise HTTPException(status_code=404, detail="Operação não encontrada")

        db.client.table("transacoes").update({"status": "cancelada"}).eq("id", operation_id).execute()
        invalidate_operation_related_view_caches()
        return {
            "mensagem": f"✅ Operação OP-{operation_id} cancelada",
            "dados": {"id": operation_id, "status": "cancelada"},
        }