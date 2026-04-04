import os
import logging
import re
import unicodedata
from html import escape
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, cast
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from ai_service import AIServiceError, extract_message_data
from database import DatabaseClient, DatabaseError
from multi_agent_system import MultiAgentRequest, MultiAgentResponse, run_multi_agent_orchestration


class WhatsAppWebhookPayload(BaseModel):
    remetente: str = Field(..., description="Telefone/ID do remetente")
    mensagem: str = Field(..., min_length=1, description="Texto recebido via WhatsApp")


class AIExtractedData(BaseModel):
    intencao: str
    ativo: Optional[str] = None
    quantidade: Optional[float] = None
    valor_informado: Optional[float] = None
    resposta: Optional[str] = None

    @field_validator("intencao")
    @classmethod
    def validate_intencao(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"atualizar_taxa", "registrar_operacao", "consultar_relatorio", "conversar"}:
            raise ValueError("intencao inválida")
        return normalized

    @field_validator("ativo")
    @classmethod
    def validate_ativo(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("ativo vazio")
        return value.strip()


app = FastAPI(title="Caixa Inteligente WhatsApp API", version="1.0.0")
logger = logging.getLogger("caixa_whatsapp")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def get_db() -> DatabaseClient:
    try:
        return DatabaseClient()
    except DatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def normalize_ativo_nome(raw: str) -> str:
    value = raw.strip().lower()
    aliases = {
        "ouro": "Ouro",
        "ouro 24k": "Ouro",
        "ouro 18k": "Ouro",
        "grama": "Ouro",
        "gramas": "Ouro",
        "usd": "USD",
        "dolar": "USD",
        "dólar": "USD",
        "dolares": "USD",
        "dólares": "USD",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
        "srd": "SRD",
    }
    return aliases.get(value, raw.strip())


def infer_tipo_operacao(mensagem: str) -> str:
    text = mensagem.lower()
    if "vendi" in text or "venda" in text:
        return "venda"
    if "cambio" in text or "câmbio" in text or "troca" in text:
        return "cambio"
    return "compra"


def parse_decimal(value: object, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Campo inválido: {field_name}") from exc


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_webhook_token(token: Optional[str]) -> None:
    expected = os.getenv("WEBHOOK_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="WEBHOOK_TOKEN não configurado no ambiente.")
    if token != expected:
        raise HTTPException(status_code=401, detail="Webhook token inválido.")


def _twiml_message(text: str) -> Response:
    safe_text = escape(text)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe_text}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


@app.get("/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/menu")
def menu() -> Dict[str, Any]:
    return {
        "titulo": "📋 Menu de Funcionalidades - Caixa Inteligente",
        "versao": "1.0.0",
        "funcionalidades": [
            {
                "id": 1,
                "nome": "📊 Consultar Extrato/Saldo",
                "intencao": "consultar_relatorio",
                "descricao": "Ver extrato do dia, operações realizadas, totais em USD e diferenças",
                "exemplos": [
                    "extrato",
                    "saldo",
                    "relatório",
                    "caixa",
                    "resumo",
                    "fechamento",
                    "balanço",
                    "statement",
                    "ver meu caixa",
                    "consultaas",
                    "me mostre o saldo"
                ],
                "resposta_esperada": "Extrato de hoje com operações, total USD, total pago e diferença"
            },
            {
                "id": 2,
                "nome": "💼 Registrar Compra/Operação",
                "intencao": "registrar_operacao",
                "descricao": "Registrar uma operação de compra, venda ou câmbio com ativo e quantidade",
                "exemplos": [
                    "Comprei 2g de ouro",
                    "Vendi 3g de ouro",
                    "Entrada 5g",
                    "Saída 1g",
                    "Received 10g gold",
                    "Achete 2 oro",
                    "Bought 100 USD",
                    "Compra 500 SRD"
                ],
                "resposta_esperada": "Compra/Venda registrada com detalhes (quantidade x preço = total)"
            },
            {
                "id": 3,
                "nome": "💹 Atualizar Taxa (Admin)",
                "intencao": "atualizar_taxa",
                "descricao": "Atualizar a taxa de um ativo (apenas para administradores)",
                "exemplos": [
                    "Taxa ouro 70.50",
                    "Cotação USD 5.30",
                    "Preço EUR 5.50",
                    "Quote gold 1850",
                    "Set rate ouro 68.00",
                    "Actualiza oro 2000",
                    "Prix or 90"
                ],
                "resposta_esperada": "Taxa atualizada para o ativo",
                "restricoes": "Apenas usuários com tipo_usuario='admin'"
            },
            {
                "id": 4,
                "nome": "🔄 Câmbio/Conversão",
                "intencao": "registrar_operacao (com exchange)",
                "descricao": "Registrar operação de câmbio entre moedas ou ativos",
                "exemplos": [
                    "Cambio ouro para USD",
                    "Troca 50g ouro",
                    "Exchange 1000 SRD para USD",
                    "Swap ouro por real",
                    "Convert 200 EUR para USD"
                ],
                "resposta_esperada": "Operação de câmbio registrada com valores"
            },
            {
                "id": 5,
                "nome": "💬 Conversa Geral",
                "intencao": "conversar",
                "descricao": "Dúvidas, saudações e qualquer outra pergunta fora das categorias acima",
                "exemplos": [
                    "Oi, tudo bem?",
                    "Como funciona?",
                    "Quais são os ativos?",
                    "Me ajuda com uma dúvida",
                    "Qual é o horário?"
                ],
                "resposta_esperada": "Resposta amigável ou redirecionamento para as funções disponíveis"
            }
        ],
        "ativos_disponiveis": [
            {"nome": "ouro", "aliases": ["gold", "oro", "or", "xau", "bullion", "barra", "lingote"]},
            {"nome": "usd", "aliases": ["dollar", "dolar", "us$", "usdollar"]},
            {"nome": "eur", "aliases": ["euro"]},
            {"nome": "srd", "aliases": ["suriname-dollar", "gulden"]}
        ],
        "dicas": [
            "✅ Use quantidades em gramas (g) para ouro",
            "✅ Use nomes de moedas ou seus aliases/abreviações",
            "✅ Seja informal - 'comprei', 'vendi', 'cambio' etc. funcionam",
            "✅ O sistema entende português, inglês, espanhol, francês e holandês",
            "✅ Mensagens são idempotentes - envie de novo sem medo de duplicar"
        ]
    }


_ERROS_AMIGAVEIS: Dict[int, str] = {
    400: "Não entendi sua mensagem. Tente algo como: 'Comprei 2g de ouro' ou 'Taxa ouro 70.00'.",
    403: "Você não tem permissão para essa operação.",
    404: "Ativo não encontrado. Os ativos disponíveis são: Ouro (por teor), USD, EUR, SRD.",
    500: "Erro interno, tente novamente em instantes.",
    502: "Serviço de IA indisponível no momento, tente novamente.",
}

# Fallback de idempotência para ambiente sem migração aplicada.
_IDEMPOTENCY_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}

_MOEDAS_SUPORTADAS = ["USD", "SRD", "EUR", "BRL"]
_RISK_DIFF_LIMIT_USD = Decimal(os.getenv("RISK_DIFF_LIMIT_USD", "250"))
_MULTI_AGENT_AUTO_ENABLED = os.getenv("MULTI_AGENT_AUTO_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
_MULTI_AGENT_AUTO_MIN_USD = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_USD", "500"))
_MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS", "10"))
_GUIDED_FLOW_STATES = {
    "await_origem",
    "await_teor",
    "await_peso",
    "await_preco_usd",
    "await_moedas",
    "await_valor_moeda",
    "await_cambio_moeda",
    "await_fechamento_gramas",
    "await_fechamento_tipo",
    "await_pessoa",
    "await_forma_pagamento",
    "await_observacoes",
    "await_confirmacao",
    "await_preco_simples",
    "await_moeda_simples",
    "await_cambio_simples",
}


def _normalize_text(value: str) -> str:
    lowered = value.strip().lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _parse_decimal_from_text(value: str, field_name: str) -> Decimal:
    cleaned = value.strip().replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    return parse_decimal(cleaned, field_name)


def _extract_confirmacao(value: str) -> Optional[bool]:
    text = _normalize_text(value)
    if text in {"sim", "confirmar", "ok", "confirmo", "s"}:
        return True
    if text in {"nao", "não", "cancelar", "n", "cancela"}:
        return False
    return None


def _extract_moedas(value: str) -> List[str]:
    text = _normalize_text(value)
    aliases = {
        "usd": "USD",
        "dolar": "USD",
        "dolares": "USD",
        "srd": "SRD",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
    }
    found: List[str] = []
    for token in re.split(r"[^a-zA-Z]+", text):
        if not token:
            continue
        moeda = aliases.get(token)
        if moeda and moeda not in found:
            found.append(moeda)
    return found


def _is_help_menu_request(message: str) -> bool:
    text = _normalize_text(message)
    keywords = [
        "menu",
        "ajuda",
        "help",
        "comandos",
        "o que voce pode fazer",
        "o que você pode fazer",
        "como funciona",
        "funcionalidades",
    ]
    return any(k in text for k in keywords)


def _build_whatsapp_checklist_menu() -> str:
    return (
        "Checklist de funcionalidades:\n"
        "[1] Registrar operacao\n"
        "- Exemplo: Comprei 2g de ouro a 105\n\n"
        "[2] Consultar caixa/extrato\n"
        "- Exemplo: caixa\n\n"
        "[3] Atualizar taxa (admin)\n"
        "- Exemplo: Taxa USD 5.40\n\n"
        "[4] Editar operacao\n"
        "- Endpoint: POST /operations/{id}/edit\n\n"
        "[5] Cancelar operacao\n"
        "- Endpoint: DELETE /operations/{id}\n\n"
        "Diga o numero da opcao ou escreva sua solicitacao."
    )


def _save_session(db: DatabaseClient, remetente: str, estado: str, contexto: Dict[str, Any]) -> None:
    _SESSION_CACHE[remetente] = {"estado": estado, "contexto": contexto}
    db.save_conversation_session(remetente=remetente, estado=estado, contexto=contexto)


def _get_session(db: DatabaseClient, remetente: str) -> Optional[Dict[str, Any]]:
    cached = _SESSION_CACHE.get(remetente)
    if cached:
        return cached
    db_session = db.get_conversation_session(remetente)
    if db_session and isinstance(db_session.get("contexto"), dict):
        session = {"estado": db_session.get("estado", ""), "contexto": db_session["contexto"]}
        _SESSION_CACHE[remetente] = session
        return session
    return None


def _clear_session(db: DatabaseClient, remetente: str) -> None:
    _SESSION_CACHE.pop(remetente, None)
    db.clear_conversation_session(remetente)


def _start_guided_flow_if_requested(
    remetente: str,
    mensagem: str,
    db: DatabaseClient,
    provider_message_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    text = _normalize_text(mensagem)
    if "compra" in text:
        tipo = "compra"
    elif "venda" in text:
        tipo = "venda"
    else:
        return None

    contexto: Dict[str, Any] = {
        "tipo_operacao": tipo,
        "pagamentos": [],
        "moedas": [],
        "moeda_index": 0,
        "moeda_atual": None,
        "source_message_id": provider_message_id,
    }
    _save_session(db, remetente, "await_origem", contexto)
    return {
        "mensagem": f"Iniciando {tipo}. A operação foi balcão ou fora?",
        "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
    }


def _format_resumo(contexto: Dict[str, Any]) -> str:
    pagamentos = contexto.get("pagamentos", [])
    linhas_pagamento: List[str] = []
    for p in pagamentos:
        linhas_pagamento.append(
            f"- {p['moeda']}: {p['valor_moeda']} ({p['cambio_para_usd']} -> {p['valor_usd']} USD)"
        )
    total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
    total_operacao = Decimal(str(contexto.get("total_usd", "0")))
    diferenca = money(total_operacao - total_pago)
    linhas_pagamento_texto = "\n".join(linhas_pagamento) if linhas_pagamento else "- Sem pagamentos informados"

    return (
        "Resumo da operação:\n"
        f"- Tipo: {contexto.get('tipo_operacao')}\n"
        f"- Origem: {contexto.get('origem')}\n"
        f"- Ouro por teor: {contexto.get('teor')}%\n"
        f"- Peso: {contexto.get('peso')}g\n"
        f"- Preço USD/g: {contexto.get('preco_usd')}\n"
        f"- Total operação USD: {contexto.get('total_usd')}\n"
        f"- Fechamento: {contexto.get('fechamento_gramas')}g ({contexto.get('fechamento_tipo')})\n"
        f"- Pessoa: {contexto.get('pessoa')}\n"
        f"- Forma pagamento: {contexto.get('forma_pagamento')}\n"
        f"- Pagamentos:\n{linhas_pagamento_texto}\n"
        f"- Total pago USD: {money(total_pago)}\n"
        f"- Diferença USD: {diferenca}\n"
        "Digite 'sim' para confirmar ou 'não' para cancelar."
    )


def _build_day_range(date_str: Optional[str]) -> Dict[str, str]:
    # Use TZ_OFFSET_HOURS to convert UTC "now" to local date (default: Brazil UTC-3)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    if date_str:
        base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now + timedelta(hours=tz_offset_hours)
        base_date = local_now.date()

    start_dt = datetime(base_date.year, base_date.month, base_date.day, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "date": str(base_date)}


def _build_custom_range(start: str, end: str) -> Dict[str, str]:
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Formato inválido. Use ISO-8601 em start e end. Ex.: 2026-04-02T00:00:00+00:00",
        ) from exc

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="'end' deve ser maior que 'start'.")

    return {
        "start": start_dt.astimezone(timezone.utc).isoformat(),
        "end": end_dt.astimezone(timezone.utc).isoformat(),
    }


def _should_trigger_multi_agent_review(transaction: Dict[str, Any], force: bool = False) -> bool:
    if not _MULTI_AGENT_AUTO_ENABLED:
        return False
    if force:
        return True

    total_usd = Decimal(str(transaction.get("total_usd", transaction.get("valor_total", 0)) or 0))
    total_pago_usd = Decimal(str(transaction.get("total_pago_usd", total_usd) or total_usd))
    peso = Decimal(str(transaction.get("peso", transaction.get("quantidade", 0)) or 0))
    diferenca = abs(money(total_usd - total_pago_usd))
    tipo_operacao = str(transaction.get("tipo_operacao", "")).lower()

    return any(
        [
            diferenca >= _RISK_DIFF_LIMIT_USD,
            total_usd >= _MULTI_AGENT_AUTO_MIN_USD,
            peso >= _MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS,
            tipo_operacao in {"venda", "cambio"},
        ]
    )


def _run_automatic_multi_agent_review(
    db: DatabaseClient,
    *,
    objective: str,
    transaction: Dict[str, Any],
    operation_id: Optional[int],
    operation_kind: str,
    source_message_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    try:
        request = MultiAgentRequest(
            objective=objective,
            operation=transaction.get("tipo_operacao"),
            operation_id=operation_id,
            operation_kind=operation_kind,
            source_message_id=source_message_id,
            transaction=transaction,
            live_context=db.build_multi_agent_live_context(
                operation_id=operation_id if operation_kind == "gold_transaction" else None
            ),
            constraints={"trigger": "automatic_review"},
            rounds=2,
        )
        response = run_multi_agent_orchestration(request)
        persisted = db.save_multi_agent_run(
            objective=request.objective,
            operation_id=operation_id,
            operation_kind=operation_kind,
            source_message_id=source_message_id,
            request_payload=request.model_dump(mode="json"),
            response_payload=response.model_dump(mode="json"),
        )
        return {
            "run_id": persisted.get("id") if isinstance(persisted, dict) else None,
            "summary": response.summary,
            "decisions": response.decisions,
            "risks": response.risks,
            "recommendations": response.recommendations,
        }
    except Exception as exc:
        logger.exception("Falha na analise multiagente automatica")
        db.insert_log(
            nivel="warning",
            mensagem_recebida="AUTO_MULTI_AGENT_REVIEW_FAILED",
            contexto={
                "objective": objective,
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "transaction": transaction,
            },
            erro=str(exc),
        )
        return None


def _process_guided_flow(remetente: str, mensagem: str, db: DatabaseClient, session: Dict[str, Any]) -> Dict[str, Any]:
    estado = str(session.get("estado", ""))
    contexto = dict(session.get("contexto", {}))
    text = _normalize_text(mensagem)

    if estado == "await_origem":
        if text not in {"balcao", "balcão", "fora"}:
            return {"mensagem": "A origem deve ser 'balcão' ou 'fora'.", "dados": {"etapa": estado}}
        contexto["origem"] = "balcao" if "balcao" in text or "balcão" in text else "fora"
        _save_session(db, remetente, "await_teor", contexto)
        return {"mensagem": "Qual o teor do ouro em %? (0 a 99.99)", "dados": {"etapa": "await_teor"}}

    if estado == "await_teor":
        teor = _parse_decimal_from_text(mensagem, "teor")
        if teor < 0 or teor > Decimal("99.99"):
            return {"mensagem": "O teor deve estar entre 0 e 99.99.", "dados": {"etapa": estado}}
        contexto["teor"] = str(money(teor))
        _save_session(db, remetente, "await_peso", contexto)
        return {"mensagem": "Quantas gramas?", "dados": {"etapa": "await_peso"}}

    if estado == "await_peso":
        peso = _parse_decimal_from_text(mensagem, "peso")
        if peso <= 0:
            return {"mensagem": "O peso deve ser maior que zero.", "dados": {"etapa": estado}}
        contexto["peso"] = str(peso)
        _save_session(db, remetente, "await_preco_usd", contexto)
        return {"mensagem": "Qual o preço por grama em USD?", "dados": {"etapa": "await_preco_usd"}}

    if estado == "await_preco_usd":
        preco = _parse_decimal_from_text(mensagem, "preco_usd")
        if preco <= 0:
            return {"mensagem": "O preço deve ser maior que zero.", "dados": {"etapa": estado}}
        peso = Decimal(str(contexto.get("peso")))
        total = money(peso * preco)
        contexto["preco_usd"] = str(money(preco))
        contexto["total_usd"] = str(total)
        _save_session(db, remetente, "await_moedas", contexto)
        return {
            "mensagem": "Pagamento em quais moedas? (USD, SRD, EUR, BRL, em qualquer combinação)",
            "dados": {"etapa": "await_moedas"},
        }

    if estado == "await_moedas":
        moedas = _extract_moedas(mensagem)
        if not moedas:
            return {"mensagem": "Não identifiquei moedas. Exemplo: 'USD e SRD'.", "dados": {"etapa": estado}}
        contexto["moedas"] = moedas
        contexto["moeda_index"] = 0
        contexto["pagamentos"] = []
        contexto["moeda_atual"] = moedas[0]
        _save_session(db, remetente, "await_valor_moeda", contexto)
        return {"mensagem": f"Quanto será pago em {moedas[0]}?", "dados": {"etapa": "await_valor_moeda"}}

    if estado == "await_valor_moeda":
        moeda_atual = str(contexto.get("moeda_atual"))
        valor_moeda = _parse_decimal_from_text(mensagem, "valor_moeda")
        if valor_moeda < 0:
            return {"mensagem": "O valor da moeda não pode ser negativo.", "dados": {"etapa": estado}}
        pagamento: Dict[str, Any] = {
            "moeda": moeda_atual,
            "valor_moeda": str(money(valor_moeda)),
            "cambio_para_usd": "1",
            "valor_usd": str(money(valor_moeda)),
            "forma_pagamento": None,
        }
        pagamentos = list(contexto.get("pagamentos", []))
        pagamentos.append(pagamento)
        contexto["pagamentos"] = pagamentos

        if moeda_atual != "USD":
            _save_session(db, remetente, "await_cambio_moeda", contexto)
            return {
                "mensagem": f"Qual o câmbio do {moeda_atual} para USD?",
                "dados": {"etapa": "await_cambio_moeda"},
            }

        moedas = list(contexto.get("moedas", []))
        idx = int(contexto.get("moeda_index", 0)) + 1
        if idx < len(moedas):
            contexto["moeda_index"] = idx
            contexto["moeda_atual"] = moedas[idx]
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": f"Quanto será pago em {moedas[idx]}?", "dados": {"etapa": "await_valor_moeda"}}

        total_pago = sum((Decimal(str(p["valor_usd"])) for p in pagamentos), Decimal("0"))
        contexto["total_pago_usd"] = str(money(total_pago))
        _save_session(db, remetente, "await_fechamento_gramas", contexto)
        return {"mensagem": "Quantas gramas foram fechadas?", "dados": {"etapa": "await_fechamento_gramas"}}

    if estado == "await_cambio_moeda":
        cambio = _parse_decimal_from_text(mensagem, "cambio")
        if cambio <= 0:
            return {"mensagem": "O câmbio deve ser maior que zero.", "dados": {"etapa": estado}}
        pagamentos = list(contexto.get("pagamentos", []))
        if not pagamentos:
            _save_session(db, remetente, "await_moedas", contexto)
            return {"mensagem": "Vamos reiniciar pagamentos. Quais moedas?", "dados": {"etapa": "await_moedas"}}

        ultimo = dict(pagamentos[-1])
        valor_moeda = Decimal(str(ultimo["valor_moeda"]))
        valor_usd = money(valor_moeda / cambio)
        ultimo["cambio_para_usd"] = str(money(cambio))
        ultimo["valor_usd"] = str(valor_usd)
        pagamentos[-1] = ultimo
        contexto["pagamentos"] = pagamentos

        moedas = list(contexto.get("moedas", []))
        idx = int(contexto.get("moeda_index", 0)) + 1
        if idx < len(moedas):
            contexto["moeda_index"] = idx
            contexto["moeda_atual"] = moedas[idx]
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": f"Quanto será pago em {moedas[idx]}?", "dados": {"etapa": "await_valor_moeda"}}

        total_pago = sum((Decimal(str(p["valor_usd"])) for p in pagamentos), Decimal("0"))
        contexto["total_pago_usd"] = str(money(total_pago))
        _save_session(db, remetente, "await_fechamento_gramas", contexto)
        return {"mensagem": "Quantas gramas foram fechadas?", "dados": {"etapa": "await_fechamento_gramas"}}

    if estado == "await_fechamento_gramas":
        fechamento = _parse_decimal_from_text(mensagem, "fechamento_gramas")
        if fechamento < 0:
            return {"mensagem": "Fechamento em gramas não pode ser negativo.", "dados": {"etapa": estado}}
        contexto["fechamento_gramas"] = str(money(fechamento))
        _save_session(db, remetente, "await_fechamento_tipo", contexto)
        return {"mensagem": "Fechamento total ou parcial?", "dados": {"etapa": "await_fechamento_tipo"}}

    if estado == "await_fechamento_tipo":
        if text not in {"total", "parcial"}:
            return {"mensagem": "Digite 'total' ou 'parcial'.", "dados": {"etapa": estado}}
        contexto["fechamento_tipo"] = text
        _save_session(db, remetente, "await_pessoa", contexto)
        return {"mensagem": "Nome do vendedor/comprador?", "dados": {"etapa": "await_pessoa"}}

    if estado == "await_pessoa":
        if len(mensagem.strip()) < 2:
            return {"mensagem": "Informe um nome válido.", "dados": {"etapa": estado}}
        contexto["pessoa"] = mensagem.strip()
        _save_session(db, remetente, "await_forma_pagamento", contexto)
        return {"mensagem": "Forma de pagamento? (dinheiro, transferencia, cheque, misto)", "dados": {"etapa": "await_forma_pagamento"}}

    if estado == "await_forma_pagamento":
        forma = _normalize_text(mensagem)
        if forma not in {"dinheiro", "transferencia", "cheque", "misto"}:
            return {"mensagem": "Forma inválida. Use: dinheiro, transferencia, cheque ou misto.", "dados": {"etapa": estado}}
        contexto["forma_pagamento"] = forma
        pagamentos = list(contexto.get("pagamentos", []))
        for pagamento in pagamentos:
            pagamento["forma_pagamento"] = forma
        contexto["pagamentos"] = pagamentos
        _save_session(db, remetente, "await_observacoes", contexto)
        return {"mensagem": "Deseja adicionar observações? (ou digite 'nenhuma')", "dados": {"etapa": "await_observacoes"}}

    if estado == "await_observacoes":
        contexto["observacoes"] = "" if _normalize_text(mensagem) in {"nenhuma", "nao", "não"} else mensagem.strip()
        resumo = _format_resumo(contexto)
        _save_session(db, remetente, "await_confirmacao", contexto)
        return {"mensagem": resumo, "dados": {"etapa": "await_confirmacao", "preview": contexto}}

    if estado == "await_confirmacao":
        confirm = _extract_confirmacao(mensagem)
        if confirm is None:
            return {"mensagem": "Responda com 'sim' para confirmar ou 'não' para cancelar.", "dados": {"etapa": estado}}

        if not confirm:
            _clear_session(db, remetente)
            return {"mensagem": "Operação cancelada.", "dados": {"intencao": "fluxo_guiado_cancelado"}}

        ativo = db.get_ativo_by_nome("Ouro")
        if not ativo:
            ativo = db.get_ativo_by_nome("Ouro 24k")
        if not ativo:
            raise HTTPException(status_code=404, detail="Ativo não encontrado: Ouro")

        ativo_id = int(ativo["id"])
        peso = Decimal(str(contexto.get("peso")))
        preco = Decimal(str(contexto.get("preco_usd")))
        total = money(peso * preco)
        total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
        diferenca = money(total - total_pago)
        risco_diferenca = abs(diferenca) >= _RISK_DIFF_LIMIT_USD

        pagamentos = list(contexto.get("pagamentos", []))
        header_payload: Dict[str, Any] = {
            "tipo_operacao": str(contexto.get("tipo_operacao", "compra")),
            "origem": str(contexto.get("origem", "balcao")),
            "gold_type": "fundido",
            "quebra": None,
            "teor": contexto.get("teor"),
            "peso": str(peso),
            "preco_usd": str(money(preco)),
            "total_usd": str(total),
            "total_pago_usd": str(money(total_pago)),
            "diferenca_usd": str(diferenca),
            "fechamento_gramas": contexto.get("fechamento_gramas"),
            "fechamento_tipo": str(contexto.get("fechamento_tipo", "parcial")),
            "pessoa": str(contexto.get("pessoa", "")),
            "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
            "observacoes": contexto.get("observacoes", ""),
            "operador_id": remetente,
            "source_message_id": contexto.get("source_message_id"),
            "contexto": contexto,
            "criado_em": datetime.now(timezone.utc).isoformat(),
        }

        gold_transaction = db.insert_gold_transaction(
            payload=header_payload,
            pagamentos=pagamentos,
        )

        transacao = db.insert_transacao(
            tipo_operacao=str(contexto.get("tipo_operacao", "compra")),
            ativo_id=ativo_id,
            quantidade=peso,
            cotacao_usada=preco,
            valor_total=total,
            operador_id=remetente,
            source_message_id=contexto.get("source_message_id"),
            status="registrada",
        )

        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida="CONFIRMACAO_FLUXO_GUIADO",
            resposta_enviada="Fluxo guiado confirmado",
            contexto=contexto,
        )
        if risco_diferenca:
            db.insert_log(
                nivel="warning",
                remetente=remetente,
                mensagem_recebida="ALERTA_RISCO_DIFERENCA",
                contexto={
                    "intencao": "alerta_risco",
                    "tipo": "diferenca_alta",
                    "limite_usd": str(_RISK_DIFF_LIMIT_USD),
                    "diferenca_usd": str(diferenca),
                    "tipo_operacao": contexto.get("tipo_operacao"),
                },
                erro="Diferença de caixa acima do limite",
            )

        review_payload: Optional[Dict[str, Any]] = None
        review_transaction: Dict[str, Any] = {
            "tipo_operacao": str(contexto.get("tipo_operacao", "compra")),
            "origem": str(contexto.get("origem", "balcao")),
            "teor": contexto.get("teor"),
            "peso": str(peso),
            "preco_usd": str(money(preco)),
            "total_usd": str(total),
            "total_pago_usd": str(money(total_pago)),
            "diferenca_usd": str(diferenca),
            "fechamento_gramas": contexto.get("fechamento_gramas"),
            "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
            "transacao_id": transacao.get("id"),
        }
        if _should_trigger_multi_agent_review(review_transaction, force=risco_diferenca):
            review_payload = _run_automatic_multi_agent_review(
                db,
                objective="avaliacao automatica de operacao enterprise",
                transaction=review_transaction,
                operation_id=gold_transaction.get("id") if isinstance(gold_transaction, dict) else None,
                operation_kind="gold_transaction",
                source_message_id=contexto.get("source_message_id"),
            )

        _clear_session(db, remetente)
        alerta = "" if not risco_diferenca else " ⚠️ Atenção: diferença acima do limite de risco."
        response_payload: Dict[str, Any] = {
            "mensagem": (
                f"✅ Operação registrada com sucesso. Total USD: {money(total)}. "
                f"Pago USD: {money(total_pago)}. Diferença USD: {diferenca}.{alerta}"
            ),
            "dados": {
                "intencao": "fluxo_guiado_confirmado",
                "tipo_operacao": contexto.get("tipo_operacao"),
                "total_usd": str(money(total)),
                "total_pago_usd": str(money(total_pago)),
                "diferenca_usd": str(diferenca),
                "alerta_risco": risco_diferenca,
            },
        }
        if review_payload:
            response_payload["dados"]["analise_multiagente"] = review_payload
        return response_payload

    if estado == "await_preco_simples":
        cotacao = _parse_decimal_from_text(mensagem, "preco_usd")
        if cotacao <= 0:
            return {"mensagem": "Preço inválido. Informe o preço por grama em USD (ex: 65.50).", "dados": {"etapa": estado}}

        quantidade = Decimal(str(contexto["quantidade"]))
        total_usd = money(quantidade * cotacao)
        contexto["cotacao_usd"] = str(cotacao)
        contexto["total_usd"] = str(total_usd)
        _save_session(db, remetente, "await_moeda_simples", contexto)
        return {
            "mensagem": f"Em qual moeda foi liquidado?\nUSD / EUR / SRD / BRL",
            "dados": {"etapa": "await_moeda_simples"},
        }

    if estado == "await_moeda_simples":
        moeda = _normalize_text(mensagem).upper()
        _MOEDAS_VALIDAS = {"USD", "EUR", "SRD", "BRL"}
        if moeda not in _MOEDAS_VALIDAS:
            return {
                "mensagem": "Moeda inválida. Use: USD, EUR, SRD ou BRL.",
                "dados": {"etapa": estado},
            }
        contexto["moeda_liquidacao"] = moeda
        if moeda == "USD":
            contexto["cambio_para_usd"] = "1.0"
            return _finish_transacao_simples(db, remetente, mensagem, contexto)
        else:
            _save_session(db, remetente, "await_cambio_simples", contexto)
            return {
                "mensagem": f"Qual o câmbio?\n(1 USD = quantos {moeda})",
                "dados": {"etapa": "await_cambio_simples"},
            }

    if estado == "await_cambio_simples":
        cambio = _parse_decimal_from_text(mensagem, "cambio_para_usd")
        if cambio <= 0:
            return {
                "mensagem": "Câmbio inválido. Informe quantas unidades da moeda equivalem a 1 USD (ex: 38).",
                "dados": {"etapa": estado},
            }
        contexto["cambio_para_usd"] = str(cambio)
        return _finish_transacao_simples(db, remetente, mensagem, contexto)

    return {"mensagem": "Não consegui continuar o fluxo. Vamos reiniciar: compra ou venda?", "dados": {"etapa": "reiniciar"}}


def _finish_transacao_simples(
    db: DatabaseClient,
    remetente: str,
    mensagem: str,
    contexto: Dict[str, Any],
) -> Dict[str, Any]:
    """Persists the quick-flow transaction with moeda and câmbio, then clears session."""
    ativo_id_ctx = int(contexto["ativo_id"])
    quantidade = Decimal(str(contexto["quantidade"]))
    tipo_operacao = str(contexto["tipo_operacao"])
    nome_ativo = str(contexto.get("nome_ativo", ""))
    nome_ativo_display = "Ouro" if "ouro" in nome_ativo.lower() else nome_ativo
    source_msg_id = contexto.get("source_message_id")
    cotacao = Decimal(str(contexto["cotacao_usd"]))
    total_usd = money(Decimal(str(contexto["total_usd"])))
    moeda = str(contexto.get("moeda_liquidacao", "USD")).upper()
    cambio = Decimal(str(contexto.get("cambio_para_usd", "1.0")))
    valor_moeda = money(total_usd * cambio)

    transacao = db.insert_transacao(
        tipo_operacao=tipo_operacao,
        ativo_id=ativo_id_ctx,
        quantidade=quantidade,
        cotacao_usada=cotacao,
        valor_total=total_usd,
        operador_id=remetente,
        source_message_id=source_msg_id,
        status="registrada",
        moeda_liquidacao=moeda,
        valor_moeda=valor_moeda,
        cambio_para_usd=cambio,
    )

    # Generate unique operation ID
    transacao_id = transacao.get("id")
    tz_offset = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    data_agora = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
    data_str = data_agora.strftime("%Y%m%d")
    op_id = f"OP-{data_str}-{transacao_id:05d}" if transacao_id else "OP-UNKNOWN"

    review_payload: Optional[Dict[str, Any]] = None
    review_transaction: Dict[str, Any] = {
        "tipo_operacao": tipo_operacao,
        "ativo": nome_ativo_display,
        "quantidade": str(quantidade),
        "peso": str(quantidade),
        "preco_usd": str(money(cotacao)),
        "valor_total": str(total_usd),
        "total_usd": str(total_usd),
        "total_pago_usd": str(total_usd),
    }
    if _should_trigger_multi_agent_review(review_transaction):
        review_payload = _run_automatic_multi_agent_review(
            db,
            objective="avaliacao automatica de operacao via webhook",
            transaction=review_transaction,
            operation_id=transacao.get("id"),
            operation_kind="transacao",
            source_message_id=source_msg_id,
        )

    operacao_texto = {
        "compra": "Compra registrada",
        "venda": "Venda registrada",
        "cambio": "Câmbio registrado",
    }.get(tipo_operacao, "Operação registrada")

    _clear_session(db, remetente)

    if moeda == "USD":
        moeda_linha = f"${total_usd} USD"
    else:
        moeda_linha = f"{valor_moeda} {moeda} (câmbio: 1 USD = {cambio} {moeda})"

    # Professional receipt format with operation ID
    tipo_icon = {
        "compra": "💳",
        "venda": "💰",
        "cambio": "🔄",
    }.get(tipo_operacao, "📝")
    
    data_hora = datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "-3")))
    data_fmt = data_hora.strftime("%d/%m/%Y %H:%M:%S")

    response_payload: Dict[str, Any] = {
        "mensagem": (
            f"✅ {operacao_texto}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 ID: {op_id}\n"
            f"📅 {data_fmt}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{tipo_icon} {tipo_operacao.upper()}\n"
            f"Ativo: {nome_ativo_display}\n"
            f"Qtd: {quantidade}g\n"
            f"Preço: ${money(cotacao)}/g\n"
            f"Total: ${total_usd} USD\n"
            f"Moeda: {moeda_linha}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ Operação concluída"
        ),
        "dados": {
            "intencao": "registrar_operacao",
            "tipo_operacao": tipo_operacao,
            "ativo": nome_ativo_display,
            "operacao_id": op_id,
            "quantidade": str(quantidade),
            "cotacao_usada": str(money(cotacao)),
            "valor_total_usd": str(total_usd),
            "moeda_liquidacao": moeda,
            "valor_moeda": str(valor_moeda),
            "cambio_para_usd": str(cambio),
        },
    }
    if review_payload:
        response_payload["dados"]["analise_multiagente"] = review_payload
    db.insert_log(
        nivel="info",
        remetente=remetente,
        mensagem_recebida=mensagem,
        resposta_enviada=response_payload["mensagem"],
        contexto=response_payload["dados"],
    )
    return response_payload
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    x_provider_message_id: Optional[str] = Header(default=None, alias="X-Provider-Message-Id"),
    x_twilio_message_sid: Optional[str] = Header(default=None, alias="X-Twilio-MessageSid"),
) -> Dict[str, Any]:
    provider_message_id = x_provider_message_id or x_twilio_message_sid
    raw_body: Any = {}
    raw_text = ""
    body_data: Dict[str, Any] = {}
    payload: Optional[WhatsAppWebhookPayload] = None

    try:
        raw_text = (await request.body()).decode("utf-8", errors="ignore")
    except Exception:
        raw_text = ""

    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            body_data = cast(Dict[str, Any], raw_body)
    except Exception:
        body_data = {}

    # Twilio/Pipedream frequently send application/x-www-form-urlencoded.
    if not body_data:
        try:
            form = await request.form()
            body_data = dict(form)
        except Exception:
            body_data = {}

    # Fallback parser for form-urlencoded when request.form() is unavailable.
    if not body_data:
        try:
            parsed = parse_qs(raw_text)
            body_data = {k: v[0] for k, v in parsed.items() if v}
        except Exception:
            body_data = {}

    try:
        payload = WhatsAppWebhookPayload(
            remetente=str(body_data.get("remetente") or body_data.get("From") or "").strip(),
            mensagem=str(body_data.get("mensagem") or body_data.get("Body") or "").strip(),
        )
    except ValidationError:
        raise HTTPException(status_code=400, detail="Payload inválido: informe remetente e mensagem.")

    # Allow token from header, query (?token=...), or body field for easy Pipedream wiring.
    token = x_webhook_token or request.query_params.get("token") or body_data.get("token")
    provider_message_id = (
        provider_message_id
        or str(body_data.get("provider_message_id") or "").strip()
        or str(body_data.get("MessageSid") or "").strip()
        or None
    )

    remetente = payload.remetente.strip().replace("whatsapp:", "")
    mensagem = payload.mensagem.strip()
    db: Optional[DatabaseClient] = None

    try:
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()

        if provider_message_id:
            existing = db.get_processed_message(provider_message_id)
            if existing and isinstance(existing.get("resposta_payload"), dict):
                return existing["resposta_payload"]
            cached = _IDEMPOTENCY_CACHE.get(provider_message_id)
            if cached:
                return cached

        response = _processar_webhook(payload, db, provider_message_id)

        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=200,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response

        return response
    except HTTPException as exc:
        msg = _ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail))
        response: Dict[str, Any] = {
            "mensagem": f"⚠️ {msg}",
            "dados": {"erro": exc.status_code, "detalhe": exc.detail},
        }
        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=exc.status_code,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response
        return response
    except Exception:
        logger.exception("Erro inesperado no webhook")
        response = {
            "mensagem": "⚠️ Ocorreu um erro inesperado. Tente novamente.",
            "dados": {"erro": 500},
        }
        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=500,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response
        return response


@app.post("/webhook/twilio")
async def whatsapp_webhook_twilio(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    x_twilio_message_sid: Optional[str] = Header(default=None, alias="X-Twilio-MessageSid"),
) -> Response:
    body_data: Dict[str, Any] = {}
    try:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        parsed = parse_qs(raw)
        body_data = {k: v[0] for k, v in parsed.items() if v}
    except Exception:
        body_data = {}

    token = x_webhook_token or request.query_params.get("token") or body_data.get("token")
    provider_message_id = (
        x_twilio_message_sid
        or str(body_data.get("MessageSid") or "").strip()
        or None
    )

    remetente = str(body_data.get("From") or "").strip().replace("whatsapp:", "")
    mensagem = str(body_data.get("Body") or "").strip()

    if not remetente or not mensagem:
        return _twiml_message("⚠️ Payload inválido: informe remetente e mensagem.")

    payload = WhatsAppWebhookPayload(remetente=remetente, mensagem=mensagem)
    db: Optional[DatabaseClient] = None

    try:
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()

        if provider_message_id:
            existing = db.get_processed_message(provider_message_id)
            if existing and isinstance(existing.get("resposta_payload"), dict):
                return _twiml_message(str(existing["resposta_payload"].get("mensagem") or ""))
            cached = _IDEMPOTENCY_CACHE.get(provider_message_id)
            if cached:
                return _twiml_message(str(cached.get("mensagem") or ""))

        response = _processar_webhook(payload, db, provider_message_id)

        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response,
                status_code=200,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response

        return _twiml_message(str(response.get("mensagem") or "Operação processada."))
    except HTTPException as exc:
        msg = _ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail))
        response_payload: Dict[str, Any] = {
            "mensagem": f"⚠️ {msg}",
            "dados": {"erro": exc.status_code, "detalhe": exc.detail},
        }
        if db and provider_message_id:
            db.save_processed_message(
                provider_message_id=provider_message_id,
                remetente=remetente,
                mensagem_recebida=mensagem,
                resposta_payload=response_payload,
                status_code=exc.status_code,
            )
            _IDEMPOTENCY_CACHE[provider_message_id] = response_payload
        return _twiml_message(response_payload["mensagem"])
    except Exception:
        logger.exception("Erro inesperado no webhook Twilio")
        return _twiml_message("⚠️ Ocorreu um erro inesperado. Tente novamente.")


@app.get("/reports/daily-closure")
def daily_closure_report(date: Optional[str] = None) -> Dict[str, Any]:
    db = get_db()
    day = _build_day_range(date)
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    by_operator = db.get_daily_gold_summary_by_operator(day["start"], day["end"])
    return {
        "date": day["date"],
        "summary": summary,
        "by_operator": by_operator,
    }


@app.get("/reports/risk-alerts")
def risk_alerts_report(date: Optional[str] = None) -> Dict[str, Any]:
    db = get_db()
    day = _build_day_range(date)
    alerts = db.get_risk_alerts(day["start"], day["end"])
    return {
        "date": day["date"],
        "total_alertas": len(alerts),
        "alerts": alerts,
    }


@app.get("/reports/closure-range")
def closure_range_report(start: str, end: str) -> Dict[str, Any]:
    db = get_db()
    rng = _build_custom_range(start, end)
    summary = db.get_gold_summary_range(rng["start"], rng["end"])
    by_operator = db.get_daily_gold_summary_by_operator(rng["start"], rng["end"])
    return {
        "range": rng,
        "summary": summary,
        "by_operator": by_operator,
    }


@app.get("/reports/reconciliation-by-currency")
def reconciliation_by_currency_report(start: str, end: str) -> Dict[str, Any]:
    db = get_db()
    rng = _build_custom_range(start, end)
    by_currency = db.get_gold_summary_by_currency(rng["start"], rng["end"])
    return {
        "range": rng,
        "by_currency": by_currency,
    }


@app.get("/reports/closure-csv")
def closure_csv_report(start: str, end: str) -> Response:
    db = get_db()
    rng = _build_custom_range(start, end)
    summary = db.get_gold_summary_range(rng["start"], rng["end"])
    by_operator = db.get_daily_gold_summary_by_operator(rng["start"], rng["end"])
    by_currency = db.get_gold_summary_by_currency(rng["start"], rng["end"])

    lines: List[str] = [
        "section,key,value",
        f"summary,total_operacoes,{summary.get('total_operacoes', 0)}",
        f"summary,total_usd,{summary.get('total_usd', '0')}",
        f"summary,total_pago_usd,{summary.get('total_pago_usd', '0')}",
        f"summary,total_diferenca_usd,{summary.get('total_diferenca_usd', '0')}",
        "",
        "operators,operador_id,total_operacoes,total_usd,total_pago_usd,total_diferenca_usd",
    ]

    for row in by_operator:
        lines.append(
            "operators,"
            f"{row.get('operador_id', '')},"
            f"{row.get('total_operacoes', 0)},"
            f"{row.get('total_usd', '0')},"
            f"{row.get('total_pago_usd', '0')},"
            f"{row.get('total_diferenca_usd', '0')}"
        )

    lines.extend([
        "",
        "currency,moeda,total_pagamentos,total_valor_moeda,total_valor_usd",
    ])

    for row in by_currency:
        lines.append(
            "currency,"
            f"{row.get('moeda', '')},"
            f"{row.get('total_pagamentos', 0)},"
            f"{row.get('total_valor_moeda', '0')},"
            f"{row.get('total_valor_usd', '0')}"
        )

    csv_content = "\n".join(lines)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=closure_report.csv",
        },
    )


@app.get("/reports/top-divergences")
def top_divergences_report(start: str, end: str, limit: int = 10) -> Dict[str, Any]:
    db = get_db()
    rng = _build_custom_range(start, end)
    rows = db.get_top_divergences(rng["start"], rng["end"], limit=limit)
    return {
        "range": rng,
        "limit": max(limit, 1),
        "items": rows,
    }


@app.get("/reports/audit/operation/{operation_id}")
def operation_audit_report(operation_id: int) -> Dict[str, Any]:
    db = get_db()
    result = db.get_gold_operation_audit(operation_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Operação não encontrada: {operation_id}")
    return result


@app.post("/ai/multi-agent/analyze", response_model=MultiAgentResponse)
def multi_agent_analyze(request: MultiAgentRequest) -> MultiAgentResponse:
    db = get_db()
    live_context = db.build_multi_agent_live_context(operation_id=request.operation_id)
    merged_live_context = dict(request.live_context)
    merged_live_context.update(live_context)

    enriched_request = request.model_copy(update={"live_context": merged_live_context})
    response = run_multi_agent_orchestration(enriched_request)

    db.save_multi_agent_run(
        objective=enriched_request.objective,
        operation_id=enriched_request.operation_id,
        operation_kind=enriched_request.operation_kind,
        source_message_id=enriched_request.source_message_id,
        request_payload=enriched_request.model_dump(mode="json"),
        response_payload=response.model_dump(mode="json"),
    )
    return response


@app.get("/ai/multi-agent/runs")
def multi_agent_recent_runs(limit: int = 10) -> Dict[str, Any]:
    db = get_db()
    safe_limit = max(1, min(limit, 50))
    return {
        "limit": safe_limit,
        "items": db.get_recent_multi_agent_runs(limit=safe_limit),
    }


def _processar_webhook(
    payload: WhatsAppWebhookPayload,
    db: DatabaseClient,
    provider_message_id: Optional[str],
) -> Dict[str, Any]:
    remetente = payload.remetente.strip()
    mensagem = payload.mensagem.strip()
    raw_ai_data: Dict[str, Any] = {}
    usuario = db.get_usuario_by_telefone(remetente)

    if not usuario:
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            erro="Remetente não autorizado",
        )
        raise HTTPException(status_code=403, detail="Remetente não autorizado.")

    session = _get_session(db, remetente)
    if session:
        estado = str(session.get("estado", ""))
        if estado in _GUIDED_FLOW_STATES:
            return _process_guided_flow(remetente, mensagem, db, session)

    maybe_start = _start_guided_flow_if_requested(remetente, mensagem, db, provider_message_id)
    if maybe_start:
        return maybe_start

    try:
        raw_ai_data = extract_message_data(mensagem)
        ai_data = AIExtractedData.model_validate(raw_ai_data)
    except AIServiceError as exc:
        logger.exception("Falha ao extrair dados da IA")
        db.insert_log(
            nivel="error",
            remetente=remetente,
            mensagem_recebida=mensagem,
            erro=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValidationError as exc:
        logger.exception("Payload da IA inválido")
        db.insert_log(
            nivel="error",
            remetente=remetente,
            mensagem_recebida=mensagem,
            contexto={"ia_payload": raw_ai_data},
            erro=str(exc),
        )
        raise HTTPException(status_code=400, detail="IA retornou JSON fora do contrato esperado.") from exc

    intencao = ai_data.intencao
    ativo_extraido = ai_data.ativo

    if intencao == "conversar":
        if _is_help_menu_request(mensagem):
            resposta = _build_whatsapp_checklist_menu()
        else:
            resposta = ai_data.resposta or (
                "Posso te ajudar com operacoes, extrato e taxas. "
                "Se quiser, envie 'menu' para ver um checklist completo."
            )
        response_payload: Dict[str, Any] = {
            "mensagem": resposta,
            "dados": {"intencao": intencao},
        }
        db.save_conversation_session(
            remetente=remetente,
            estado="conversando",
            contexto={"ultima_mensagem": mensagem, "ultima_intencao": intencao},
        )
        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida=mensagem,
            resposta_enviada=resposta,
            contexto={"intencao": intencao},
        )
        return response_payload

    if intencao == "consultar_relatorio":
        day = _build_day_range(None)
        summary = db.get_daily_gold_summary(day["start"], day["end"])
        by_operator = db.get_daily_gold_summary_by_operator(day["start"], day["end"])
        total_operacoes = int(summary.get("total_operacoes", 0) or 0)
        total_usd = summary.get("total_usd", "0")
        total_pago_usd = summary.get("total_pago_usd", "0")
        total_diferenca_usd = summary.get("total_diferenca_usd", "0")

        resposta = (
            "📊 Extrato de hoje\n"
            f"Operações: {total_operacoes}\n"
            f"Total USD: {total_usd}\n"
            f"Total pago USD: {total_pago_usd}\n"
            f"Diferença USD: {total_diferenca_usd}"
        )
        response_payload = {
            "mensagem": resposta,
            "dados": {
                "intencao": intencao,
                "date": day["date"],
                "summary": summary,
                "by_operator": by_operator,
            },
        }
        saldo = db.get_saldo_caixa()
        gold_gramas = saldo.get("gold_gramas", "0")
        moedas = saldo.get("moedas", {})

        _MOEDA_SIMBOLO = {"USD": "$", "EUR": "€", "SRD": "Sf", "BRL": "R$"}
        _MOEDA_FLAG   = {"USD": "💵", "EUR": "💶", "SRD": "🇸🇷", "BRL": "🇧🇷"}
        _MOEDA_ORDEM  = ["USD", "EUR", "SRD", "BRL"]

        linhas_moeda: list[str] = []
        for m in _MOEDA_ORDEM:
            val_str = moedas.get(m, "0")
            val = Decimal(val_str)
            simbolo = _MOEDA_SIMBOLO.get(m, m)
            flag = _MOEDA_FLAG.get(m, "💰")
            sinal = "+" if val >= 0 else ""
            linhas_moeda.append(f"{flag} {m}:  {sinal}{simbolo}{val:,.2f}")

        # Also include any unexpected currency in saldo
        for m, val_str in moedas.items():
            if m not in _MOEDA_ORDEM:
                val = Decimal(val_str)
                sinal = "+" if val >= 0 else ""
                linhas_moeda.append(f"💰 {m}:  {sinal}{val:,.2f}")

        moedas_txt = "\n".join(linhas_moeda) if linhas_moeda else "Sem movimentações"

        # Today's activity count
        day = _build_day_range(None)
        summary = db.get_daily_gold_summary(day["start"], day["end"])
        ops_hoje = int(summary.get("total_operacoes", 0) or 0)

        ouro_val = Decimal(gold_gramas)
        sinal_ouro = "+" if ouro_val >= 0 else ""

        resposta = (
            f"📊 CAIXA — {day['date']}\n"
            "────────────────────────\n"
            f"🥇 Ouro em estoque: {sinal_ouro}{ouro_val:,.3f} g\n"
            "────────────────────────\n"
            f"{moedas_txt}\n"
            "────────────────────────\n"
            f"📈 Operações hoje: {ops_hoje}"
        )
        response_payload = {
            "mensagem": resposta,
            "dados": {
                "intencao": intencao,
                "date": day["date"],
                "gold_gramas": gold_gramas,
                "moedas": moedas,
                "ops_hoje": ops_hoje,
            },
        }
        db.save_conversation_session(
            remetente=remetente,
            estado="conversando",
            contexto={"ultima_mensagem": mensagem, "ultima_intencao": intencao},
        )
        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida=mensagem,
            resposta_enviada=resposta,
            contexto={"intencao": intencao, "date": day["date"]},
        )
        return response_payload

    nome_ativo = normalize_ativo_nome(ativo_extraido or "")
    ativo = db.get_ativo_by_nome(nome_ativo)

    if not ativo:
        raise HTTPException(status_code=404, detail=f"Ativo não encontrado: {nome_ativo}")

    ativo_id = int(ativo["id"])

    if intencao == "atualizar_taxa":
        if usuario.get("tipo_usuario") != "admin":
            db.insert_log(
                nivel="warning",
                remetente=remetente,
                mensagem_recebida=mensagem,
                contexto={"intencao": intencao},
                erro="Operador sem permissão para atualizar taxa",
            )
            raise HTTPException(status_code=403, detail="Apenas o Admin pode atualizar taxa.")

        valor_informado = ai_data.valor_informado
        if valor_informado is None:
            valor_informado = ai_data.quantidade
        nova_taxa = parse_decimal(valor_informado, "valor_informado")

        if nova_taxa <= 0:
            raise HTTPException(status_code=400, detail="Taxa deve ser maior que zero.")

        db.insert_taxa_diaria(ativo_id=ativo_id, preco=nova_taxa, admin_id=remetente)
        response_payload: Dict[str, Any] = {
            "mensagem": f"✅ Taxa do {(ativo_extraido or ativo['nome']).lower()} atualizada para ${money(nova_taxa)}",
            "dados": {
                "intencao": intencao,
                "ativo": ativo["nome"],
                "taxa": str(money(nova_taxa)),
            },
        }
        db.insert_log(
            nivel="info",
            remetente=remetente,
            mensagem_recebida=mensagem,
            resposta_enviada=response_payload["mensagem"],
            contexto=response_payload["dados"],
        )
        db.save_conversation_session(
            remetente=remetente,
            estado="operacao_finalizada",
            contexto={"ultima_mensagem": mensagem, "ultima_intencao": intencao},
        )
        return response_payload

    if intencao == "registrar_operacao":
        quantidade = parse_decimal(ai_data.quantidade, "quantidade")
        if quantidade <= 0:
            raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero.")

        tipo_operacao = infer_tipo_operacao(mensagem)
        valor_informado = ai_data.valor_informado

        contexto: Dict[str, Any] = {
            "ativo_id": ativo_id,
            "nome_ativo": ativo["nome"],
            "quantidade": str(quantidade),
            "tipo_operacao": tipo_operacao,
            "source_message_id": provider_message_id,
        }

        # Se o preço já foi informado, pula direto para perguntar moeda
        if valor_informado is not None and valor_informado > 0:
            cotacao = parse_decimal(valor_informado, "valor_informado")
            total_usd = money(quantidade * cotacao)
            contexto["cotacao_usd"] = str(cotacao)
            contexto["total_usd"] = str(total_usd)
            db.save_conversation_session(
                remetente=remetente,
                estado="await_moeda_simples",
                contexto=contexto,
            )
            return {
                "mensagem": f"Em qual moeda foi liquidado?\nUSD / EUR / SRD / BRL",
                "dados": {"etapa": "await_moeda_simples"},
            }

        # Senão, pede o preço
        db.save_conversation_session(
            remetente=remetente,
            estado="await_preco_simples",
            contexto=contexto,
        )

        operacao_texto = {
            "compra": "compra",
            "venda": "venda",
            "cambio": "câmbio",
        }.get(tipo_operacao, "operação")

        return {
            "mensagem": f"Qual o preço por grama em USD para essa {operacao_texto} de {quantidade}g?",
            "dados": {"etapa": "await_preco_simples"},
        }

    raise HTTPException(status_code=400, detail=f"Intenção não suportada: {intencao}")


@app.post("/operations/{operation_id}/edit")
async def edit_operation(
    operation_id: int,
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
) -> Dict[str, Any]:
    """Edit an operation (only by the operator who created it)."""
    token = x_webhook_token or request.query_params.get("token")
    validate_webhook_token(str(token) if token is not None else None)
    db = get_db()

    transacao = (
        db.client.table("transacoes")
        .select("*")
        .eq("id", operation_id)
        .limit(1)
        .execute()
    )
    rows = cast(List[Dict[str, Any]], transacao.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Operação não encontrada: {operation_id}")

    body = await request.json()
    
    # Allow editing: quantidade, cotacao_usada, moeda_liquidacao, valor_moeda
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
    """Delete/cancel an operation."""
    token = x_webhook_token or request.query_params.get("token")
    validate_webhook_token(str(token) if token is not None else None)
    db = get_db()

    transacao = (
        db.client.table("transacoes")
        .select("*")
        .eq("id", operation_id)
        .limit(1)
        .execute()
    )
    rows = cast(List[Dict[str, Any]], transacao.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Operação não encontrada: {operation_id}")

    # Mark as cancelled instead of deleting
    db.client.table("transacoes").update({"status": "cancelada"}).eq("id", operation_id).execute()

    return {
        "mensagem": f"✅ Operação OP-{operation_id} cancelada",
        "dados": {"id": operation_id, "status": "cancelada"},
    }
