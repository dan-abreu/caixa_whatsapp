from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse


def register_system_routes(
    app: FastAPI,
    *,
    get_db: Callable[[], Any],
    build_day_range: Callable[[Optional[str]], Dict[str, str]],
) -> None:
    @app.get("/health")
    def healthcheck() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(url="/saas", status_code=307)

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/menu")
    def menu() -> Dict[str, Any]:
        return {
            "titulo": "Central de Comandos",
            "versao": "1.0.0",
            "funcionalidades": [
                {
                    "id": 1,
                    "nome": "Registrar operacao de compra ou venda",
                    "intencao": "registrar_operacao",
                    "descricao": "Executa o registro operacional de ouro por fluxo assistido.",
                    "exemplos": ["compra", "venda", "compra ouro 2g"],
                    "resposta_esperada": "Retorna o comprovante operacional da transacao.",
                },
                {
                    "id": 2,
                    "nome": "Consultar posicao de caixa",
                    "intencao": "consultar_relatorio",
                    "descricao": "Apresenta a posicao atual por moeda e a situacao do ouro em caixa.",
                    "exemplos": ["caixa", "caixa eur", "caixa srd", "caixa xau"],
                    "resposta_esperada": "Retorna a posicao consolidada atual.",
                },
                {
                    "id": 3,
                    "nome": "Consultar extrato analitico",
                    "intencao": "extrato",
                    "descricao": "Lista as operacoes do periodo com detalhamento de cada lancamento.",
                    "exemplos": ["extrato", "extrato hoje", "extrato semana"],
                    "resposta_esperada": "Retorna o extrato detalhado em formato analitico.",
                },
                {
                    "id": 4,
                    "nome": "Ajustar operacao",
                    "intencao": "editar_operacao",
                    "descricao": "Permite ajustar preco, quantidade, moeda, valor na moeda ou cambio de uma operacao existente.",
                    "exemplos": ["editar 123 preco 110", "editar 123 quantidade 2.5"],
                    "resposta_esperada": "Confirma os campos atualizados na operacao.",
                },
                {
                    "id": 5,
                    "nome": "Cancelar operacao",
                    "intencao": "cancelar_operacao",
                    "descricao": "Inativa a operacao selecionada no controle operacional.",
                    "exemplos": ["cancelar 123"],
                    "resposta_esperada": "Confirma o cancelamento operacional.",
                },
            ],
            "ativos_disponiveis": [
                {"nome": "ouro", "aliases": ["gold", "oro", "or"]},
                {"nome": "usd", "aliases": ["dollar", "dolar"]},
                {"nome": "eur", "aliases": ["euro"]},
                {"nome": "srd", "aliases": []},
                {"nome": "brl", "aliases": ["real", "reais"]},
            ],
            "dicas": [
                "Utilize instrucoes objetivas.",
                "Informe um dado por vez durante o fluxo.",
                "Em caso de duvida, envie: menu.",
                "Para retornar uma etapa, envie: voltar.",
            ],
        }

    @app.get("/reports/daily-closure")
    def daily_closure_report(date: Optional[str] = None) -> Dict[str, Any]:
        db = get_db()
        day = build_day_range(date)
        summary = db.get_daily_gold_summary(day["start"], day["end"])
        by_operator = db.get_daily_gold_summary_by_operator(day["start"], day["end"])
        return {
            "date": day["date"],
            "summary": summary,
            "by_operator": by_operator,
        }