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
        if normalized not in {"registrar_operacao", "consultar_relatorio", "conversar"}:
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


def fx_rate(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def validate_webhook_token(token: Optional[str]) -> None:
    expected = os.getenv("WEBHOOK_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="Token do sistema nao configurado")
    if token != expected:
        raise HTTPException(status_code=401, detail="Token invalido")


def _twiml_message(text: str) -> Response:
    safe_text = escape(text)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe_text}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _twiml_empty_response() -> Response:
    xml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=xml, media_type="application/xml")


def _should_suppress_twilio_reply(message: str) -> bool:
    mode = os.getenv("TWILIO_REPLY_MODE", "normal").strip().lower()
    if mode == "silent_all":
        return True
    if mode != "silent_prefix":
        return False

    prefix = os.getenv("TWILIO_SILENT_PREFIX", "debug:").strip().lower()
    if not prefix:
        return False
    return message.strip().lower().startswith(prefix)


@app.get("/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/menu")
def menu() -> Dict[str, Any]:
    return {
        "titulo": "Menu",
        "versao": "1.0.0",
        "funcionalidades": [
            {
                "id": 1,
                "nome": "Registrar compra ou venda",
                "intencao": "registrar_operacao",
                "descricao": "Registra operacao de ouro com passos guiados.",
                "exemplos": [
                    "Comprei 2g de ouro",
                    "Vendi 3g de ouro",
                    "Comprei 2g de ouro a 105"
                ],
                "resposta_esperada": "Retorna comprovante da operacao."
            },
            {
                "id": 2,
                "nome": "Consultar saldo",
                "intencao": "consultar_relatorio",
                "descricao": "Mostra saldo atual por moeda e total de ouro em estoque.",
                "exemplos": [
                    "caixa",
                    "caixa eur",
                    "caixa srd",
                    "caixa xau"
                ],
                "resposta_esperada": "Retorna saldos atuais."
            },
            {
                "id": 3,
                "nome": "Extrato detalhado",
                "intencao": "extrato",
                "descricao": "Lista todas as operacoes do periodo com detalhes de cada lancamento.",
                "exemplos": [
                    "extrato",
                    "extrato hoje",
                    "extrato semana"
                ],
                "resposta_esperada": "Retorna extrato detalhado no estilo bancario."
            },
            {
                "id": 4,
                "nome": "Editar operacao",
                "intencao": "editar_operacao",
                "descricao": "Altera preco, quantidade, moeda, valor_moeda ou cambio de uma operacao existente.",
                "exemplos": [
                    "editar 123 preco 110",
                    "editar 123 quantidade 2.5"
                ],
                "resposta_esperada": "Confirma o que foi alterado."
            },
            {
                "id": 5,
                "nome": "Cancelar operacao",
                "intencao": "cancelar_operacao",
                "descricao": "Marca a operacao como cancelada.",
                "exemplos": [
                    "cancelar 123"
                ],
                "resposta_esperada": "Confirma cancelamento."
            }
        ],
        "ativos_disponiveis": [
            {"nome": "ouro", "aliases": ["gold", "oro", "or"]},
            {"nome": "usd", "aliases": ["dollar", "dolar"]},
            {"nome": "eur", "aliases": ["euro"]},
            {"nome": "srd", "aliases": []},
            {"nome": "brl", "aliases": ["real", "reais"]}
        ],
        "dicas": [
            "Use frases objetivas.",
            "Responda uma informacao por vez.",
            "Em caso de duvida, envie: menu",
            "Para corrigir etapa atual, envie: voltar"
        ]
    }


_ERROS_AMIGAVEIS: Dict[int, str] = {
    400: "Não entendi. Tente assim: Comprei 2g de ouro a 105",
    401: "Acesso negado. Token inválido.",
    403: "Você não tem permissão para isso.",
    404: "Recurso não encontrado. Digite 'menu' para ver as opções.",
    422: "Dados incompletos. Tente com uma mensagem mais objetiva.",
    500: "Erro interno. Tente novamente em alguns segundos.",
    502: "O serviço de IA não respondeu. Tente novamente.",
}

# Fallback de idempotência para ambiente sem migração aplicada.
_IDEMPOTENCY_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}

_MOEDAS_SUPORTADAS = ["USD", "SRD", "EUR", "BRL"]
_RISK_DIFF_LIMIT_USD = Decimal(os.getenv("RISK_DIFF_LIMIT_USD", "250"))
_GUIDED_SESSION_IDLE_MINUTES = int(os.getenv("GUIDED_SESSION_IDLE_MINUTES", "5"))
_MULTI_AGENT_AUTO_ENABLED = os.getenv("MULTI_AGENT_AUTO_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
_MULTI_AGENT_AUTO_MIN_USD = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_USD", "500"))
_MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS", "10"))
_GUIDED_FLOW_STATES = {
    "await_menu_option",
    "await_menu_tipo_operacao",
    "await_nome_usuario",
    "await_origem",
    "await_teor",
    "await_peso",
    "await_preco_moeda",
    "await_preco_usd",
    "await_preco_cambio",
    "await_cambio_base_para_total",
    "await_moedas",
    "await_valor_moeda",
    "await_cambio_moeda_pre_valor",
    "await_cambio_moeda",
    "await_fechamento_gramas",
    "await_fechamento_tipo",
    "await_pessoa",
    "await_forma_pagamento",
    "await_observacoes",
    "await_confirmacao",
    "await_resume_confirmacao",
    "await_preco_simples",
    "await_moeda_simples",
    "await_cambio_simples",
    "await_extrato_periodo",
    "await_extrato_data_inicio",
    "await_extrato_data_fim",
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


def _build_cambio_prompt(moeda: str) -> str:
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "EUR":
        return "1 EUR = quantos USD?"
    return f"1 USD = quantos {moeda_up}?"


def _normalize_cambio_para_usd(moeda: str, cambio_informado: Decimal) -> Decimal:
    """Normalize user input to the internal format: quote_currency per 1 USD."""
    moeda_up = str(moeda or "USD").upper()
    if moeda_up == "EUR":
        # User informs USD per EUR (strong -> weak), so invert to EUR per USD.
        return fx_rate(Decimal("1") / cambio_informado)
    return fx_rate(cambio_informado)


def _try_set_total_usd_from_base_rate(contexto: Dict[str, Any], cambio_base_para_usd: Decimal) -> bool:
    """Set preco_usd/total_usd when the base-pricing currency exchange rate becomes available."""
    preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
    if preco_moeda == "USD":
        return bool(contexto.get("total_usd"))

    preco_moeda_valor_raw = contexto.get("preco_moeda_valor")
    peso_raw = contexto.get("peso")
    if preco_moeda_valor_raw is None or peso_raw is None:
        return False

    preco_moeda_valor = Decimal(str(preco_moeda_valor_raw))
    peso = Decimal(str(peso_raw))
    preco_usd = money(preco_moeda_valor / cambio_base_para_usd)
    total_usd = money(preco_usd * peso)
    contexto["cambio_preco_moeda"] = str(fx_rate(cambio_base_para_usd))
    contexto["preco_usd"] = str(preco_usd)
    contexto["total_usd"] = str(total_usd)
    return True


def _guided_prompt_for_state(state: str, contexto: Dict[str, Any]) -> str:
    if state == "await_origem":
        return "Passo 0: local da operação (balcão ou fora)?"
    if state == "await_teor":
        return "Passo 1: qual o teor do ouro em %? Exemplo: 91,6"
    if state == "await_peso":
        return "Passo 2: quantas gramas? Exemplo: 2,5"
    if state == "await_preco_moeda":
        return "Passo 2.5: qual a moeda base da precificação? (USD, EUR, SRD ou BRL)"
    if state == "await_preco_usd":
        return "Passo 3: qual o preço por grama? Exemplo: 115 USD"
    if state == "await_preco_cambio":
        moeda_preco = str(contexto.get("preco_moeda") or "EUR").upper()
        return f"Passo 4: informe o câmbio. Exemplo: {_build_cambio_prompt(moeda_preco)}"
    if state == "await_cambio_base_para_total":
        moeda_preco = str(contexto.get("preco_moeda") or "EUR").upper()
        return f"Passo 4.5: para fechar o total em USD, informe o câmbio da moeda-base ({_build_cambio_prompt(moeda_preco)})"
    if state == "await_moedas":
        return "Passo 5: em quais moedas foi pago? Use: USD, EUR, SRD, BRL"
    if state == "await_valor_moeda":
        moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
        return f"Passo 6: quanto será pago em {moeda_atual}?"
    if state == "await_cambio_moeda_pre_valor":
        moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
        return f"Passo 6.5: informe o câmbio de {moeda_atual} antes do valor ({_build_cambio_prompt(moeda_atual)})"
    if state == "await_cambio_moeda":
        moeda_atual = str(contexto.get("moeda_atual") or "a moeda")
        return f"Passo 7: informe o câmbio ({_build_cambio_prompt(moeda_atual)})"
    if state == "await_fechamento_gramas":
        return "Passo 8: quantas gramas foram fechadas? (use quando for venda/câmbio)"
    if state == "await_fechamento_tipo":
        return "Passo 9: fechamento total ou parcial?"
    if state == "await_pessoa":
        return "Passo 10: nome da pessoa?"
    if state == "await_forma_pagamento":
        return "Passo 11: forma de pagamento (dinheiro, transferência, cheque, misto)"
    if state == "await_observacoes":
        return "Passo 12: observações (ou digite 'nenhuma')"
    return "Continue informando os dados solicitados."


def _guided_clear_from_step(contexto: Dict[str, Any], target_state: str) -> Dict[str, Any]:
    cleared = dict(contexto)
    order = [
        "await_teor",
        "await_peso",
        "await_preco_usd",
        "await_preco_cambio",
        "await_cambio_base_para_total",
        "await_moedas",
        "await_valor_moeda",
        "await_cambio_moeda_pre_valor",
        "await_cambio_moeda",
        "await_fechamento_gramas",
        "await_fechamento_tipo",
        "await_pessoa",
        "await_forma_pagamento",
        "await_observacoes",
    ]
    fields_by_step: Dict[str, List[str]] = {
        "await_teor": ["teor"],
        "await_peso": ["peso"],
        "await_preco_usd": ["preco_moeda", "preco_moeda_valor", "total_moeda", "preco_usd", "cambio_preco_moeda", "total_usd"],
        "await_preco_cambio": ["cambio_preco_moeda", "preco_usd", "total_usd"],
        "await_cambio_base_para_total": ["cambio_preco_moeda", "preco_usd", "total_usd"],
        "await_moedas": ["moedas", "moeda_index", "moeda_atual", "pagamentos", "total_pago_usd"],
        "await_valor_moeda": ["pagamentos", "total_pago_usd"],
        "await_cambio_moeda_pre_valor": ["cambio_moeda_atual_pre", "pagamentos", "total_pago_usd"],
        "await_cambio_moeda": ["pagamentos", "total_pago_usd"],
        "await_fechamento_gramas": ["fechamento_gramas", "fechamento_tipo", "pessoa", "forma_pagamento", "observacoes"],
        "await_fechamento_tipo": ["fechamento_tipo", "pessoa", "forma_pagamento", "observacoes"],
        "await_pessoa": ["pessoa", "forma_pagamento", "observacoes"],
        "await_forma_pagamento": ["forma_pagamento", "observacoes"],
        "await_observacoes": ["observacoes"],
    }

    start_clearing = False
    for step in order:
        if step == target_state:
            start_clearing = True
        if start_clearing:
            for field in fields_by_step.get(step, []):
                cleared.pop(field, None)
    return cleared


def _guided_try_back_command(
    remetente: str,
    mensagem: str,
    estado: str,
    contexto: Dict[str, Any],
    db: DatabaseClient,
) -> Optional[Dict[str, Any]]:
    text = _normalize_text(mensagem)
    if not (text.startswith("voltar") or text.startswith("editar") or text.startswith("corrigir")):
        return None

    aliases: Dict[str, str] = {
        "teor": "await_teor",
        "peso": "await_peso",
        "gramas": "await_peso",
        "preco": "await_preco_usd",
        "preco usd": "await_preco_usd",
        "cotacao": "await_preco_usd",
        "cambio preco": "await_preco_cambio",
        "cambio base": "await_cambio_base_para_total",
        "moedas": "await_moedas",
        "moeda": "await_moedas",
        "pagamento": "await_valor_moeda",
        "valor": "await_valor_moeda",
        "cambio": "await_cambio_moeda",
        "cambio moeda": "await_cambio_moeda_pre_valor",
        "fechamento": "await_fechamento_gramas",
        "pessoa": "await_pessoa",
        "nome": "await_pessoa",
        "forma": "await_forma_pagamento",
        "observacoes": "await_observacoes",
        "observacao": "await_observacoes",
    }

    # "voltar" simples = etapa anterior mais segura
    if text in {"voltar", "corrigir", "editar"}:
        tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
        prev_pessoa = "await_moedas" if tipo_operacao == "compra" else "await_fechamento_tipo"
        previous_map: Dict[str, str] = {
            "await_origem": "await_menu_tipo_operacao",
            "await_teor": "await_origem",
            "await_peso": "await_teor",
            "await_preco_moeda": "await_peso",
            "await_preco_usd": "await_peso",
            "await_preco_cambio": "await_preco_usd",
            "await_cambio_base_para_total": "await_moedas",
            "await_moedas": "await_preco_usd",
            "await_valor_moeda": "await_moedas",
            "await_cambio_moeda_pre_valor": "await_moedas",
            "await_cambio_moeda": "await_valor_moeda",
            "await_fechamento_gramas": "await_moedas",
            "await_fechamento_tipo": "await_fechamento_gramas",
            "await_pessoa": prev_pessoa,
            "await_forma_pagamento": "await_pessoa",
            "await_observacoes": "await_forma_pagamento",
            "await_confirmacao": "await_observacoes",
        }
        target_state = previous_map.get(estado)
    else:
        target_state = None
        for key, mapped_state in aliases.items():
            if key in text:
                target_state = mapped_state
                break

    if not target_state:
        return {
            "mensagem": (
                "Para corrigir sem cancelar, envie: 'voltar', 'voltar peso', 'voltar preço' ou 'voltar teor'."
            ),
            "dados": {"etapa": estado},
        }

    novo_contexto = _guided_clear_from_step(contexto, target_state)
    _save_session(db, remetente, target_state, novo_contexto)
    prompt = _guided_prompt_for_state(target_state, novo_contexto)
    return {
        "mensagem": f"Corrigindo esta etapa.\n{prompt}",
        "dados": {"etapa": target_state, "acao": "voltar_editar"},
    }


def _extract_caixa_currency(message: str) -> Optional[str]:
    text = _normalize_text(message)
    aliases = {
        "usd": "USD",
        "dolar": "USD",
        "dolar americano": "USD",
        "eur": "EUR",
        "euro": "EUR",
        "srd": "SRD",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
        "xau": "XAU",
        "ouro": "XAU",
    }
    for token in re.split(r"[^a-zA-Z]+", text):
        if token in aliases:
            return aliases[token]
    return None


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


def _is_greeting(message: str) -> bool:
    text = _normalize_text(message)
    # Remove punctuation and collapse spaces for robust matching.
    compact = re.sub(r"[^a-z0-9\s]", " ", text)
    compact = re.sub(r"\s+", " ", compact).strip()

    # Accept common variants like: "oii", "olaaa", "ola!", "bom diaaa".
    if re.match(r"^o+i+$", compact):
        return True
    if re.match(r"^o+l+a+$", compact):
        return True
    if compact.startswith("bom dia") or compact.startswith("boa tarde") or compact.startswith("boa noite"):
        return True
    if compact in {"hello", "hi", "hey"}:
        return True
    return False


def _looks_like_new_operation_start(message: str) -> bool:
    text = _normalize_text(message)
    operation_tokens = [
        "comprei",
        "comprar",
        "compra",
        "vendi",
        "vender",
        "venda",
        "cambio",
        "cambio",
        "troca",
    ]
    has_operation_word = any(token in text for token in operation_tokens)
    has_asset_or_amount = ("ouro" in text) or bool(re.search(r"\d", text))
    return has_operation_word and has_asset_or_amount


def _sanitize_nome(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:80]


def _parse_operation_id(raw: str) -> Optional[int]:
    text = raw.strip().lower()
    match_op = re.search(r"op-\d{8}-(\d+)", text)
    if match_op:
        return int(match_op.group(1))

    match_num = re.search(r"\b(\d{1,12})\b", text)
    if match_num:
        return int(match_num.group(1))
    return None


def _normalize_edit_field(raw: str) -> Optional[str]:
    field = _normalize_text(raw)
    aliases = {
        "preco": "cotacao_usada",
        "preço": "cotacao_usada",
        "cotacao": "cotacao_usada",
        "cotacao_usada": "cotacao_usada",
        "quantidade": "quantidade",
        "qtd": "quantidade",
        "moeda": "moeda_liquidacao",
        "moeda_liquidacao": "moeda_liquidacao",
        "valor_moeda": "valor_moeda",
        "cambio": "cambio_para_usd",
        "câmbio": "cambio_para_usd",
        "cambio_para_usd": "cambio_para_usd",
    }
    return aliases.get(field)


def _try_handle_whatsapp_commands(
    db: DatabaseClient,
    usuario: Dict[str, Any],
    remetente: str,
    mensagem: str,
) -> Optional[Dict[str, Any]]:
    text = mensagem.strip()
    text_norm = _normalize_text(text)

    # extrato: intercept before AI so it starts the dedicated extract flow.
    if re.match(r"^extrato\b", text_norm):
        if any(w in text_norm for w in {"hoje", "dia", "agora"}):
            day = _build_day_range(None)
            _clear_session(db, remetente)
            return _build_extrato_response(db, day["start"], day["end"], f"Hoje ({day['date']})")
        if any(w in text_norm for w in {"semana", "week"}):
            week = _build_week_range()
            _clear_session(db, remetente)
            return _build_extrato_response(db, week["start"], week["end"], week["label"])
        _save_session(db, remetente, "await_extrato_periodo", {})
        return {
            "mensagem": (
                "EXTRATO - selecione o periodo:\n"
                "1) Hoje\n"
                "2) Esta semana\n"
                "3) Informar datas"
            ),
            "dados": {"etapa": "await_extrato_periodo"},
        }

    # editar 123 preco 110
    edit_match = re.match(r"^\s*(editar|edit)\s+(.+?)\s+([\w_çÇãÃâÂáÁéÉíÍóÓúÚ]+)\s+(.+?)\s*$", text, re.IGNORECASE)
    if edit_match:
        op_token = edit_match.group(2)
        field_token = edit_match.group(3)
        value_token = edit_match.group(4)

        op_id = _parse_operation_id(op_token)
        if op_id is None:
            return {"mensagem": "ID inválido. Exemplo: editar 123 preco 110", "dados": {"acao": "editar_operacao"}}

        transacao_resp = (
            db.client.table("transacoes")
            .select("id,operador_id,quantidade,cotacao_usada,valor_total,moeda_liquidacao,valor_moeda,cambio_para_usd,status")
            .eq("id", op_id)
            .limit(1)
            .execute()
        )
        rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
        if not rows:
            return {"mensagem": f"Operação {op_id} não encontrada.", "dados": {"acao": "editar_operacao"}}

        row = rows[0]
        is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
        if not is_admin and str(row.get("operador_id", "")) != remetente:
            return {
                "mensagem": "Você não tem permissão para editar esta operação.",
                "dados": {"acao": "editar_operacao", "permitido": False},
            }

        field = _normalize_edit_field(field_token)
        if field is None:
            return {
                "mensagem": "Campo inválido. Use: preco, quantidade, moeda, valor_moeda ou cambio.",
                "dados": {"acao": "editar_operacao"},
            }

        update_payload: Dict[str, Any] = {}

        quantidade = Decimal(str(row.get("quantidade", "0")))
        cotacao = Decimal(str(row.get("cotacao_usada", "0")))
        moeda = str(row.get("moeda_liquidacao") or "USD").upper()
        valor_moeda = Decimal(str(row.get("valor_moeda") or row.get("valor_total") or "0"))
        cambio = Decimal(str(row.get("cambio_para_usd") or "1"))

        if field in {"quantidade", "cotacao_usada", "valor_moeda", "cambio_para_usd"}:
            novo = _parse_decimal_from_text(value_token, field)
            if field in {"quantidade", "cotacao_usada", "cambio_para_usd"} and novo <= 0:
                return {"mensagem": f"Valor inválido para {field}.", "dados": {"acao": "editar_operacao"}}
            if field == "valor_moeda" and novo < 0:
                return {"mensagem": "O valor da moeda não pode ser negativo.", "dados": {"acao": "editar_operacao"}}

            if field == "quantidade":
                quantidade = novo
                update_payload["quantidade"] = str(novo)
            elif field == "cotacao_usada":
                cotacao = novo
                update_payload["cotacao_usada"] = str(novo)
            elif field == "valor_moeda":
                valor_moeda = novo
                update_payload["valor_moeda"] = str(novo)
            elif field == "cambio_para_usd":
                cambio = novo
                update_payload["cambio_para_usd"] = str(novo)

        elif field == "moeda_liquidacao":
            nova_moeda = _normalize_text(value_token).upper()
            if nova_moeda not in _MOEDAS_SUPORTADAS:
                return {
                    "mensagem": "Moeda inválida. Use: USD, EUR, SRD ou BRL.",
                    "dados": {"acao": "editar_operacao"},
                }
            moeda = nova_moeda
            update_payload["moeda_liquidacao"] = moeda

        total_usd = money(quantidade * cotacao)
        update_payload["valor_total"] = str(total_usd)

        if moeda == "USD":
            update_payload["moeda_liquidacao"] = "USD"
            update_payload["cambio_para_usd"] = "1"
            update_payload["valor_moeda"] = str(total_usd)
        else:
            if field != "valor_moeda":
                valor_moeda = money(total_usd * cambio)
            update_payload["valor_moeda"] = str(valor_moeda)
            update_payload["cambio_para_usd"] = str(cambio)

        db.client.table("transacoes").update(update_payload).eq("id", op_id).execute()
        return {
            "mensagem": f"✅ Operação {op_id} atualizada com sucesso.",
            "dados": {"acao": "editar_operacao", "id": op_id, "campos": list(update_payload.keys())},
        }

    # cancelar 123
    cancel_match = re.match(r"^\s*(cancelar|cancela|excluir|delete)\s+(.+?)\s*$", text, re.IGNORECASE)
    if cancel_match:
        op_id = _parse_operation_id(cancel_match.group(2))
        if op_id is None:
            return {"mensagem": "ID inválido. Exemplo: cancelar 123", "dados": {"acao": "cancelar_operacao"}}

        transacao_resp = (
            db.client.table("transacoes")
            .select("id,operador_id,status")
            .eq("id", op_id)
            .limit(1)
            .execute()
        )
        rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
        if not rows:
            return {"mensagem": f"Operação {op_id} não encontrada.", "dados": {"acao": "cancelar_operacao"}}

        row = rows[0]
        is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
        if not is_admin and str(row.get("operador_id", "")) != remetente:
            return {
                "mensagem": "Você não tem permissão para cancelar esta operação.",
                "dados": {"acao": "cancelar_operacao", "permitido": False},
            }

        db.client.table("transacoes").update({"status": "cancelada"}).eq("id", op_id).execute()
        return {
            "mensagem": f"✅ Operação {op_id} cancelada com sucesso.",
            "dados": {"acao": "cancelar_operacao", "id": op_id, "status": "cancelada"},
        }

    return None


def _needs_name_onboarding(usuario: Dict[str, Any]) -> bool:
    nome = str(usuario.get("nome") or "").strip().lower()
    if not nome:
        return True
    placeholders = {"operador", "usuario", "usuário", "sem nome", "unknown", "n/a"}
    return nome in placeholders


def _build_whatsapp_checklist_menu() -> str:
    return (
        "MENU\n"
        "──────────────────\n"
        "1) Registrar compra ou venda\n"
        "   Ex: Comprei 2g de ouro a 105\n\n"
        "2) Consultar saldo\n"
        "   Ex: caixa | caixa eur | caixa srd | caixa xau\n\n"
        "3) Extrato detalhado\n"
        "   Ex: extrato | extrato hoje | extrato semana\n\n"
        "4) Editar operação\n"
        "   Ex: editar 123 preco 110 | editar 123 quantidade 2.5\n\n"
        "5) Cancelar operação\n"
        "   Ex: cancelar 123\n"
        "──────────────────\n"
        "Responda com 1 a 5."
    )


def _build_caixa_response(db: DatabaseClient, requested_currency: Optional[str] = None) -> Dict[str, Any]:
    day = _build_day_range(None)
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    saldo = db.get_saldo_caixa()

    gold_gramas = saldo.get("gold_gramas", "0")
    moedas = saldo.get("moedas", {})
    ops_hoje = int(summary.get("total_operacoes", 0) or 0)

    moeda_simbolo = {"USD": "$", "EUR": "EUR ", "SRD": "SRD ", "BRL": "R$"}
    moeda_ordem = ["USD", "EUR", "SRD", "BRL"]

    linhas_moeda: List[str] = []
    for m in moeda_ordem:
        val_str = moedas.get(m, "0")
        val = Decimal(str(val_str))
        simbolo = moeda_simbolo.get(m, m)
        situacao = "positivo" if val >= 0 else "negativo"
        linhas_moeda.append(f"- {m}: {simbolo}{val:,.2f} ({situacao})")

    for m, val_str in moedas.items():
        if m not in moeda_ordem:
            val = Decimal(str(val_str))
            situacao = "positivo" if val >= 0 else "negativo"
            linhas_moeda.append(f"- {m}: {val:,.2f} ({situacao})")

    moedas_txt = "\n".join(linhas_moeda) if linhas_moeda else "Sem movimentações"
    ouro_val = Decimal(str(gold_gramas))
    ouro_situacao = "positivo" if ouro_val >= 0 else "negativo"

    if requested_currency:
        moeda = requested_currency.upper()
        if moeda == "XAU":
            ouro_gramas = Decimal(str(gold_gramas))
            ativo_ouro = db.get_ativo_by_nome("Ouro") or db.get_ativo_by_nome("Ouro 24k")
            taxa_ouro: Optional[Decimal] = None
            if ativo_ouro:
                taxa = db.get_taxa_atual(int(ativo_ouro["id"]))
                if taxa:
                    taxa_ouro = Decimal(str(taxa.get("preco_compra", "0")))

            if taxa_ouro and taxa_ouro > 0:
                saldo_usd = money(ouro_gramas * taxa_ouro)
                cambio_txt = f"Cotação ouro: {money(taxa_ouro)} USD/g"
            else:
                saldo_usd = Decimal("0")
                cambio_txt = "Sem cotação atual de ouro"

            resposta = (
                f"SALDO ORGANIZADO - XAU\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "--------------------\n"
                f"1) Ouro em estoque: {ouro_gramas:,.3f} g\n"
                f"2) Referência em USD: {saldo_usd:,.2f}\n"
                f"3) {cambio_txt}\n"
                "--------------------\n"
                "Leitura rápida: quanto maior o valor, maior o estoque de ouro."
            )
        else:
            saldo_moeda = Decimal(str(moedas.get(moeda, "0")))
            cambio = db.get_last_cambio_para_usd(moeda)
            if moeda == "USD":
                saldo_usd = saldo_moeda
                cambio_txt = "1 USD = 1 USD"
            elif cambio and cambio > 0:
                saldo_usd = money(saldo_moeda / cambio)
                cambio_txt = f"1 USD = {money(cambio)} {moeda}"
            else:
                saldo_usd = Decimal("0")
                cambio_txt = f"Sem câmbio recente"

            resposta = (
                f"SALDO ORGANIZADO - {moeda}\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "--------------------\n"
                f"1) Saldo em {moeda}: {saldo_moeda:,.2f}\n"
                f"2) Equivalente em USD: {saldo_usd:,.2f}\n"
                f"3) Câmbio usado: {cambio_txt}\n"
                "--------------------\n"
                "Leitura rápida: positivo = entrou mais, negativo = saiu mais."
            )
    else:
        resposta = (
            "SALDO ORGANIZADO\n"
            f"Data: {day['date']}\n"
            f"Operações hoje: {ops_hoje}\n"
            "====================\n"
            f"1) Ouro em estoque: {ouro_val:,.3f} g ({ouro_situacao})\n"
            "2) Saldos por moeda:\n"
            f"{moedas_txt}\n"
            "====================\n"
            "Como ler:\n"
            "- positivo: entrou mais do que saiu\n"
            "- negativo: saiu mais do que entrou"
        )
    return {
        "mensagem": resposta,
        "dados": {
            "intencao": "consultar_relatorio",
            "date": day["date"],
            "gold_gramas": gold_gramas,
            "moedas": moedas,
            "ops_hoje": ops_hoje,
            "summary": summary,
            "requested_currency": requested_currency,
        },
    }


def _build_extrato_response(
    db: DatabaseClient,
    start_iso: str,
    end_iso: str,
    label_periodo: str,
) -> Dict[str, Any]:
    """Build a professional bank-style transaction statement for the given period."""
    transactions = db.get_extrato_transactions(start_iso, end_iso)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    moeda_simbolo: Dict[str, str] = {"USD": "$", "EUR": "EUR ", "SRD": "SRD ", "BRL": "R$"}

    linhas: List[str] = [
        "===== EXTRATO =====",
        f"Periodo: {label_periodo}",
        f"Total: {len(transactions)} operac{'oes' if len(transactions) != 1 else 'ao'}",
        "====================",
    ]

    total_compra_g = Decimal("0")
    total_venda_g = Decimal("0")
    total_compra_usd = Decimal("0")
    total_venda_usd = Decimal("0")

    for i, t in enumerate(transactions, 1):
        tipo = str(t.get("tipo_operacao") or "").upper()
        data_hora_raw = str(t.get("criado_em") or "")
        try:
            dt = datetime.fromisoformat(data_hora_raw.replace("Z", "+00:00"))
            dt_local = dt + timedelta(hours=tz_offset_hours)
            data_fmt = dt_local.strftime("%d/%m %H:%M")
        except Exception:
            data_fmt = data_hora_raw[:16]

        peso = Decimal(str(t.get("peso") or "0"))
        preco_usd = Decimal(str(t.get("preco_usd") or "0"))
        total_usd_val = Decimal(str(t.get("total_usd") or "0"))
        total_pago = Decimal(str(t.get("total_pago_usd") or total_usd_val))
        diferenca = Decimal(str(t.get("diferenca_usd") or "0"))
        pessoa = str(t.get("pessoa") or "").strip()
        observacoes = str(t.get("observacoes") or "").strip()
        status = str(t.get("status") or "registrada")
        source = str(t.get("source") or "transacoes")
        tid = t.get("id")
        id_prefixado = f"GT-{tid}" if source == "gold_transactions" else f"T-{tid}"

        linhas.append("--------------------")
        status_tag = f" [{status.upper()}]" if status not in ("registrada", "") else ""
        linhas.append(f"#{i} | {data_fmt} | {tipo}{status_tag}")
        if tid:
            linhas.append(f"ID: {id_prefixado}")
        if peso > 0:
            linhas.append(f"Peso: {peso:,.3f} g")
        if preco_usd > 0:
            linhas.append(f"Preco: ${preco_usd:,.2f}/g")
        linhas.append(f"Total ref: ${total_usd_val:,.2f}")

        pagamentos: List[Dict[str, Any]] = t.get("pagamentos") or []
        if pagamentos:
            for p in pagamentos:
                moeda = str(p.get("moeda") or "USD").upper()
                valor_m = Decimal(str(p.get("valor_moeda") or "0"))
                cambio = Decimal(str(p.get("cambio_para_usd") or "1"))
                simbolo = moeda_simbolo.get(moeda, f"{moeda} ")
                if moeda == "USD":
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f}")
                else:
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f} (cambio: {cambio:,.4f})")
        else:
            moeda = str(t.get("moeda") or "USD").upper()
            valor_m_raw = t.get("valor_moeda")
            if valor_m_raw:
                valor_m = Decimal(str(valor_m_raw))
                cambio_raw = t.get("cambio_para_usd")
                cambio = Decimal(str(cambio_raw)) if cambio_raw else Decimal("1")
                simbolo = moeda_simbolo.get(moeda, f"{moeda} ")
                if moeda == "USD":
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f}")
                else:
                    linhas.append(f"Pago: {simbolo}{valor_m:,.2f} (cambio: {cambio:,.4f})")
            else:
                linhas.append(f"Pago: ${total_pago:,.2f}")

        if diferenca != 0:
            sinal = "+" if diferenca > 0 else ""
            linhas.append(f"Diferenca: {sinal}${diferenca:,.2f}")
        if pessoa:
            linhas.append(f"Pessoa: {pessoa}")
        if observacoes:
            linhas.append(f"Obs: {observacoes[:60]}")

        if tipo == "COMPRA":
            total_compra_g += peso
            total_compra_usd += total_usd_val
        elif tipo in ("VENDA", "CAMBIO"):
            total_venda_g += peso
            total_venda_usd += total_usd_val

    linhas.append("====================")
    linhas.append("RESUMO:")
    if not transactions:
        linhas.append("Nenhuma operação encontrada.")
    else:
        if total_compra_g > 0:
            n_c = sum(1 for x in transactions if str(x.get("tipo_operacao") or "").upper() == "COMPRA")
            linhas.append(f"Compras: {n_c} op | {total_compra_g:,.3f} g | ${total_compra_usd:,.2f}")
        if total_venda_g > 0:
            n_v = sum(1 for x in transactions if str(x.get("tipo_operacao") or "").upper() in ("VENDA", "CAMBIO"))
            linhas.append(f"Vendas:  {n_v} op | {total_venda_g:,.3f} g | ${total_venda_usd:,.2f}")
        saldo_g = total_compra_g - total_venda_g
        sinal_g = "+" if saldo_g >= 0 else ""
        linhas.append(f"Saldo ouro: {sinal_g}{saldo_g:,.3f} g")
    linhas.append("====================")

    return {
        "mensagem": "\n".join(linhas),
        "dados": {
            "intencao": "extrato",
            "periodo": label_periodo,
            "total_operacoes": len(transactions),
        },
    }


def _handle_menu_option(remetente: str, mensagem: str, db: DatabaseClient) -> Optional[Dict[str, Any]]:
    option = _normalize_text(mensagem)
    if option not in {"1", "2", "3", "4", "5"}:
        return {
            "mensagem": (
                "Opção inválida. Escolha um número de 1 a 5.\n\n"
                f"{_build_whatsapp_checklist_menu()}"
            ),
            "dados": {"etapa": "await_menu_option"},
        }

    if option == "1":
        _save_session(
            db,
            remetente,
            "await_menu_tipo_operacao",
            {"source": "menu", "source_message_id": None},
        )
        return {
            "mensagem": (
                "Registrar operação.\n"
                "Informe o tipo: compra ou venda."
            ),
            "dados": {"acao": "registrar_operacao"},
        }

    if option == "2":
        _clear_session(db, remetente)
        return _build_caixa_response(db)

    if option == "3":
        _clear_session(db, remetente)
        _save_session(db, remetente, "await_extrato_periodo", {})
        return {
            "mensagem": (
                "EXTRATO - selecione o periodo:\n"
                "1) Hoje\n"
                "2) Esta semana\n"
                "3) Informar datas"
            ),
            "dados": {"etapa": "await_extrato_periodo"},
        }

    if option == "4":
        _clear_session(db, remetente)
        return {
            "mensagem": (
                "Editar operação.\n"
                "Formato: editar ID campo valor\n\n"
                "Campos: preço | quantidade | moeda | valor_moeda | câmbio\n"
                "Exemplos:\n"
                "- editar 123 preco 110\n"
                "- editar 123 quantidade 2.5"
            ),
            "dados": {"acao": "editar_operacao"},
        }

    # option == "5"
    _clear_session(db, remetente)
    return {
        "mensagem": (
            "Cancelar operação.\n"
        ),
        "dados": {"acao": "cancelar_operacao"},
    }


def _save_session(db: DatabaseClient, remetente: str, estado: str, contexto: Dict[str, Any]) -> None:
    atualizado_em = datetime.now(timezone.utc).isoformat()
    _SESSION_CACHE[remetente] = {"estado": estado, "contexto": contexto, "atualizado_em": atualizado_em}
    db.save_conversation_session(remetente=remetente, estado=estado, contexto=contexto)


def _get_session(db: DatabaseClient, remetente: str) -> Optional[Dict[str, Any]]:
    cached = _SESSION_CACHE.get(remetente)
    if cached:
        return cached
    db_session = db.get_conversation_session(remetente)
    if db_session and isinstance(db_session.get("contexto"), dict):
        session = {
            "estado": db_session.get("estado", ""),
            "contexto": db_session["contexto"],
            "atualizado_em": db_session.get("atualizado_em"),
        }
        _SESSION_CACHE[remetente] = session
        return session
    return None


def _guided_session_idle_minutes(session: Dict[str, Any]) -> Optional[int]:
    updated_raw = session.get("atualizado_em")
    if not updated_raw:
        return None
    try:
        updated_dt = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - updated_dt.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds() // 60))


def _is_guided_session_stale(session: Dict[str, Any]) -> bool:
    idle = _guided_session_idle_minutes(session)
    if idle is None:
        return False
    return idle >= _GUIDED_SESSION_IDLE_MINUTES


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
    if any(token in text for token in {"compra", "comprei", "comprar", "buy", "bought"}):
        tipo = "compra"
    elif any(token in text for token in {"venda", "vendi", "vender", "sell", "sold"}):
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
        "mensagem": f"Iniciando registro de {tipo}.\nLocal da operação: balcão ou fora?",
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

    tipo_operacao = str(contexto.get("tipo_operacao") or "")
    pessoa_label = "Vendedor" if tipo_operacao == "compra" else "Comprador"

    if tipo_operacao == "compra":
        return (
            "RESUMO FINAL\n"
            f"1) Tipo: {contexto.get('tipo_operacao')}\n"
            f"2) Origem: {contexto.get('origem')}\n"
            f"3) Teor: {contexto.get('teor')}%\n"
            f"4) Peso: {contexto.get('peso')}g\n"
            f"5) Preço USD/g: {contexto.get('preco_usd')}\n"
            f"6) Total USD: {contexto.get('total_usd')}\n"
            f"7) {pessoa_label}: {contexto.get('pessoa')}\n"
            f"8) Forma: {contexto.get('forma_pagamento')}\n"
            f"9) Pagamentos:\n{linhas_pagamento_texto}\n"
            f"10) Total pago USD: {money(total_pago)}\n"
            f"11) Diferença USD: {diferenca}\n"
            "Se estiver correto, responda: sim. Para cancelar, responda: não."
        )

    return (
        "RESUMO FINAL\n"
        f"1) Tipo: {contexto.get('tipo_operacao')}\n"
        f"2) Origem: {contexto.get('origem')}\n"
        f"3) Teor: {contexto.get('teor')}%\n"
        f"4) Peso: {contexto.get('peso')}g\n"
        f"5) Preço USD/g: {contexto.get('preco_usd')}\n"
        f"6) Total USD: {contexto.get('total_usd')}\n"
        f"7) Fechamento: {contexto.get('fechamento_gramas')}g ({contexto.get('fechamento_tipo')})\n"
        f"8) {pessoa_label}: {contexto.get('pessoa')}\n"
        f"9) Forma: {contexto.get('forma_pagamento')}\n"
        f"10) Pagamentos:\n{linhas_pagamento_texto}\n"
        f"11) Total pago USD: {money(total_pago)}\n"
        f"12) Diferença USD: {diferenca}\n"
        "Se estiver correto, responda: sim. Para cancelar, responda: não."
    )


def _build_day_range(date_str: Optional[str]) -> Dict[str, str]:
    # Use TZ_OFFSET_HOURS to convert UTC "now" to local date (default: Brazil UTC-3)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    if date_str:
        try:
            base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Data invalida. Use: AAAA-MM-DD") from exc
    else:
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now + timedelta(hours=tz_offset_hours)
        base_date = local_now.date()

    start_dt = datetime(base_date.year, base_date.month, base_date.day, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "date": str(base_date)}


def _build_week_range() -> Dict[str, str]:
    """ISO range from Monday of the current week to end of today (inclusive)."""
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))
    utc_now = datetime.now(timezone.utc)
    local_now = utc_now + timedelta(hours=tz_offset_hours)
    today = local_now.date()
    monday = today - timedelta(days=today.weekday())
    start_dt = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    end_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=1)
    return {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "label": f"{monday.isoformat()} a {today.isoformat()}",
    }


def _parse_date_user_input(text: str) -> Optional[str]:
    """Accept DD/MM/AAAA, DD/MM/AA, DD-MM-AAAA, or AAAA-MM-DD → return YYYY-MM-DD."""
    import re as _re
    s = text.strip()
    m = _re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", s)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year_raw = m.group(3)
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            from datetime import date as _date
            year = _date.today().year
        try:
            from datetime import date as _date
            _date(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            return None
    m2 = _re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m2:
        return s
    return None


def _build_custom_range(start: str, end: str) -> Dict[str, str]:
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data/hora invalida. Use formato ISO.") from exc

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="A data final deve ser maior que a inicial.")

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


def _advance_after_payment_exchange(
    db: DatabaseClient,
    remetente: str,
    contexto: Dict[str, Any],
    pagamentos: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Advance the guided flow after a payment entry has been fully populated (amount + exchange rate)."""
    moedas = list(contexto.get("moedas", []))
    idx = int(contexto.get("moeda_index", 0)) + 1
    total_operacao = Decimal(str(contexto.get("total_usd", "0")))
    total_pago_parcial = sum((Decimal(str(p["valor_usd"])) for p in pagamentos), Decimal("0"))

    # Se ainda não temos total em USD (precificação em moeda não-USD sem câmbio-base),
    # avançamos sem calcular restante e pedimos o câmbio-base no final.
    if total_operacao <= 0:
        if idx < len(moedas):
            contexto["moeda_index"] = idx
            contexto["moeda_atual"] = moedas[idx]
            proxima_moeda = str(moedas[idx]).upper()
            if proxima_moeda != "USD":
                _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
                return {
                    "mensagem": (
                        "Pagamento registrado.\n"
                        "Ainda falta o câmbio da moeda-base para calcular o total em USD.\n"
                        f"Antes do valor em {proxima_moeda}, informe o câmbio: {_build_cambio_prompt(proxima_moeda)}"
                    ),
                    "dados": {"etapa": "await_cambio_moeda_pre_valor"},
                }

            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {
                "mensagem": (
                    "Pagamento registrado.\n"
                    "Ainda falta o câmbio da moeda-base para calcular o total em USD.\n"
                    f"Valor em {moedas[idx]}?"
                ),
                "dados": {"etapa": "await_valor_moeda"},
            }

        _save_session(db, remetente, "await_cambio_base_para_total", contexto)
        moeda_preco = str(contexto.get("preco_moeda", "EUR")).upper()
        return {
            "mensagem": (
                "Para fechar o total da operação em USD, informe o câmbio da moeda-base.\n"
                f"{_build_cambio_prompt(moeda_preco)}"
            ),
            "dados": {"etapa": "await_cambio_base_para_total"},
        }

    restante = money(total_operacao - total_pago_parcial)

    if idx < len(moedas):
        contexto["moeda_index"] = idx
        contexto["moeda_atual"] = moedas[idx]
        _save_session(db, remetente, "await_valor_moeda", contexto)
        return {
            "mensagem": (
                f"Pago até agora: {money(total_pago_parcial)} USD. Restante: {restante} USD.\n"
                f"Valor em {moedas[idx]}?"
            ),
            "dados": {"etapa": "await_valor_moeda"},
        }

    total_pago = sum((Decimal(str(p["valor_usd"])) for p in pagamentos), Decimal("0"))
    contexto["total_pago_usd"] = str(money(total_pago))
    tipo_operacao_ctx = str(contexto.get("tipo_operacao", "compra"))

    if tipo_operacao_ctx == "compra":
        peso_ctx = Decimal(str(contexto.get("peso", "0")))
        contexto["fechamento_gramas"] = str(money(peso_ctx))
        contexto["fechamento_tipo"] = "total"
        _save_session(db, remetente, "await_pessoa", contexto)
        return {
            "mensagem": (
                f"Total pago: {money(total_pago)} USD.\n"
                f"Diferença atual: {money(total_operacao - total_pago)} USD.\n"
                "Nome do vendedor (de quem você comprou)?"
            ),
            "dados": {"etapa": "await_pessoa"},
        }

    _save_session(db, remetente, "await_fechamento_gramas", contexto)
    return {
        "mensagem": (
            f"Total pago: {money(total_pago)} USD.\n"
            f"Diferença atual: {money(total_operacao - total_pago)} USD.\n"
            "Informe as gramas fechadas."
        ),
        "dados": {"etapa": "await_fechamento_gramas"},
    }


def _process_guided_flow(remetente: str, mensagem: str, db: DatabaseClient, session: Dict[str, Any]) -> Dict[str, Any]:
    estado = str(session.get("estado", ""))
    contexto = dict(session.get("contexto", {}))
    text = _normalize_text(mensagem)

    cancelable_states = _GUIDED_FLOW_STATES - {"await_menu_option", "await_menu_tipo_operacao", "await_nome_usuario"}

    if estado in cancelable_states and text in {"cancelar", "cancela", "cancel", "parar", "sair"}:
        _clear_session(db, remetente)
        return {
            "mensagem": "Operação cancelada por você. Quando quiser recomeçar, envie: compra ou venda.",
            "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
        }

    if estado == "await_menu_option":
        menu_result = _handle_menu_option(remetente, mensagem, db)
        if menu_result is not None:
            return menu_result

    back_result = _guided_try_back_command(remetente, mensagem, estado, contexto, db)
    if back_result is not None and estado in _GUIDED_FLOW_STATES:
        return back_result

    if estado == "await_resume_confirmacao":
        if text in {"continuar", "retomar", "sim", "s"}:
            estado_anterior = str(contexto.get("estado_anterior", ""))
            contexto_anterior = dict(contexto.get("contexto_anterior", {}))
            if not estado_anterior or estado_anterior not in _GUIDED_FLOW_STATES:
                _clear_session(db, remetente)
                return {
                    "mensagem": "Sessão anterior expirada. Envie 'compra' ou 'venda' para iniciar novamente.",
                    "dados": {"acao": "sessao_expirada"},
                }

            _save_session(db, remetente, estado_anterior, contexto_anterior)
            if estado_anterior == "await_confirmacao":
                resumo = _format_resumo(contexto_anterior)
                return {
                    "mensagem": f"Retomando de onde parou.\n{resumo}",
                    "dados": {"etapa": estado_anterior, "acao": "retomar_fluxo"},
                }

            prompt = _guided_prompt_for_state(estado_anterior, contexto_anterior)
            return {
                "mensagem": f"Retomando de onde parou.\n{prompt}",
                "dados": {"etapa": estado_anterior, "acao": "retomar_fluxo"},
            }

        if text in {"cancelar", "cancela", "cancel", "nao", "não", "n", "parar", "sair"}:
            _clear_session(db, remetente)
            return {
                "mensagem": "Operação cancelada por você. Quando quiser recomeçar, envie: compra ou venda.",
                "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
            }

        return {
            "mensagem": "Deseja continuar a transação de onde parou? Responda: continuar ou cancelar.",
            "dados": {"etapa": "await_resume_confirmacao"},
        }

    if estado == "await_nome_usuario":
        nome = _sanitize_nome(mensagem)
        if len(nome) < 2:
            return {
                "mensagem": "Nome inválido. Digite um nome com pelo menos 2 letras.",
                "dados": {"etapa": "await_nome_usuario"},
            }

        db.update_usuario_nome(remetente, nome)
        _clear_session(db, remetente)
        return {
            "mensagem": (
                f"Bem-vindo, {nome}. Seu cadastro está completo.\n"
                "Digite 'menu' para acessar as opções."
            ),
            "dados": {"acao": "cadastro_nome", "nome": nome},
        }

    if estado == "await_menu_tipo_operacao":
        if text not in {"compra", "venda"}:
            return {
                "mensagem": "Tipo inválido. Digite somente: compra ou venda.",
                "dados": {"etapa": "await_menu_tipo_operacao"},
            }

        contexto.update(
            {
                "tipo_operacao": text,
                "pagamentos": [],
                "moedas": [],
                "moeda_index": 0,
                "moeda_atual": None,
            }
        )
        _save_session(db, remetente, "await_origem", contexto)
        return {
            "mensagem": f"Operação: {text}.\nLocal: balcão ou fora?",
            "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
        }

    if estado == "await_origem":
        if text not in {"balcao", "balcão", "fora"}:
            return {"mensagem": "Origem inválida. Digite: balcão ou fora.", "dados": {"etapa": estado}}
        contexto["origem"] = "balcao" if "balcao" in text or "balcão" in text else "fora"
        _save_session(db, remetente, "await_teor", contexto)
        return {"mensagem": "Qual o teor do ouro em %? (0 a 99,99)", "dados": {"etapa": "await_teor"}}

    if estado == "await_teor":
        teor = _parse_decimal_from_text(mensagem, "teor")
        if teor < 0 or teor > Decimal("99.99"):
            return {"mensagem": "O teor deve estar entre 0 e 99,99.", "dados": {"etapa": estado}}
        contexto["teor"] = str(money(teor))
        _save_session(db, remetente, "await_peso", contexto)
        return {"mensagem": "Quantas gramas?", "dados": {"etapa": "await_peso"}}

    if estado == "await_peso":
        peso = _parse_decimal_from_text(mensagem, "peso")
        if peso <= 0:
            return {"mensagem": "O peso deve ser maior que zero.", "dados": {"etapa": estado}}
        contexto["peso"] = str(peso)
        _save_session(db, remetente, "await_preco_moeda", contexto)
        return {
            "mensagem": (
                "Moeda base para precificação:\n"
                "USD, EUR, SRD ou BRL"
            ),
            "dados": {"etapa": "await_preco_moeda"},
        }

    if estado == "await_preco_moeda":
        moeda_preco = _normalize_text(mensagem).upper()
        if moeda_preco not in _MOEDAS_SUPORTADAS:
            return {
                "mensagem": "Moeda inválida. Escolha: USD, EUR, SRD ou BRL.",
                "dados": {"etapa": estado},
            }
        contexto["preco_moeda"] = moeda_preco
        _save_session(db, remetente, "await_preco_usd", contexto)
        return {
            "mensagem": f"Informe o preço por grama em {moeda_preco}.",
            "dados": {"etapa": "await_preco_usd"},
        }

    if estado == "await_preco_usd":
        preco = _parse_decimal_from_text(mensagem, "preco_usd")
        if preco <= 0:
            return {"mensagem": "Preço deve ser maior que zero.", "dados": {"etapa": estado}}

        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda != "USD":
            contexto["preco_moeda_valor"] = str(money(preco))
            peso = Decimal(str(contexto.get("peso")))
            total_moeda = money(peso * preco)
            contexto["total_moeda"] = str(total_moeda)
            _save_session(db, remetente, "await_moedas", contexto)
            return {
                "mensagem": (
                    f"Preco recebido: {money(preco)} {preco_moeda}/g.\n"
                    f"Total da operação: {total_moeda} {preco_moeda}.\n"
                    "Informe as moedas de pagamento: USD, EUR, SRD, BRL\n"
                    "(o câmbio será pedido na etapa de pagamento)"
                ),
                "dados": {"etapa": "await_moedas"},
            }

        peso = Decimal(str(contexto.get("peso")))
        total = money(peso * preco)
        contexto["preco_usd"] = str(money(preco))
        contexto["total_usd"] = str(total)
        _save_session(db, remetente, "await_moedas", contexto)
        return {
            "mensagem": (
                f"{peso}g x {money(preco)} USD/g = {total} USD.\n"
                "Informe as moedas de pagamento: USD, EUR, SRD, BRL"
            ),
            "dados": {"etapa": "await_moedas"},
        }

    if estado == "await_preco_cambio":
        cambio = _parse_decimal_from_text(mensagem, "cambio_preco")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        cambio_normalizado = _normalize_cambio_para_usd(preco_moeda, cambio)
        preco_moeda_valor = Decimal(str(contexto.get("preco_moeda_valor", "0")))
        preco_usd = money(preco_moeda_valor / cambio_normalizado)
        peso = Decimal(str(contexto.get("peso")))
        total = money(peso * preco_usd)

        contexto["preco_usd"] = str(preco_usd)
        contexto["cambio_preco_moeda"] = str(cambio_normalizado)
        contexto["total_usd"] = str(total)
        _save_session(db, remetente, "await_moedas", contexto)
        return {
            "mensagem": (
                f"Conversão feita: {preco_usd} USD/g.\n"
                f"Total da operação: {total} USD.\n"
                "Informe as moedas de pagamento: USD, EUR, SRD, BRL"
            ),
            "dados": {"etapa": "await_moedas"},
        }

    if estado == "await_moedas":
        moedas = _extract_moedas(mensagem)
        if not moedas:
            return {"mensagem": "Não entendi as moedas. Exemplo: USD e SRD", "dados": {"etapa": estado}}
        contexto["moedas"] = moedas
        contexto["moeda_index"] = 0
        contexto["pagamentos"] = []
        contexto["moeda_atual"] = moedas[0]
        _save_session(db, remetente, "await_valor_moeda", contexto)
        total_operacao = Decimal(str(contexto.get("total_usd", "0")))
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        total_moeda = Decimal(str(contexto.get("total_moeda", "0")))

        if total_operacao > 0:
            total_txt = f"Total da operação: {money(total_operacao)} USD."
        elif preco_moeda != "USD" and total_moeda > 0:
            total_txt = f"Total da operação: {money(total_moeda)} {preco_moeda}."
        else:
            total_txt = "Total da operação definido."

        return {
            "mensagem": (
                f"{total_txt}\n"
                f"Quanto será pago em {moedas[0]}?"
            ),
            "dados": {"etapa": "await_valor_moeda"},
        }

    if estado == "await_cambio_moeda_pre_valor":
        cambio = _parse_decimal_from_text(mensagem, "cambio_pre_valor")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

        moeda_atual = str(contexto.get("moeda_atual", "USD")).upper()
        if moeda_atual == "USD":
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "Quanto será pago em USD?", "dados": {"etapa": "await_valor_moeda"}}

        cambio_normalizado = _normalize_cambio_para_usd(moeda_atual, cambio)
        contexto["cambio_moeda_atual_pre"] = str(cambio_normalizado)
        _save_session(db, remetente, "await_valor_moeda", contexto)
        return {
            "mensagem": f"Câmbio registrado para {moeda_atual}. Quanto será pago em {moeda_atual}?",
            "dados": {"etapa": "await_valor_moeda"},
        }

    if estado == "await_valor_moeda":
        moeda_atual = str(contexto.get("moeda_atual"))
        valor_moeda = _parse_decimal_from_text(mensagem, "valor_moeda")
        if valor_moeda < 0:
            return {"mensagem": "Valor da moeda não pode ser negativo.", "dados": {"etapa": estado}}
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

        if moeda_atual == "USD":
            contexto.pop("cambio_moeda_atual_pre", None)
            return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        cambio_pre = contexto.get("cambio_moeda_atual_pre")
        if cambio_pre:
            cambio_pre_dec = Decimal(str(cambio_pre))
            valor_usd_pre = money(valor_moeda / cambio_pre_dec)
            pagamentos[-1]["cambio_para_usd"] = str(cambio_pre_dec)
            pagamentos[-1]["valor_usd"] = str(valor_usd_pre)
            contexto["pagamentos"] = pagamentos
            contexto.pop("cambio_moeda_atual_pre", None)

            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if preco_moeda != "USD" and str(moeda_atual).upper() == preco_moeda:
                _try_set_total_usd_from_base_rate(contexto, cambio_pre_dec)

            return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

        # Câmbio de moeda não-USD sempre é pedido na etapa de pagamento.
        total_operacao = Decimal(str(contexto.get("total_usd", "0")))
        _save_session(db, remetente, "await_cambio_moeda", contexto)
        total_linha = f"Total da operação: {money(total_operacao)} USD.\n" if total_operacao > 0 else ""
        return {
            "mensagem": (
                f"{moeda_atual}: {money(valor_moeda)} registrado.\n"
                f"{total_linha}"
                f"Câmbio do {moeda_atual}: {_build_cambio_prompt(moeda_atual)}"
            ),
            "dados": {"etapa": "await_cambio_moeda"},
        }

    if estado == "await_cambio_moeda":
        cambio = _parse_decimal_from_text(mensagem, "cambio")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}
        pagamentos = list(contexto.get("pagamentos", []))
        if not pagamentos:
            _save_session(db, remetente, "await_moedas", contexto)
            return {"mensagem": "Pagamentos reiniciados. Informe as moedas novamente.", "dados": {"etapa": "await_moedas"}}

        ultimo = dict(pagamentos[-1])
        moeda_ult = str(ultimo.get("moeda", "USD")).upper()
        cambio_normalizado = _normalize_cambio_para_usd(moeda_ult, cambio)
        valor_moeda_ult = Decimal(str(ultimo["valor_moeda"]))
        valor_usd = money(valor_moeda_ult / cambio_normalizado)
        ultimo["cambio_para_usd"] = str(cambio_normalizado)
        ultimo["valor_usd"] = str(valor_usd)
        pagamentos[-1] = ultimo
        contexto["pagamentos"] = pagamentos

        # Se esta moeda for a base da precificação, usamos o câmbio para fechar total em USD automaticamente.
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda != "USD" and moeda_ult == preco_moeda:
            _try_set_total_usd_from_base_rate(contexto, cambio_normalizado)

        return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

    if estado == "await_cambio_base_para_total":
        cambio = _parse_decimal_from_text(mensagem, "cambio_base_total")
        if cambio <= 0:
            return {"mensagem": "Câmbio deve ser maior que zero.", "dados": {"etapa": estado}}

        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        cambio_normalizado = _normalize_cambio_para_usd(preco_moeda, cambio)
        if not _try_set_total_usd_from_base_rate(contexto, cambio_normalizado):
            _clear_session(db, remetente)
            return {
                "mensagem": "Não consegui retomar os dados da operação. Envie compra ou venda para reiniciar.",
                "dados": {"acao": "reiniciar"},
            }

        pagamentos = list(contexto.get("pagamentos", []))
        return _advance_after_payment_exchange(db, remetente, contexto, pagamentos)

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
        tipo_op_ft = str(contexto.get("tipo_operacao", "compra"))
        pergunta_pessoa = "Nome do vendedor (de quem você comprou)?" if tipo_op_ft == "compra" else "Nome do comprador?"
        return {"mensagem": pergunta_pessoa, "dados": {"etapa": "await_pessoa"}}

    if estado == "await_pessoa":
        if len(mensagem.strip()) < 2:
            return {"mensagem": "Informe um nome válido.", "dados": {"etapa": estado}}
        contexto["pessoa"] = mensagem.strip()
        _save_session(db, remetente, "await_forma_pagamento", contexto)
        return {"mensagem": "Como foi o pagamento? (dinheiro, transferência, cheque, misto)", "dados": {"etapa": "await_forma_pagamento"}}

    if estado == "await_forma_pagamento":
        forma = _normalize_text(mensagem)
        if forma not in {"dinheiro", "transferencia", "cheque", "misto"}:
            return {"mensagem": "Forma inválida. Use: dinheiro, transferência, cheque ou misto.", "dados": {"etapa": estado}}
        contexto["forma_pagamento"] = forma
        pagamentos = list(contexto.get("pagamentos", []))
        for pagamento in pagamentos:
            pagamento["forma_pagamento"] = forma
        contexto["pagamentos"] = pagamentos
        _save_session(db, remetente, "await_observacoes", contexto)
        return {"mensagem": "Quer adicionar observações? (ou digite 'nenhuma')", "dados": {"etapa": "await_observacoes"}}

    if estado == "await_observacoes":
        contexto["observacoes"] = "" if _normalize_text(mensagem) in {"nenhuma", "nao", "não"} else mensagem.strip()
        resumo = _format_resumo(contexto)
        _save_session(db, remetente, "await_confirmacao", contexto)
        return {"mensagem": resumo, "dados": {"etapa": "await_confirmacao", "preview": contexto}}

    if estado == "await_confirmacao":
        confirm = _extract_confirmacao(mensagem)
        if confirm is None:
            return {"mensagem": "Digite apenas: sim ou não.", "dados": {"etapa": estado}}

        if not confirm:
            _clear_session(db, remetente)
            return {"mensagem": "Operação cancelada com sucesso.", "dados": {"intencao": "fluxo_guiado_cancelado"}}

        ativo = db.get_ativo_by_nome("Ouro")
        if not ativo:
            ativo = db.get_ativo_by_nome("Ouro 24k")
        if not ativo:
            raise HTTPException(status_code=404, detail="Ativo não encontrado")

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

        gt_id = gold_transaction.get("id") if isinstance(gold_transaction, dict) else None
        id_linha = f"ID: GT-{gt_id}\n" if gt_id else ""

        caixa_resp = _build_caixa_response(db)
        caixa_msg = str(caixa_resp.get("mensagem", ""))

        response_payload: Dict[str, Any] = {
            "mensagem": (
                f"✅ Operação salva com sucesso.\n"
                f"{id_linha}"
                f"Total USD: {money(total)}\n"
                f"Pago USD: {money(total_pago)}\n"
                f"Diferença USD: {diferenca}{alerta}\n"
                "--------------------\n"
                f"{caixa_msg}"
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
            return {"mensagem": "Preço inválido. Exemplo: 65.50", "dados": {"etapa": estado}}

        quantidade = Decimal(str(contexto["quantidade"]))
        total_usd = money(quantidade * cotacao)
        contexto["cotacao_usd"] = str(cotacao)
        contexto["total_usd"] = str(total_usd)
        _save_session(db, remetente, "await_moeda_simples", contexto)
        return {
            "mensagem": "Em qual moeda foi pago?\nUSD / EUR / SRD / BRL",
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
                "mensagem": "Câmbio inválido. Exemplo: 38",
                "dados": {"etapa": estado},
            }
        contexto["cambio_para_usd"] = str(cambio)
        return _finish_transacao_simples(db, remetente, mensagem, contexto)

    # ── Extrato guided flow ──────────────────────────────────────────────────
    if estado == "await_extrato_periodo":
        escolha = _normalize_text(mensagem)
        if escolha in {"1", "hoje", "dia", "hoje (1)", "1)"}:
            day = _build_day_range(None)
            _clear_session(db, remetente)
            return _build_extrato_response(db, day["start"], day["end"], f"Hoje ({day['date']})")
        if escolha in {"2", "semana", "esta semana", "week", "2)"}:
            week = _build_week_range()
            _clear_session(db, remetente)
            return _build_extrato_response(db, week["start"], week["end"], week["label"])
        if escolha in {"3", "data", "datas", "informar", "informar datas", "outro", "3)"}:
            _save_session(db, remetente, "await_extrato_data_inicio", {})
            return {
                "mensagem": (
                    "Informe a data inicial:\n"
                    "Ex: 01/04/2026 ou 2026-04-01"
                ),
                "dados": {"etapa": "await_extrato_data_inicio"},
            }
        return {
            "mensagem": "Escolha inválida. Digite 1, 2 ou 3.",
            "dados": {"etapa": "await_extrato_periodo"},
        }

    if estado == "await_extrato_data_inicio":
        parsed = _parse_date_user_input(mensagem.strip())
        if not parsed:
            return {
                "mensagem": "Data inválida. Use o formato DD/MM/AAAA ou AAAA-MM-DD.",
                "dados": {"etapa": estado},
            }
        _save_session(db, remetente, "await_extrato_data_fim", {"data_inicio": parsed})
        return {
            "mensagem": (
                f"Data inicial: {parsed}\n"
                "Informe a data final:\n"
                "Ex: 04/04/2026 ou 2026-04-04"
            ),
            "dados": {"etapa": "await_extrato_data_fim"},
        }

    if estado == "await_extrato_data_fim":
        parsed = _parse_date_user_input(mensagem.strip())
        if not parsed:
            return {
                "mensagem": "Data inválida. Use o formato DD/MM/AAAA ou AAAA-MM-DD.",
                "dados": {"etapa": estado},
            }
        data_inicio = str(contexto.get("data_inicio", ""))
        if not data_inicio:
            _clear_session(db, remetente)
            return {"mensagem": "Erro interno. Tente novamente: extrato", "dados": {"etapa": "reiniciar"}}
        try:
            start_day = _build_day_range(data_inicio)
            end_day = _build_day_range(parsed)
        except HTTPException:
            return {
                "mensagem": "Datas inválidas. Use o formato AAAA-MM-DD.",
                "dados": {"etapa": estado},
            }
        if end_day["start"] < start_day["start"]:
            return {
                "mensagem": "A data final deve ser maior ou igual à data inicial.",
                "dados": {"etapa": estado},
            }
        label = f"{data_inicio} a {parsed}"
        _clear_session(db, remetente)
        return _build_extrato_response(db, start_day["start"], end_day["end"], label)

    return {"mensagem": "Não foi possível continuar o fluxo. Inicie novamente: compra ou venda.", "dados": {"etapa": "reiniciar"}}


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

    # Didactic receipt format: short and easy to read.
    data_hora = datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "-3")))
    data_fmt = data_hora.strftime("%d/%m/%Y %H:%M:%S")

    response_payload: Dict[str, Any] = {
        "mensagem": (
            f"✅ {operacao_texto}\n"
            f"ID: {op_id}\n"
            f"Data: {data_fmt}\n"
            f"Tipo: {tipo_operacao}\n"
            f"Ativo: {nome_ativo_display}\n"
            f"Quantidade: {quantidade}g\n"
            f"Preço: ${money(cotacao)}/g\n"
            f"Total USD: ${total_usd}\n"
            f"Pagamento: {moeda_linha}\n"
            "Operação registrada com sucesso."
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
        raise HTTPException(status_code=400, detail="Mensagem inválida")

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
        msg = _ERROS_AMIGAVEIS.get(exc.status_code, "Não consegui processar. Envie: menu")
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
                "mensagem": "⚠️ Erro inesperado. Tente novamente.",
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
        return _twiml_message("⚠️ Mensagem inválida. Tente novamente.")

    payload = WhatsAppWebhookPayload(remetente=remetente, mensagem=mensagem)
    suppress_reply = _should_suppress_twilio_reply(mensagem)
    db: Optional[DatabaseClient] = None

    try:
        validate_webhook_token(str(token) if token is not None else None)
        db = get_db()

        if provider_message_id:
            existing = db.get_processed_message(provider_message_id)
            if existing and isinstance(existing.get("resposta_payload"), dict):
                if suppress_reply:
                    return _twiml_empty_response()
                return _twiml_message(str(existing["resposta_payload"].get("mensagem") or ""))
            cached = _IDEMPOTENCY_CACHE.get(provider_message_id)
            if cached:
                if suppress_reply:
                    return _twiml_empty_response()
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

        if suppress_reply:
            return _twiml_empty_response()
        return _twiml_message(str(response.get("mensagem") or "Operação processada."))
    except HTTPException as exc:
        msg = _ERROS_AMIGAVEIS.get(exc.status_code, "Não consegui processar. Envie: menu")
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
        if suppress_reply:
            return _twiml_empty_response()
        return _twiml_message(response_payload["mensagem"])
    except Exception:
        logger.exception("Erro inesperado no webhook Twilio")
        if suppress_reply:
            return _twiml_empty_response()
        return _twiml_message("⚠️ Erro inesperado. Tente novamente.")


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
        raise HTTPException(status_code=404, detail="Operação não encontrada")
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
    mensagem_norm = _normalize_text(mensagem)

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
            if estado != "await_resume_confirmacao" and _is_guided_session_stale(session):
                if mensagem_norm in {"cancelar", "cancela", "cancel", "parar", "sair"}:
                    _clear_session(db, remetente)
                    return {
                        "mensagem": "Operação cancelada por você. Quando quiser recomeçar, envie: compra ou venda.",
                        "dados": {"intencao": "fluxo_guiado_cancelado", "acao": "cancelar"},
                    }

                idle_min = _guided_session_idle_minutes(session) or _GUIDED_SESSION_IDLE_MINUTES
                contexto_atual = dict(session.get("contexto", {}))
                _save_session(
                    db,
                    remetente,
                    "await_resume_confirmacao",
                    {
                        "estado_anterior": estado,
                        "contexto_anterior": contexto_atual,
                    },
                )
                return {
                    "mensagem": (
                        f"Ficamos {idle_min} minutos sem mensagens. "
                        "Deseja continuar a transação de onde parou? "
                        "Responda: continuar ou cancelar."
                    ),
                    "dados": {"etapa": "await_resume_confirmacao", "idle_minutos": idle_min},
                }

            # If user sends a fresh operation sentence, reset stale flow and re-interpret.
            if _looks_like_new_operation_start(mensagem):
                _clear_session(db, remetente)
            else:
                return _process_guided_flow(remetente, mensagem, db, session)

    session = _get_session(db, remetente)
    if session:
        estado = str(session.get("estado", ""))
        if estado in _GUIDED_FLOW_STATES:
            return _process_guided_flow(remetente, mensagem, db, session)

    maybe_start = _start_guided_flow_if_requested(remetente, mensagem, db, provider_message_id)
    if maybe_start:
        return maybe_start

    if _is_greeting(mensagem) and _needs_name_onboarding(usuario):
        _save_session(db, remetente, "await_nome_usuario", {"source": "onboarding"})
        return {
            "mensagem": "Olá. Para começar, informe seu nome.",
            "dados": {"etapa": "await_nome_usuario"},
        }

    command_response = _try_handle_whatsapp_commands(db, usuario, remetente, mensagem)
    if command_response is not None:
        return command_response

    try:
        raw_ai_data = extract_message_data(mensagem)
        ai_data = AIExtractedData.model_validate(raw_ai_data)
    except AIServiceError as exc:
        logger.warning("Falha ao extrair dados da IA; aplicando fallback seguro")
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            erro=str(exc),
        )
        ai_data = AIExtractedData(
            intencao="conversar",
            ativo=None,
            quantidade=None,
            valor_informado=None,
            resposta=(
                "Não foi possível interpretar a mensagem. "
                "Tente: 'Comprei 2g de ouro a 105' ou 'Taxa USD 5.40'."
            ),
        )
    except ValidationError as exc:
        logger.warning("Payload da IA inválido; aplicando fallback seguro")
        db.insert_log(
            nivel="warning",
            remetente=remetente,
            mensagem_recebida=mensagem,
            contexto={"ia_payload": raw_ai_data},
            erro=str(exc),
        )
        ai_data = AIExtractedData(
            intencao="conversar",
            ativo=None,
            quantidade=None,
            valor_informado=None,
            resposta=(
                "Dados insuficientes. "
                "Informe o ativo e a quantidade, por exemplo: 'Vendi 3g de ouro'."
            ),
        )

    intencao = ai_data.intencao
    ativo_extraido = ai_data.ativo

    if intencao == "conversar":
        nome_usuario = str(usuario.get("nome") or "").strip()
        keep_menu_state = False
        if _is_help_menu_request(mensagem):
            resposta = _build_whatsapp_checklist_menu()
            _save_session(
                remetente=remetente,
                db=db,
                estado="await_menu_option",
                contexto={"origem": "menu"},
            )
            keep_menu_state = True
        else:
            resposta = ai_data.resposta or (
                "Posso ajudar com operações de ouro, câmbio e consulta de caixa.\n"
                "Digite 'menu' para ver as opções."
            )

        if _is_greeting(mensagem) and nome_usuario:
            resposta = (
                f"Olá, {nome_usuario}.\n"
                "Como posso ajudar?\n"
                "Digite 'menu' para ver as opções."
            )
        response_payload: Dict[str, Any] = {
            "mensagem": resposta,
            "dados": {"intencao": intencao},
        }
        if not keep_menu_state:
            _save_session(
                db=db,
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
        requested_currency = _extract_caixa_currency(mensagem)
        response_payload = _build_caixa_response(db, requested_currency=requested_currency)
        resposta = response_payload["mensagem"]
        day = {"date": str(response_payload["dados"].get("date", ""))}
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
        raise HTTPException(status_code=404, detail="Ativo não encontrado")

    ativo_id = int(ativo["id"])

    if intencao == "registrar_operacao":
        quantidade = parse_decimal(ai_data.quantidade, "quantidade")
        if quantidade <= 0:
            raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

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
                "mensagem": "Em qual moeda foi pago?\nUSD / EUR / SRD / BRL",
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
            "mensagem": f"Informe o preço por grama em USD ({operacao_texto} de {quantidade}g).",
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
        raise HTTPException(status_code=404, detail="Operação não encontrada")

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
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    # Mark as cancelled instead of deleting
    db.client.table("transacoes").update({"status": "cancelada"}).eq("id", operation_id).execute()

    return {
        "mensagem": f"✅ Operação OP-{operation_id} cancelada",
        "dados": {"id": operation_id, "status": "cancelada"},
    }
