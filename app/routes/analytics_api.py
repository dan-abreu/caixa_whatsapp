from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response

from app.multi_agent_system import MultiAgentRequest, MultiAgentResponse, run_multi_agent_orchestration


def register_analytics_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
    build_custom_range: Callable[[str, str], Dict[str, str]],
) -> None:
    @app.get("/reports/risk-alerts")
    def risk_alerts_report(date: Optional[str] = None) -> Dict[str, Any]:
        db = get_db()
        day = build_day_range(date)
        alerts = db.get_risk_alerts(day["start"], day["end"])
        return {"date": day["date"], "total_alertas": len(alerts), "alerts": alerts}

    @app.get("/reports/closure-range")
    def closure_range_report(start: str, end: str) -> Dict[str, Any]:
        db = get_db()
        rng = build_custom_range(start, end)
        return {"range": rng, "summary": db.get_gold_summary_range(rng["start"], rng["end"]), "by_operator": db.get_daily_gold_summary_by_operator(rng["start"], rng["end"])}

    @app.get("/reports/reconciliation-by-currency")
    def reconciliation_by_currency_report(start: str, end: str) -> Dict[str, Any]:
        db = get_db()
        rng = build_custom_range(start, end)
        return {"range": rng, "by_currency": db.get_gold_summary_by_currency(rng["start"], rng["end"])}

    @app.get("/reports/closure-csv")
    def closure_csv_report(start: str, end: str) -> Response:
        db = get_db()
        rng = build_custom_range(start, end)
        summary = db.get_gold_summary_range(rng["start"], rng["end"])
        by_operator = db.get_daily_gold_summary_by_operator(rng["start"], rng["end"])
        by_currency = db.get_gold_summary_by_currency(rng["start"], rng["end"])
        lines: List[str] = ["section,key,value", f"summary,total_operacoes,{summary.get('total_operacoes', 0)}", f"summary,total_usd,{summary.get('total_usd', '0')}", f"summary,total_pago_usd,{summary.get('total_pago_usd', '0')}", f"summary,total_diferenca_usd,{summary.get('total_diferenca_usd', '0')}", "", "operators,operador_id,total_operacoes,total_usd,total_pago_usd,total_diferenca_usd"]
        for row in by_operator:
            lines.append("operators," f"{row.get('operador_id', '')}," f"{row.get('total_operacoes', 0)}," f"{row.get('total_usd', '0')}," f"{row.get('total_pago_usd', '0')}," f"{row.get('total_diferenca_usd', '0')}")
        lines.extend(["", "currency,moeda,total_pagamentos,total_valor_moeda,total_valor_usd"])
        for row in by_currency:
            lines.append("currency," f"{row.get('moeda', '')}," f"{row.get('total_pagamentos', 0)}," f"{row.get('total_valor_moeda', '0')}," f"{row.get('total_valor_usd', '0')}")
        return Response(content="\n".join(lines), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=closure_report.csv"})

    @app.get("/reports/top-divergences")
    def top_divergences_report(start: str, end: str, limit: int = 10) -> Dict[str, Any]:
        db = get_db()
        rng = build_custom_range(start, end)
        return {"range": rng, "limit": max(limit, 1), "items": db.get_top_divergences(rng["start"], rng["end"], limit=limit)}

    @app.get("/reports/audit/operation/{operation_id}")
    def operation_audit_report(operation_id: int) -> Dict[str, Any]:
        result = get_db().get_gold_operation_audit(operation_id)
        if not result:
            raise HTTPException(status_code=404, detail="Operação não encontrada")
        return result

    @app.post("/ai/multi-agent/analyze", response_model=MultiAgentResponse)
    def multi_agent_analyze(request: MultiAgentRequest) -> MultiAgentResponse:
        db = get_db()
        live_context = db.build_multi_agent_live_context(operation_id=request.operation_id)
        enriched_request = request.model_copy(update={"live_context": {**dict(request.live_context), **live_context}})
        response = run_multi_agent_orchestration(enriched_request)
        db.save_multi_agent_run(objective=enriched_request.objective, operation_id=enriched_request.operation_id, operation_kind=enriched_request.operation_kind, source_message_id=enriched_request.source_message_id, request_payload=enriched_request.model_dump(mode="json"), response_payload=response.model_dump(mode="json"))
        return response

    @app.get("/ai/multi-agent/runs")
    def multi_agent_recent_runs(limit: int = 10) -> Dict[str, Any]:
        safe_limit = max(1, min(limit, 50))
        return {"limit": safe_limit, "items": get_db().get_recent_multi_agent_runs(limit=safe_limit)}