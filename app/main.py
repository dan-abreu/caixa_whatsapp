import json
import os
import hmac
import base64
import hashlib
import logging
import re
import unicodedata
from html import escape
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple, cast
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai_service import AIServiceError, extract_message_data
from app.database import DatabaseClient, DatabaseError
from app.multi_agent_system import MultiAgentRequest, MultiAgentResponse, run_multi_agent_orchestration


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

_SAAS_SESSION_COOKIE = os.getenv("SAAS_SESSION_COOKIE", "caixa_saas_session")
_SAAS_SESSION_TTL_SECONDS = int(os.getenv("SAAS_SESSION_TTL_SECONDS", "43200"))
_SAAS_COOKIE_SECURE = os.getenv("SAAS_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


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


def _compute_sale_profit_reference(
    db: DatabaseClient,
    ativo_id: int,
    peso: Decimal,
    total_pago_usd: Decimal,
) -> Optional[Dict[str, str]]:
    taxa_atual = db.get_taxa_atual(ativo_id)
    if not taxa_atual:
        return None

    preco_compra_raw = taxa_atual.get("preco_compra")
    if preco_compra_raw is None:
        return None

    try:
        preco_compra_ref = Decimal(str(preco_compra_raw))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if preco_compra_ref <= 0:
        return None

    custo_ref_usd = money(peso * preco_compra_ref)
    lucro_ref_usd = money(total_pago_usd - custo_ref_usd)
    return {
        "preco_compra_ref_usd": str(money(preco_compra_ref)),
        "custo_ref_usd": str(custo_ref_usd),
        "lucro_ref_usd": str(lucro_ref_usd),
    }


def _attach_sale_profit_reference(db: DatabaseClient, contexto: Dict[str, Any]) -> None:
    if str(contexto.get("tipo_operacao", "")).lower() != "venda":
        return

    try:
        peso = Decimal(str(contexto.get("peso", "0")))
        total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
    except (InvalidOperation, TypeError, ValueError):
        return

    if peso <= 0 or total_pago <= 0:
        return

    ativo = db.get_ativo_by_nome("Ouro")
    if not ativo:
        ativo = db.get_ativo_by_nome("Ouro 24k")
    if not ativo:
        return

    profit_ref = _compute_sale_profit_reference(db, int(ativo["id"]), peso, total_pago)
    if profit_ref:
        contexto.update(profit_ref)

    inventory_txs = db.get_gold_inventory_transactions()
    lots = _build_fifo_inventory_lots(inventory_txs)
    fifo_result = _preview_fifo_sale_consumption(lots, peso)
    consumed_grams = Decimal(str(fifo_result.get("consumed_grams") or "0"))
    consumed_cost = Decimal(str(fifo_result.get("consumed_cost_usd") or "0"))
    shortfall = Decimal(str(fifo_result.get("shortfall_grams") or "0"))
    if consumed_grams > 0 and shortfall == 0:
        contexto.update(
            {
                "profit_method": "fifo_real",
                "custo_fifo_usd": str(money(consumed_cost)),
                "lucro_real_usd": str(money(total_pago - consumed_cost)),
                "consumo_fifo": fifo_result.get("breakdown", []),
            }
        )
    elif shortfall > 0:
        contexto["profit_method"] = "fifo_insufficient_stock"
        contexto["fifo_shortfall_grams"] = str(shortfall)


def _build_fifo_inventory_lots(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lots: List[Dict[str, Any]] = []
    ordered = sorted(
        transactions,
        key=lambda tx: (
            str(tx.get("criado_em") or ""),
            int(tx.get("id") or 0),
        ),
    )
    for tx in ordered:
        tipo = str(tx.get("tipo_operacao") or "").lower()
        if tipo not in {"compra", "venda"}:
            continue

        try:
            peso = Decimal(str(tx.get("peso") or "0"))
        except (InvalidOperation, TypeError, ValueError):
            continue

        if peso <= 0:
            continue

        if tipo == "compra":
            try:
                unit_cost = Decimal(str(tx.get("preco_usd") or "0"))
            except (InvalidOperation, TypeError, ValueError):
                unit_cost = Decimal("0")
            lots.append(
                {
                    "source_id": int(tx.get("id") or 0),
                    "criado_em": str(tx.get("criado_em") or ""),
                    "remaining_grams": peso,
                    "unit_cost_usd": unit_cost,
                }
            )
            continue

        remaining_sale = peso
        while remaining_sale > 0 and lots:
            head = lots[0]
            head_remaining = Decimal(str(head.get("remaining_grams") or "0"))
            if head_remaining <= 0:
                lots.pop(0)
                continue
            consumed = min(head_remaining, remaining_sale)
            head["remaining_grams"] = str(head_remaining - consumed)
            remaining_sale -= consumed
            if Decimal(str(head.get("remaining_grams") or "0")) <= 0:
                lots.pop(0)

    normalized: List[Dict[str, Any]] = []
    for lot in lots:
        remaining = Decimal(str(lot.get("remaining_grams") or "0"))
        if remaining > 0:
            normalized.append(
                {
                    "source_id": int(lot.get("source_id") or 0),
                    "criado_em": str(lot.get("criado_em") or ""),
                    "remaining_grams": str(remaining),
                    "unit_cost_usd": str(Decimal(str(lot.get("unit_cost_usd") or "0"))),
                }
            )
    return normalized


def _preview_fifo_sale_consumption(
    lots: List[Dict[str, Any]],
    peso_venda: Decimal,
) -> Dict[str, Any]:
    remaining_sale = peso_venda
    consumed_cost = Decimal("0")
    consumed_grams = Decimal("0")
    breakdown: List[Dict[str, Any]] = []

    working_lots = [dict(lot) for lot in lots]
    for lot in working_lots:
        if remaining_sale <= 0:
            break
        lot_remaining = Decimal(str(lot.get("remaining_grams") or "0"))
        if lot_remaining <= 0:
            continue
        unit_cost = Decimal(str(lot.get("unit_cost_usd") or "0"))
        consumed = min(lot_remaining, remaining_sale)
        cost_usd = money(consumed * unit_cost)
        breakdown.append(
            {
                "source_id": int(lot.get("source_id") or 0),
                "grams": str(consumed),
                "unit_cost_usd": str(money(unit_cost)),
                "cost_usd": str(cost_usd),
            }
        )
        consumed_cost += cost_usd
        consumed_grams += consumed
        remaining_sale -= consumed

    return {
        "consumed_grams": consumed_grams,
        "consumed_cost_usd": money(consumed_cost),
        "shortfall_grams": remaining_sale if remaining_sale > 0 else Decimal("0"),
        "breakdown": breakdown,
    }


def _compute_inventory_metrics(transactions: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    lots = _build_fifo_inventory_lots(transactions)
    total_grams = sum((Decimal(str(lot.get("remaining_grams") or "0")) for lot in lots), Decimal("0"))
    total_cost = sum(
        (
            Decimal(str(lot.get("remaining_grams") or "0"))
            * Decimal(str(lot.get("unit_cost_usd") or "0"))
            for lot in lots
        ),
        Decimal("0"),
    )
    avg_cost = money(total_cost / total_grams) if total_grams > 0 else Decimal("0")
    return {
        "available_grams": total_grams,
        "inventory_cost_usd": money(total_cost),
        "avg_cost_usd_per_gram": avg_cost,
    }


def _project_caixa_balances(
    current_saldos: Dict[str, Any],
    tipo_operacao: str,
    peso_gramas: Decimal,
    pagamentos: List[Dict[str, Any]],
) -> Dict[str, Decimal]:
    projected = {
        moeda.upper(): Decimal(str(valor))
        for moeda, valor in current_saldos.items()
    }
    for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]:
        projected.setdefault(moeda, Decimal("0"))

    if peso_gramas > 0:
        projected["XAU"] += peso_gramas if tipo_operacao == "compra" else -peso_gramas

    for pagamento in pagamentos:
        moeda = str(pagamento.get("moeda") or "USD").upper()
        valor_moeda = Decimal(str(pagamento.get("valor_moeda") or "0"))
        if moeda not in projected or valor_moeda == 0:
            continue
        projected[moeda] += -valor_moeda if tipo_operacao == "compra" else valor_moeda

    return projected


def _find_negative_caixa_balances(projected_saldos: Dict[str, Decimal]) -> List[Tuple[str, Decimal]]:
    negatives: List[Tuple[str, Decimal]] = []
    for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]:
        saldo = projected_saldos.get(moeda, Decimal("0"))
        if saldo < 0:
            negatives.append((moeda, saldo))
    return negatives


def _format_negative_caixa_lines(negatives: List[Tuple[str, Decimal]]) -> List[str]:
    lines: List[str] = []
    for moeda, saldo in negatives:
        lines.append(f"- {moeda}: {_format_caixa_movement(moeda, saldo)}")
    return lines


def _should_reset_guided_session_for_message(message: str) -> bool:
    text = _normalize_text(message)
    if _looks_like_new_operation_start(message) or _is_greeting(message):
        return True
    global_commands = ["menu", "caixa", "extrato", "ajuda", "help", "taxa", "relatorio", "relatório"]
    return any(text.startswith(cmd) for cmd in global_commands)


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
                    "compra",
                    "venda",
                    "compra ouro 2g"
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
    400: "Não entendi. Tente assim: compra | venda | caixa | extrato | taxa ouro 70.00",
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


def _env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_float(name: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    raw = os.getenv(name, str(default)).strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


_MOEDAS_SUPORTADAS = ["USD", "SRD", "EUR", "BRL"]
_RISK_DIFF_LIMIT_USD = Decimal(os.getenv("RISK_DIFF_LIMIT_USD", "250"))
_GUIDED_SESSION_IDLE_MINUTES = int(os.getenv("GUIDED_SESSION_IDLE_MINUTES", "5"))
_MULTI_AGENT_AUTO_ENABLED = os.getenv("MULTI_AGENT_AUTO_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
_MULTI_AGENT_AUTO_MIN_USD = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_USD", "500"))
_MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS = Decimal(os.getenv("MULTI_AGENT_AUTO_MIN_WEIGHT_GRAMS", "10"))
_AI_CONF_PRESETS: Dict[str, Dict[str, float]] = {
    "balanced": {
        "samples_target": 300,
        "risk_weight": 0.7,
        "failsafe_weight": 1.3,
        "weight_maturity": 45,
        "weight_stability": 45,
        "weight_alerts": 10,
        "band_excellent": 85,
        "band_good": 70,
        "band_moderate": 50,
    },
    "conservative": {
        "samples_target": 450,
        "risk_weight": 0.9,
        "failsafe_weight": 1.8,
        "weight_maturity": 35,
        "weight_stability": 55,
        "weight_alerts": 10,
        "band_excellent": 90,
        "band_good": 78,
        "band_moderate": 60,
    },
    "aggressive": {
        "samples_target": 220,
        "risk_weight": 0.55,
        "failsafe_weight": 1.0,
        "weight_maturity": 55,
        "weight_stability": 35,
        "weight_alerts": 10,
        "band_excellent": 82,
        "band_good": 66,
        "band_moderate": 45,
    },
}
_ai_conf_profile_setting = os.getenv("AI_CONF_PROFILE", "balanced").strip().lower()
if _ai_conf_profile_setting not in {*_AI_CONF_PRESETS.keys(), "auto"}:
    _ai_conf_profile_setting = "balanced"
_AI_CONF_PROFILE_SETTING = _ai_conf_profile_setting


def _resolve_auto_ai_conf_profile(total_samples: int) -> str:
    if total_samples >= 300:
        return "conservative"
    if total_samples >= 30:
        return "balanced"
    return "aggressive"


def _get_ai_conf_config(total_samples: int) -> Dict[str, Any]:
    selected_profile = _AI_CONF_PROFILE_SETTING
    if selected_profile == "auto":
        selected_profile = _resolve_auto_ai_conf_profile(total_samples)

    defaults = _AI_CONF_PRESETS[selected_profile]
    samples_target = _env_int("AI_CONF_SAMPLES_TARGET", int(defaults["samples_target"]), minimum=50, maximum=5000)
    risk_weight = _env_float("AI_CONF_RISK_WEIGHT", float(defaults["risk_weight"]), minimum=0.0, maximum=5.0)
    failsafe_weight = _env_float("AI_CONF_FAILSAFE_WEIGHT", float(defaults["failsafe_weight"]), minimum=0.0, maximum=5.0)
    weight_maturity = _env_float("AI_CONF_WEIGHT_MATURITY", float(defaults["weight_maturity"]), minimum=0.0, maximum=100.0)
    weight_stability = _env_float("AI_CONF_WEIGHT_STABILITY", float(defaults["weight_stability"]), minimum=0.0, maximum=100.0)
    weight_alerts = _env_float("AI_CONF_WEIGHT_ALERTS", float(defaults["weight_alerts"]), minimum=0.0, maximum=100.0)
    band_excellent = _env_int("AI_CONF_BAND_EXCELLENT", int(defaults["band_excellent"]), minimum=1, maximum=100)
    band_good = _env_int("AI_CONF_BAND_GOOD", int(defaults["band_good"]), minimum=1, maximum=100)
    band_moderate = _env_int("AI_CONF_BAND_MODERATE", int(defaults["band_moderate"]), minimum=1, maximum=100)

    return {
        "profile_setting": _AI_CONF_PROFILE_SETTING,
        "profile_effective": selected_profile,
        "samples_target": samples_target,
        "risk_weight": risk_weight,
        "failsafe_weight": failsafe_weight,
        "weight_maturity": weight_maturity,
        "weight_stability": weight_stability,
        "weight_alerts": weight_alerts,
        "band_excellent": band_excellent,
        "band_good": band_good,
        "band_moderate": band_moderate,
    }
_GUIDED_FLOW_STATES = {
    "await_menu_option",
    "await_menu_tipo_operacao",
    "await_nome_usuario",
    "await_caixa_detalhe",
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
    if cleaned in {"", "-", ".", "-.", ".-"}:
        return Decimal("-1")
    try:
        return parse_decimal(cleaned, field_name)
    except HTTPException:
        return Decimal("-1")


def _extract_confirmacao(value: str) -> Optional[bool]:
    text = _normalize_text(value)
    if text in {"sim", "confirmar", "ok", "confirmo", "s", "1"}:
        return True
    if text in {"nao", "não", "cancelar", "n", "cancela", "2"}:
        return False
    return None


def _navigation_hint() -> str:
    return "\n\nDigite voltar para retornar ou cancelar para encerrar."


def _parse_single_currency_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    number_map = {"1": "USD", "2": "EUR", "3": "SRD", "4": "BRL"}
    if text in number_map:
        return number_map[text]

    aliases = {
        "usd": "USD",
        "dolar": "USD",
        "dolares": "USD",
        "dolar americano": "USD",
        "eur": "EUR",
        "euro": "EUR",
        "euros": "EUR",
        "srd": "SRD",
        "brl": "BRL",
        "real": "BRL",
        "reais": "BRL",
    }
    return aliases.get(text)


def _parse_origem_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    if text == "1":
        return "balcao"
    if text == "2":
        return "fora"
    if text in {"balcao", "balcão"}:
        return "balcao"
    if text == "fora":
        return "fora"
    return None


def _parse_forma_pagamento_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    number_map = {
        "1": "dinheiro",
        "2": "transferencia",
        "3": "cheque",
        "4": "misto",
    }
    if text in number_map:
        return number_map[text]
    if text in {"dinheiro", "transferencia", "cheque", "misto"}:
        return text
    return None


def _parse_fechamento_tipo_choice(value: str) -> Optional[str]:
    text = _normalize_text(value)
    if text == "1":
        return "total"
    if text == "2":
        return "parcial"
    if text in {"total", "parcial"}:
        return text
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


# Strength ordering — stronger = lower number (numerator of the pair prompt).
_MOEDA_STRENGTH: Dict[str, int] = {"EUR": 0, "USD": 1, "BRL": 2, "SRD": 3}


def _build_pair_cambio_prompt(base: str, payment: str) -> str:
    """Return the natural pair prompt: '1 STRONGER = quantos WEAKER?'"""
    b, p = base.upper(), payment.upper()
    if _MOEDA_STRENGTH.get(b, 5) <= _MOEDA_STRENGTH.get(p, 5):
        return f"1 {b} = quantos {p}?"
    return f"1 {p} = quantos {b}?"


def _pair_rate_to_payment_per_usd(
    base: str,
    payment: str,
    user_rate: Decimal,
    db: "DatabaseClient",
) -> Tuple[Optional[Decimal], Decimal, Optional[Decimal]]:
    """Convert a direct B/P pair rate (direction per _build_pair_cambio_prompt) to
    (payment_per_usd, pair_P_per_B, c_base_per_usd)."""
    b, p = base.upper(), payment.upper()
    if _MOEDA_STRENGTH.get(b, 5) <= _MOEDA_STRENGTH.get(p, 5):
        pair_p_per_b = user_rate                          # prompt: "1 B = R P"
    else:
        pair_p_per_b = fx_rate(Decimal("1") / user_rate) if user_rate > 0 else Decimal("1")

    # Primary: B/USD from DB -> pay_per_usd = P_per_B x B_per_USD
    raw_base = db.get_last_cambio_para_usd(b)
    if raw_base and Decimal(str(raw_base)) > 0:
        c_base = Decimal(str(raw_base))
        return fx_rate(pair_p_per_b * c_base), pair_p_per_b, c_base

    # Fallback: P/USD directly from DB
    raw_pay = db.get_last_cambio_para_usd(p)
    if raw_pay and Decimal(str(raw_pay)) > 0:
        return Decimal(str(raw_pay)), pair_p_per_b, None

    return None, pair_p_per_b, None


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
        "1": "XAU",
        "2": "EUR",
        "3": "USD",
        "4": "SRD",
        "5": "BRL",
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
    if text in aliases:
        return aliases[text]
    for token in re.split(r"[^a-zA-Z0-9]+", text):
        if token in aliases:
            return aliases[token]
    return None


def _format_caixa_movement(currency: str, movement: Decimal) -> str:
    signal = "+" if movement >= 0 else "-"
    magnitude = abs(movement)
    if currency == "XAU":
        return f"{signal}{magnitude:,.3f} g"
    if currency == "USD":
        return f"{signal}$ {magnitude:,.2f}"
    if currency == "EUR":
        return f"{signal}EUR {magnitude:,.2f}"
    if currency == "SRD":
        return f"{signal}SRD {magnitude:,.2f}"
    if currency == "BRL":
        return f"{signal}R$ {magnitude:,.2f}"
    return f"{signal}{currency} {magnitude:,.2f}"


def _build_caixa_detail_response(
    db: DatabaseClient,
    currency: str,
    start_iso: str,
    end_iso: str,
    label_periodo: str,
) -> Dict[str, Any]:
    currency_up = currency.upper()
    saldo = db.get_saldo_caixa()
    transactions = db.get_extrato_transactions(start_iso, end_iso)
    tz_offset_hours = int(os.getenv("TZ_OFFSET_HOURS", "-3"))

    caixa_titles = {
        "XAU": "CAIXA OURO (XAU)",
        "EUR": "CAIXA EURO (EUR)",
        "USD": "CAIXA DOLAR (USD)",
        "SRD": "CAIXA SURINAMES (SRD)",
        "BRL": "CAIXA REAL (BRL)",
    }

    movement_rows: List[Dict[str, Any]] = []
    total_entries = Decimal("0")
    total_exits = Decimal("0")
    total_sale_profit = Decimal("0")

    for tx in transactions:
        tipo = str(tx.get("tipo_operacao") or "").lower()
        if tipo not in {"compra", "venda", "cambio"}:
            continue

        movement = Decimal("0")
        if currency_up == "XAU":
            peso = Decimal(str(tx.get("peso") or "0"))
            if tipo == "compra":
                movement = peso
            elif tipo in {"venda", "cambio"}:
                movement = -peso
        else:
            pagamentos_raw = tx.get("pagamentos")
            pagamentos = cast(List[Dict[str, Any]], pagamentos_raw) if isinstance(pagamentos_raw, list) else []
            if pagamentos:
                for pagamento in pagamentos:
                    moeda = str(pagamento.get("moeda") or "USD").upper()
                    if moeda != currency_up:
                        continue
                    valor_moeda = Decimal(str(pagamento.get("valor_moeda") or "0"))
                    movement += -valor_moeda if tipo == "compra" else valor_moeda
            else:
                moeda = str(tx.get("moeda") or "USD").upper()
                if moeda == currency_up:
                    valor_moeda = Decimal(str(tx.get("valor_moeda") or tx.get("total_usd") or "0"))
                    movement = -valor_moeda if tipo == "compra" else valor_moeda

        if movement == 0:
            continue

        raw_dt = str(tx.get("criado_em") or "")
        try:
            dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            dt_local = dt + timedelta(hours=tz_offset_hours)
            data_fmt = dt_local.strftime("%d/%m/%Y %H:%M")
        except Exception:
            data_fmt = raw_dt[:16]

        if movement > 0:
            total_entries += movement
        else:
            total_exits += abs(movement)

        lucro_venda: Optional[Decimal] = None
        if tipo == "venda" and isinstance(tx.get("contexto"), dict):
            ctx_tx = cast(Dict[str, Any], tx.get("contexto") or {})
            lucro_raw = ctx_tx.get("lucro_real_usd")
            if lucro_raw is None:
                lucro_raw = ctx_tx.get("lucro_ref_usd")
            if lucro_raw is not None:
                try:
                    lucro_venda = Decimal(str(lucro_raw))
                    total_sale_profit += lucro_venda
                except (InvalidOperation, TypeError, ValueError):
                    lucro_venda = None

        movement_rows.append(
            {
                "tx_id": str(tx.get("id") or "-"),
                "data_fmt": data_fmt,
                "tipo": tipo.upper(),
                "movimento": movement,
                "cliente": str(tx.get("pessoa") or "").strip(),
                "operador": str(tx.get("operador_id") or "").strip(),
                "lucro_usd": lucro_venda,
            }
        )

    saldo_atual = Decimal(str(saldo.get(currency_up, "0")))
    lines = [
        f"EXTRATO {caixa_titles.get(currency_up, currency_up)}",
        f"Periodo: {label_periodo}",
        "================================",
    ]

    if movement_rows:
        for i, row in enumerate(movement_rows):
            if i > 0:
                lines.append("--------------------------------")
            lines.append(f"ID: #{row['tx_id']}  |  {row['data_fmt']}")
            lines.append(f"Tipo:     {row['tipo']}")
            lines.append(f"Cliente:  {row['cliente'][:40] if row['cliente'] else '—'}")
            lines.append(f"Operador: {row['operador'][:40] if row['operador'] else '—'}")
            lines.append(f"Valor:    {_format_caixa_movement(currency_up, cast(Decimal, row['movimento']))}")
            lucro_usd = row.get("lucro_usd")
            if isinstance(lucro_usd, Decimal):
                lines.append(f"Lucro:    USD {money(lucro_usd)}")
    else:
        lines.append("Nenhuma movimentacao neste periodo.")

    lines.extend(
        [
            "================================",
            f"Entradas: {_format_caixa_movement(currency_up, total_entries)}",
            f"Saidas:   {_format_caixa_movement(currency_up, -total_exits)}",
            f"Saldo:    {_format_caixa_movement(currency_up, saldo_atual)}",
        ]
    )
    if movement_rows:
        lines.append(f"Ops:      {len(movement_rows)}")
    if total_sale_profit != 0:
        lines.append(f"Lucro vendas: USD {money(total_sale_profit)}")

    return {
        "mensagem": "\n".join(lines),
        "dados": {
            "intencao": "consultar_relatorio",
            "requested_currency": currency_up,
            "periodo": label_periodo,
            "movimentos": len(movement_rows),
            "saldo_atual": str(saldo_atual),
        },
    }


def _persist_gold_operation_from_context(
    db: DatabaseClient,
    remetente: str,
    contexto: Dict[str, Any],
    post_save_session: bool = True,
) -> Dict[str, Any]:
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
    tipo_operacao = str(contexto.get("tipo_operacao", "compra"))
    if tipo_operacao == "venda":
        _attach_sale_profit_reference(db, contexto)

    pagamentos = list(contexto.get("pagamentos", []))
    header_payload: Dict[str, Any] = {
        "tipo_operacao": tipo_operacao,
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
        tipo_operacao=tipo_operacao,
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
        "tipo_operacao": tipo_operacao,
        "origem": str(contexto.get("origem", "balcao")),
        "teor": contexto.get("teor"),
        "peso": str(peso),
        "preco_usd": str(money(preco)),
        "total_usd": str(total),
        "total_pago_usd": str(money(total_pago)),
        "diferenca_usd": str(diferenca),
        "fechamento_gramas": contexto.get("fechamento_gramas"),
        "forma_pagamento": str(contexto.get("forma_pagamento", "dinheiro")),
        "pagamentos": pagamentos,
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

    if post_save_session:
        _save_session(db, remetente, "await_caixa_detalhe", {"source": "post_operacao"})

    alerta = "" if not risco_diferenca else " ⚠️ Atenção: verificar diferença."
    gt_id = gold_transaction.get("id") if isinstance(gold_transaction, dict) else None
    tx_id = transacao.get("id")
    if gt_id:
        id_linha = f"ID: GT-{gt_id}\n"
    elif tx_id:
        id_linha = f"ID: T-{tx_id}\n"
    else:
        id_linha = ""

    caixa_resp = _build_caixa_response(db)
    caixa_msg = str(caixa_resp.get("mensagem", ""))
    direcao_txt = "Saiu" if tipo_operacao == "compra" else "Entrou"
    direcao_ouro_txt = "Entrou" if tipo_operacao == "compra" else "Saiu"
    mov_linhas: List[str] = [f"- {direcao_ouro_txt} ouro: {peso:,.3f}g"]
    for pagamento in pagamentos:
        moeda_pg = str(pagamento.get("moeda", "USD")).upper()
        valor_moeda_pg = Decimal(str(pagamento.get("valor_moeda", "0")))
        mov_linhas.append(f"- {direcao_txt} {moeda_pg}: {money(valor_moeda_pg)}")
    mov_txt = "\n".join(mov_linhas) if mov_linhas else "- Sem movimentação registrada"

    response_payload: Dict[str, Any] = {
        "mensagem": (
            f"✅ Operação salva com sucesso.\n"
            f"{id_linha}"
            f"Tipo: {tipo_operacao}\n"
            f"Peso: {peso:,.3f}g\n"
            "Movimentação dos 5 caixas:\n"
            f"{mov_txt}{alerta}\n"
            "════════════════════════════════\n"
            f"{caixa_msg}"
        ),
        "dados": {
            "intencao": "fluxo_guiado_confirmado",
            "tipo_operacao": contexto.get("tipo_operacao"),
            "peso": str(peso),
            "pagamentos": pagamentos,
            "gold_transaction_id": gt_id,
            "transacao_id": tx_id,
        },
    }
    if review_payload:
        response_payload["dados"]["analise_multiagente"] = review_payload
    return response_payload


def _normalize_user_phone(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    return f"+{digits}"


def _validate_web_pin_format(pin: str) -> str:
    normalized = str(pin or "").strip()
    if not re.fullmatch(r"\d{4,12}", normalized):
        raise HTTPException(status_code=400, detail="PIN web deve ter entre 4 e 12 dígitos numéricos")
    return normalized


def _get_saas_session_secret() -> str:
    return (
        os.getenv("SAAS_SESSION_SECRET")
        or os.getenv("WEBHOOK_TOKEN")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
        or "caixa-saas-dev-secret"
    )


def _encode_saas_session(telefone: str) -> str:
    expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=_SAAS_SESSION_TTL_SECONDS)).timestamp())
    payload = f"{telefone}|{expires_at}"
    payload_token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    signature = hmac.new(
        _get_saas_session_secret().encode("utf-8"),
        payload_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_token}.{signature}"


def _decode_saas_session(raw_cookie: Optional[str]) -> Optional[str]:
    if not raw_cookie or "." not in raw_cookie:
        return None
    payload_token, signature = raw_cookie.rsplit(".", 1)
    expected_signature = hmac.new(
        _get_saas_session_secret().encode("utf-8"),
        payload_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = base64.urlsafe_b64decode(payload_token.encode("ascii")).decode("utf-8")
        telefone, expires_at_raw = payload.split("|", 1)
        if int(expires_at_raw) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return telefone
    except Exception:
        return None


def _set_saas_session(response: Response, telefone: str) -> None:
    response.set_cookie(
        key=_SAAS_SESSION_COOKIE,
        value=_encode_saas_session(telefone),
        httponly=True,
        secure=_SAAS_COOKIE_SECURE,
        samesite="lax",
        max_age=_SAAS_SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_saas_session(response: Response) -> None:
    response.delete_cookie(key=_SAAS_SESSION_COOKIE, path="/")


def _get_saas_authenticated_user(request: Request, db: DatabaseClient) -> Optional[Dict[str, Any]]:
    telefone = _decode_saas_session(request.cookies.get(_SAAS_SESSION_COOKIE))
    if not telefone:
        return None
    usuario = db.get_usuario_web_auth(telefone)
    if not usuario:
        return None
    enriched = dict(usuario)
    enriched["web_pin_bootstrap_required"] = not bool(enriched.get("web_pin_hash"))
    return enriched


def _derive_forma_pagamento_summary(pagamentos: List[Dict[str, Any]]) -> str:
    if not pagamentos:
        return "dinheiro"
    methods = {str(item.get("forma_pagamento") or "dinheiro") for item in pagamentos}
    if len(methods) == 1:
        method = next(iter(methods))
        if method in {"dinheiro", "transferencia", "cheque"}:
            return method
    return "misto"


def _build_web_payment_rows_html(values: Dict[str, str]) -> str:
    rows: List[str] = []
    for index in range(1, 5):
        currency_key = f"payment_{index}_moeda"
        amount_key = f"payment_{index}_valor"
        fx_key = f"payment_{index}_cambio"
        method_key = f"payment_{index}_forma"
        moeda = values.get(currency_key, "USD" if index == 1 else "")
        valor = values.get(amount_key, "")
        cambio = values.get(fx_key, "1" if moeda == "USD" and index == 1 else "")
        forma = values.get(method_key, "dinheiro")
        rows.append(
            f"""
            <div class='payment-row'>
                <label>Moeda #{index}
                    <select name='{currency_key}'>
                        <option value='' {'selected' if not moeda else ''}>-</option>
                        <option value='USD' {'selected' if moeda=='USD' else ''}>USD</option>
                        <option value='EUR' {'selected' if moeda=='EUR' else ''}>EUR</option>
                        <option value='SRD' {'selected' if moeda=='SRD' else ''}>SRD</option>
                        <option value='BRL' {'selected' if moeda=='BRL' else ''}>BRL</option>
                    </select>
                </label>
                <label>Valor na moeda
                    <input name='{amount_key}' value='{escape(valor)}' placeholder='ex.: 380' />
                </label>
                <label>Câmbio para USD
                    <input name='{fx_key}' value='{escape(cambio)}' placeholder='vazio = último câmbio' />
                </label>
                <label>Forma
                    <select name='{method_key}'>
                        <option value='dinheiro' {'selected' if forma=='dinheiro' else ''}>Dinheiro</option>
                        <option value='transferencia' {'selected' if forma=='transferencia' else ''}>Transferência</option>
                        <option value='cheque' {'selected' if forma=='cheque' else ''}>Cheque</option>
                    </select>
                </label>
            </div>
            """
        )
    return "".join(rows)


def _parse_decimal_web_field(raw: str, field_name: str) -> Decimal:
    return parse_decimal(str(raw or "0").strip().replace(",", "."), field_name)


def _parse_web_payments_from_form(db: DatabaseClient, form: Dict[str, str]) -> List[Dict[str, Any]]:
    pagamentos: List[Dict[str, Any]] = []
    for index in range(1, 5):
        currency_key = f"payment_{index}_moeda"
        amount_key = f"payment_{index}_valor"
        fx_key = f"payment_{index}_cambio"
        method_key = f"payment_{index}_forma"
        moeda_raw = str(form.get(currency_key) or "").strip().upper()
        valor_raw = str(form.get(amount_key) or "").strip()
        cambio_raw = str(form.get(fx_key) or "").strip()
        forma = _normalize_text(str(form.get(method_key) or "dinheiro"))

        if not any([moeda_raw, valor_raw, cambio_raw]):
            continue
        if not moeda_raw or not valor_raw:
            raise HTTPException(status_code=400, detail=f"Pagamento #{index} incompleto")
        if moeda_raw not in {"USD", "EUR", "SRD", "BRL"}:
            raise HTTPException(status_code=400, detail=f"Moeda inválida no pagamento #{index}")
        if forma not in {"dinheiro", "transferencia", "cheque"}:
            raise HTTPException(status_code=400, detail=f"Forma inválida no pagamento #{index}")

        valor_moeda = _parse_decimal_web_field(valor_raw, amount_key)
        if valor_moeda <= 0:
            raise HTTPException(status_code=400, detail=f"Valor do pagamento #{index} deve ser maior que zero")

        if moeda_raw == "USD":
            cambio_para_usd = Decimal("1")
        elif cambio_raw:
            cambio_para_usd = _normalize_cambio_para_usd(moeda_raw, _parse_decimal_web_field(cambio_raw, fx_key))
        else:
            last_cambio = db.get_last_cambio_para_usd(moeda_raw)
            if not last_cambio or Decimal(str(last_cambio)) <= 0:
                raise HTTPException(status_code=400, detail=f"Sem câmbio disponível para {moeda_raw} no pagamento #{index}")
            cambio_para_usd = fx_rate(Decimal(str(last_cambio)))

        if cambio_para_usd <= 0:
            raise HTTPException(status_code=400, detail=f"Câmbio inválido no pagamento #{index}")

        pagamentos.append(
            {
                "moeda": moeda_raw,
                "valor_moeda": str(money(valor_moeda)),
                "cambio_para_usd": str(cambio_para_usd),
                "valor_usd": str(money(valor_moeda / cambio_para_usd)),
                "forma_pagamento": forma,
            }
        )

    if pagamentos:
        return pagamentos

    total_pago_raw = str(form.get("total_pago_usd") or "").strip()
    forma_pagamento = _normalize_text(str(form.get("forma_pagamento") or "dinheiro"))
    if total_pago_raw:
        total_pago = _parse_decimal_web_field(total_pago_raw, "total_pago_usd")
        if total_pago <= 0:
            raise HTTPException(status_code=400, detail="Total pago deve ser maior que zero")
        return [
            {
                "moeda": "USD",
                "valor_moeda": str(money(total_pago)),
                "cambio_para_usd": "1",
                "valor_usd": str(money(total_pago)),
                "forma_pagamento": forma_pagamento if forma_pagamento in {"dinheiro", "transferencia", "cheque"} else "dinheiro",
            }
        ]

    raise HTTPException(status_code=400, detail="Informe ao menos um pagamento")


async def _request_form_dict(request: Request) -> Dict[str, str]:
    raw_text = ""
    try:
        raw_text = (await request.body()).decode("utf-8", errors="ignore")
    except Exception:
        raw_text = ""

    try:
        form = await request.form()
        return {str(k): str(v) for k, v in dict(form).items()}
    except Exception:
        pass

    try:
        parsed = parse_qs(raw_text)
        return {k: v[0] for k, v in parsed.items() if v}
    except Exception:
        return {}


def _dashboard_default_form_values(session_user: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    operador = str((session_user or {}).get("telefone") or "+59711111111")
    return {
        "operador_id": operador,
        "tipo_operacao": "compra",
        "origem": "balcao",
        "teor": "90",
        "peso": "",
        "preco_usd": "",
        "fechamento_gramas": "",
        "fechamento_tipo": "total",
        "pessoa": "",
        "forma_pagamento": "dinheiro",
        "total_pago_usd": "",
        "observacoes": "",
        "console_remetente": operador,
        "console_mensagem": "menu",
        "payment_1_moeda": "USD",
        "payment_1_valor": "",
        "payment_1_cambio": "1",
        "payment_1_forma": "dinheiro",
        "payment_2_moeda": "",
        "payment_2_valor": "",
        "payment_2_cambio": "",
        "payment_2_forma": "dinheiro",
        "payment_3_moeda": "",
        "payment_3_valor": "",
        "payment_3_cambio": "",
        "payment_3_forma": "dinheiro",
        "payment_4_moeda": "",
        "payment_4_valor": "",
        "payment_4_cambio": "",
        "payment_4_forma": "dinheiro",
    }


def _render_saas_login_html(message: Optional[str] = None, telefone: str = "") -> str:
    alert = ""
    if message:
        alert = f"<div class='alert error'>{escape(message)}</div>"
    return f"""
    <html>
        <head>
            <title>Caixa SaaS</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <link rel='preconnect' href='https://fonts.googleapis.com'>
            <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>
            <link href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap' rel='stylesheet'>
            <style>
                :root {{ --bg: #f4efe6; --panel: #fffaf2; --ink: #1b1a17; --muted: #6f695d; --accent: #a36a00; --accent-2: #184f3f; --line: #e6d8bc; --error: #8f2d1d; }}
                * {{ box-sizing: border-box; }}
                body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: 'Space Grotesk', 'Segoe UI', sans-serif; background: radial-gradient(circle at top, #f8f2e7 0%, #efe6d6 40%, #e5d9c4 100%); color: var(--ink); }}
                .shell {{ width: min(520px, calc(100vw - 32px)); background: var(--panel); border: 1px solid var(--line); border-radius: 28px; padding: 28px; box-shadow: 0 24px 80px rgba(67, 46, 7, 0.12); }}
                h1 {{ margin: 0 0 8px; font-size: 32px; }}
                p {{ color: var(--muted); line-height: 1.5; }}
                label {{ display: block; margin: 18px 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
                input {{ width: 100%; padding: 14px 16px; border-radius: 14px; border: 1px solid var(--line); font: inherit; background: white; }}
                button {{ width: 100%; margin-top: 18px; padding: 14px 18px; border: 0; border-radius: 14px; font: inherit; font-weight: 700; color: white; background: linear-gradient(135deg, var(--accent-2), var(--accent)); cursor: pointer; }}
                .alert {{ margin: 16px 0; padding: 12px 14px; border-radius: 14px; }}
                .error {{ background: #fde8e3; color: var(--error); border: 1px solid #efc3b8; }}
            </style>
        </head>
        <body>
            <div class='shell'>
                <h1>Caixa SaaS</h1>
                <p>Painel web para operar o mesmo motor do WhatsApp com leitura mais clara, relatórios e entrada rápida de dados.</p>
                {alert}
                <form method='post' action='/saas/login'>
                    <label>Telefone do operador</label>
                    <input name='telefone' value='{escape(telefone)}' placeholder='+59711111111' required />
                    <label>PIN web</label>
                    <input type='password' name='pin' inputmode='numeric' placeholder='Seu PIN numérico' required />
                    <p class='hint'>Primeiro acesso após a migração: use os últimos 6 dígitos do telefone e troque o PIN logo após entrar.</p>
                    <button type='submit'>Entrar no painel</button>
                </form>
            </div>
        </body>
    </html>
    """


def _render_saas_dashboard_html(
    db: DatabaseClient,
    session_user: Dict[str, Any],
    notice: Optional[str] = None,
    notice_kind: str = "info",
    assistant_result: Optional[Dict[str, Any]] = None,
    form_values: Optional[Dict[str, str]] = None,
) -> str:
    values = dict(_dashboard_default_form_values(session_user))
    if form_values:
        values.update({k: str(v) for k, v in form_values.items()})

    day = _build_day_range(None)
    week = _build_week_range()
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    saldo = db.get_saldo_caixa()
    inventory = db.get_gold_inventory_status()
    if not inventory.get("lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status()
    recent_ops = db.get_extrato_transactions(week["start"], week["end"])[-12:]

    balances_html = "".join(
        f"<div class='balance'><span>{escape(moeda)}</span><strong>{escape(_format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</strong></div>"
        for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]
    )

    lot_rows = cast(List[Dict[str, Any]], inventory.get("open_lots") or [])[:8]
    lots_html = "".join(
        f"<tr><td>GT-{escape(str(item.get('source_transaction_id', '')))}</td><td>{escape(str(item.get('remaining_grams', '0')))} g</td><td>USD {escape(str(item.get('unit_cost_usd', '0')))}</td></tr>"
        for item in lot_rows
    ) or "<tr><td colspan='3'>Sem lotes abertos.</td></tr>"

    recent_rows = []
    for item in reversed(recent_ops):
        source = str(item.get("source") or "transacoes")
        tid = str(item.get("id") or "-")
        id_label = f"GT-{tid}" if source == "gold_transactions" else f"T-{tid}"
        recent_rows.append(
            f"<tr><td>{escape(id_label)}</td><td>{escape(str(item.get('tipo_operacao') or '-').upper())}</td><td>{escape(str(item.get('pessoa') or '-'))}</td><td>{escape(str(item.get('peso') or '0'))} g</td><td>USD {escape(str(item.get('total_usd') or '0'))}</td></tr>"
        )
    recent_html = "".join(recent_rows) or "<tr><td colspan='5'>Nenhuma operação recente.</td></tr>"

    notice_html = ""
    if notice:
        notice_html = f"<div class='notice {escape(notice_kind)}'>{escape(notice)}</div>"

    assistant_html = ""
    if assistant_result:
        assistant_message = escape(str(assistant_result.get("mensagem") or ""))
        assistant_data = escape(json.dumps(assistant_result.get("dados") or {}, ensure_ascii=False, indent=2))
        assistant_html = f"<div class='console-output'><h3>Resposta do operador virtual</h3><pre>{assistant_message}</pre><details><summary>Dados técnicos</summary><pre>{assistant_data}</pre></details></div>"

    user_name = escape(str(session_user.get("nome") or session_user.get("telefone") or "Operador"))
    user_phone = escape(str(session_user.get("telefone") or "-"))
    user_role = escape(str(session_user.get("tipo_usuario") or "operador"))
    bootstrap_notice = ""
    if session_user.get("web_pin_bootstrap_required"):
        bootstrap_notice = "<div class='notice error'>PIN temporário em uso. Troque o PIN agora para remover o bootstrap de login.</div>"
    payment_rows_html = _build_web_payment_rows_html(values)
    return f"""
    <html>
        <head>
            <title>Caixa SaaS Dashboard</title>
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <link rel='preconnect' href='https://fonts.googleapis.com'>
            <link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>
            <link href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap' rel='stylesheet'>
            <style>
                :root {{ --bg: #f6f0e4; --panel: rgba(255,252,245,.88); --ink: #1b1a17; --muted: #6f695d; --line: #e7dbc5; --gold: #ad7400; --green: #1d5844; --green-2: #2f7760; --danger: #8f2d1d; --danger-bg: #fde9e5; --info-bg: #e7f5ef; }}
                * {{ box-sizing: border-box; }}
                body {{ margin: 0; font-family: 'Space Grotesk', 'Segoe UI', sans-serif; color: var(--ink); background: radial-gradient(circle at top left, #fff8ea 0%, #f6ecda 35%, #ecdfc7 100%); }}
                .wrap {{ width: min(1380px, calc(100vw - 28px)); margin: 20px auto 48px; }}
                .hero {{ display: grid; grid-template-columns: 1.3fr .7fr; gap: 18px; margin-bottom: 18px; }}
                .panel {{ background: var(--panel); backdrop-filter: blur(18px); border: 1px solid var(--line); border-radius: 28px; box-shadow: 0 24px 80px rgba(64, 44, 7, 0.08); }}
                .hero-main {{ padding: 28px; background: linear-gradient(135deg, rgba(29,88,68,.95), rgba(173,116,0,.92)); color: white; }}
                .hero-main h1 {{ margin: 0 0 8px; font-size: 38px; line-height: 1; }}
                .hero-main p {{ margin: 0; max-width: 720px; color: rgba(255,255,255,.82); line-height: 1.5; }}
                .hero-side {{ padding: 22px; display: grid; gap: 12px; align-content: start; }}
                .hero-side a {{ color: var(--green); text-decoration: none; font-weight: 700; }}
                .grid {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; }}
                .stack {{ display: grid; gap: 18px; }}
                .section {{ padding: 22px; }}
                .section h2 {{ margin: 0 0 14px; font-size: 22px; }}
                .cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
                .card {{ padding: 18px; border-radius: 20px; background: rgba(255,255,255,.7); border: 1px solid var(--line); }}
                .card small {{ display: block; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }}
                .card strong {{ font-size: 28px; }}
                .balance-grid {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }}
                .balance {{ padding: 16px; border-radius: 18px; background: rgba(255,255,255,.72); border: 1px solid var(--line); display: grid; gap: 6px; }}
                .balance span {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }}
                .balance strong {{ font-size: 20px; }}
                .notice {{ margin-bottom: 18px; padding: 14px 16px; border-radius: 18px; font-weight: 500; }}
                .notice.info {{ background: var(--info-bg); color: var(--green); border: 1px solid #bfe3d4; }}
                .notice.error {{ background: var(--danger-bg); color: var(--danger); border: 1px solid #eab7ad; }}
                form {{ display: grid; gap: 14px; }}
                .fields-2 {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
                .fields-3 {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
                label {{ display: grid; gap: 7px; color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }}
                input, select, textarea {{ width: 100%; border-radius: 14px; border: 1px solid var(--line); padding: 13px 14px; font: inherit; color: var(--ink); background: rgba(255,255,255,.95); }}
                textarea {{ min-height: 116px; resize: vertical; }}
                button {{ border: 0; border-radius: 16px; padding: 14px 18px; font: inherit; font-weight: 700; cursor: pointer; color: white; background: linear-gradient(135deg, var(--green), var(--gold)); }}
                table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
                th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid var(--line); }}
                th {{ color: var(--muted); text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }}
                pre {{ white-space: pre-wrap; word-break: break-word; font-family: 'IBM Plex Mono', monospace; background: #181814; color: #f7f4ea; padding: 16px; border-radius: 16px; overflow: auto; }}
                .console-output details summary {{ cursor: pointer; color: var(--muted); margin-bottom: 10px; }}
                .hint {{ color: var(--muted); font-size: 14px; line-height: 1.5; }}
                .user-chip {{ display: inline-flex; gap: 8px; align-items: center; padding: 8px 12px; border-radius: 999px; background: rgba(255,255,255,.14); font-size: 13px; margin-top: 14px; }}
                .actions-inline {{ display: flex; gap: 10px; flex-wrap: wrap; }}
                .actions-inline form {{ display: inline-block; }}
                .ghost-btn {{ background: white; color: var(--green); border: 1px solid var(--line); }}
                .payment-stack {{ display: grid; gap: 10px; }}
                .payment-row {{ display: grid; grid-template-columns: 0.9fr 1.1fr 1fr 1fr; gap: 10px; }}
                @media (max-width: 1100px) {{ .hero, .grid {{ grid-template-columns: 1fr; }} .balance-grid, .cards, .fields-3 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
                @media (max-width: 720px) {{ .wrap {{ width: calc(100vw - 18px); }} .balance-grid, .cards, .fields-2, .fields-3, .payment-row {{ grid-template-columns: 1fr; }} .hero-main h1 {{ font-size: 30px; }} }}
            </style>
        </head>
        <body>
            <div class='wrap'>
                <div class='hero'>
                    <section class='panel hero-main'>
                        <h1>Caixa SaaS</h1>
                        <p>Operação híbrida para o mesmo backend: relatórios, estoque FIFO, caixa dos 5 saldos, operação rápida multi-moeda e console web usando o mesmo motor conversacional do WhatsApp.</p>
                        <div class='user-chip'>{user_name} · {user_role} · {user_phone}</div>
                    </section>
                    <aside class='panel hero-side'>
                        <div><strong>Data</strong><br>{escape(day['date'])}</div>
                        <div><strong>Estoque</strong><br>{escape(str(inventory.get('available_grams', '0')))} g</div>
                        <div><strong>Links</strong><br><a href='/reports/inventory-status' target='_blank'>JSON estoque</a></div>
                        <div class='actions-inline'>
                            <form method='post' action='/saas/logout'><button class='ghost-btn' type='submit'>Sair</button></form>
                        </div>
                    </aside>
                </div>
                {bootstrap_notice}
                {notice_html}
                <section class='panel section'>
                    <h2>Leitura Rápida</h2>
                    <div class='cards'>
                        <div class='card'><small>Operações Hoje</small><strong>{escape(str(summary.get('total_operacoes', 0)))}</strong></div>
                        <div class='card'><small>Total USD Hoje</small><strong>USD {escape(str(summary.get('total_usd', '0')))}</strong></div>
                        <div class='card'><small>Custo Médio Aberto</small><strong>USD {escape(str(inventory.get('avg_cost_usd_per_gram', '0.00')))}</strong></div>
                    </div>
                </section>
                <section class='panel section'>
                    <h2>5 Caixas</h2>
                    <div class='balance-grid'>{balances_html}</div>
                </section>
                <div class='grid'>
                    <div class='stack'>
                        <section class='panel section'>
                            <h2>Operação Rápida Web</h2>
                            <p class='hint'>Entrada direta para compra ou venda com até 4 pagamentos por moeda. Se o câmbio ficar vazio, o sistema tenta usar o último valor conhecido.</p>
                            <form method='post' action='/saas/operations/quick'>
                                <div class='fields-3'>
                                    <label>Operador
                                        <input name='operador_id' value='{escape(values['operador_id'])}' required />
                                    </label>
                                    <label>Tipo
                                        <select name='tipo_operacao'>
                                            <option value='compra' {'selected' if values['tipo_operacao']=='compra' else ''}>Compra</option>
                                            <option value='venda' {'selected' if values['tipo_operacao']=='venda' else ''}>Venda</option>
                                        </select>
                                    </label>
                                    <label>Origem
                                        <select name='origem'>
                                            <option value='balcao' {'selected' if values['origem']=='balcao' else ''}>Balcão</option>
                                            <option value='fora' {'selected' if values['origem']=='fora' else ''}>Fora</option>
                                        </select>
                                    </label>
                                </div>
                                <div class='fields-3'>
                                    <label>Teor %
                                        <input name='teor' value='{escape(values['teor'])}' required />
                                    </label>
                                    <label>Peso g
                                        <input name='peso' value='{escape(values['peso'])}' required />
                                    </label>
                                    <label>Preço USD/g
                                        <input name='preco_usd' value='{escape(values['preco_usd'])}' required />
                                    </label>
                                </div>
                                <div class='fields-2'>
                                    <label>Fechamento g
                                        <input name='fechamento_gramas' value='{escape(values['fechamento_gramas'])}' placeholder='vazio = total' />
                                    </label>
                                    <label>Fechamento Tipo
                                        <select name='fechamento_tipo'>
                                            <option value='total' {'selected' if values['fechamento_tipo']=='total' else ''}>Total</option>
                                            <option value='parcial' {'selected' if values['fechamento_tipo']=='parcial' else ''}>Parcial</option>
                                        </select>
                                    </label>
                                </div>
                                <div class='fields-2'>
                                    <label>Pessoa
                                        <input name='pessoa' value='{escape(values['pessoa'])}' required />
                                    </label>
                                    <label>Total Pago USD legado
                                        <input name='total_pago_usd' value='{escape(values['total_pago_usd'])}' placeholder='use só se não preencher linhas de pagamento' />
                                    </label>
                                </div>
                                <div class='payment-stack'>
                                    {payment_rows_html}
                                </div>
                                <label>Observações
                                    <textarea name='observacoes' placeholder='Detalhes adicionais'>{escape(values['observacoes'])}</textarea>
                                </label>
                                <label><input type='checkbox' name='risk_override' value='1' style='width:auto;margin-right:8px;' /> Autorizar risco se o operador informado for admin</label>
                                <button type='submit'>Salvar operação web</button>
                            </form>
                        </section>
                        <section class='panel section'>
                            <h2>Operações Recentes</h2>
                            <table>
                                <thead><tr><th>ID</th><th>Tipo</th><th>Pessoa</th><th>Peso</th><th>Total</th></tr></thead>
                                <tbody>{recent_html}</tbody>
                            </table>
                        </section>
                    </div>
                    <div class='stack'>
                        <section class='panel section'>
                            <h2>Console do Operador</h2>
                            <p class='hint'>Aqui você usa exatamente o mesmo motor do WhatsApp, mas dentro do navegador. Bom para onboarding, testes e operação híbrida.</p>
                            <form method='post' action='/saas/console'>
                                <label>Remetente / operador
                                    <input name='console_remetente' value='{escape(values['console_remetente'])}' required />
                                </label>
                                <label>Mensagem
                                    <textarea name='console_mensagem' required>{escape(values['console_mensagem'])}</textarea>
                                </label>
                                <button type='submit'>Executar no motor do WhatsApp</button>
                            </form>
                            {assistant_html}
                        </section>
                        <section class='panel section'>
                            <h2>Segurança do Acesso</h2>
                            <p class='hint'>O painel agora usa sessão HTTP com operador autenticado. Troque o PIN sempre que fizer bootstrap ou rotação de credencial.</p>
                            <form method='post' action='/saas/profile/pin'>
                                <div class='fields-3'>
                                    <label>PIN atual
                                        <input type='password' name='current_pin' inputmode='numeric' required />
                                    </label>
                                    <label>Novo PIN
                                        <input type='password' name='new_pin' inputmode='numeric' required />
                                    </label>
                                    <label>Confirmar novo PIN
                                        <input type='password' name='confirm_pin' inputmode='numeric' required />
                                    </label>
                                </div>
                                <button type='submit'>Trocar PIN web</button>
                            </form>
                        </section>
                        <section class='panel section'>
                            <h2>Estoque FIFO Aberto</h2>
                            <div class='cards'>
                                <div class='card'><small>Disponível</small><strong>{escape(str(inventory.get('available_grams', '0')))} g</strong></div>
                                <div class='card'><small>Custo Aberto</small><strong>USD {escape(str(inventory.get('inventory_cost_usd', '0.00')))}</strong></div>
                                <div class='card'><small>Lotes</small><strong>{escape(str(len(lot_rows)))}</strong></div>
                            </div>
                            <table>
                                <thead><tr><th>Lote</th><th>Saldo</th><th>Custo</th></tr></thead>
                                <tbody>{lots_html}</tbody>
                            </table>
                        </section>
                    </div>
                </div>
            </div>
        </body>
    </html>
    """


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


def _parse_operation_reference(raw: str) -> Tuple[str, Optional[int]]:
    text = raw.strip().lower()
    if text.startswith("gt-"):
        return "gold", _parse_operation_id(text)
    if text.startswith("t-") or text.startswith("op-"):
        return "transacao", _parse_operation_id(text)
    return "transacao", _parse_operation_id(text)


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

        op_kind, op_id = _parse_operation_reference(op_token)
        if op_id is None:
            return {"mensagem": "ID inválido. Exemplo: editar 123 preco 110", "dados": {"acao": "editar_operacao"}}

        if op_kind == "gold":
            return {
                "mensagem": "Operações guiadas GT não suportam edição direta. Use cancelar GT-<id> e refaça a operação.",
                "dados": {"acao": "editar_operacao", "id": op_id, "kind": "gold", "permitido": False},
            }

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
        op_kind, op_id = _parse_operation_reference(cancel_match.group(2))
        if op_id is None:
            return {"mensagem": "ID inválido. Exemplo: cancelar 123", "dados": {"acao": "cancelar_operacao"}}

        if op_kind == "gold":
            transacao_resp = (
                db.client.table("gold_transactions")
                .select("*")
                .eq("id", op_id)
                .limit(1)
                .execute()
            )
            rows = cast(List[Dict[str, Any]], transacao_resp.data or [])
            if not rows:
                return {"mensagem": f"Operação GT-{op_id} não encontrada.", "dados": {"acao": "cancelar_operacao", "kind": "gold"}}

            row = rows[0]
            is_admin = str(usuario.get("tipo_usuario", "")).lower() == "admin"
            if not is_admin and str(row.get("operador_id", "")) != remetente:
                return {
                    "mensagem": "Você não tem permissão para cancelar esta operação guiada.",
                    "dados": {"acao": "cancelar_operacao", "permitido": False, "kind": "gold"},
                }

            ok = db.cancel_gold_transaction(op_id, cancelled_by=remetente)
            if not ok:
                return {"mensagem": "Não consegui cancelar a operação guiada agora.", "dados": {"acao": "cancelar_operacao", "id": op_id, "kind": "gold"}}
            return {
                "mensagem": f"✅ Operação GT-{op_id} cancelada com sucesso.",
                "dados": {"acao": "cancelar_operacao", "id": op_id, "status": "cancelada", "kind": "gold"},
            }

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
        "   Ex: compra | venda | compra ouro 2g\n\n"
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
    """Build safe-to-display caixa status with 5 independent cashes (5 caixas).
    
    NEW STRUCTURE (as of refactor):
    - Caixa XAU: gramas de ouro (quantidade)
    - Caixa EUR: saldo em euros (sem conversão)
    - Caixa USD: saldo em dólares (sem conversão)
    - Caixa SRD: saldo em surinamês (sem conversão)
    - Caixa BRL: saldo em reais (sem conversão)
    
    Each caixa is independent. No USD reference layer.
    """
    day = _build_day_range(None)
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    saldo = db.get_saldo_caixa()
    
    ops_hoje = int(summary.get("total_operacoes", 0) or 0)
    
    # New structure: each currency directly in saldo
    saldo_xau = Decimal(str(saldo.get("XAU", "0")))
    saldo_eur = Decimal(str(saldo.get("EUR", "0")))
    saldo_usd = Decimal(str(saldo.get("USD", "0")))
    saldo_srd = Decimal(str(saldo.get("SRD", "0")))
    saldo_brl = Decimal(str(saldo.get("BRL", "0")))
    
    def situacao_txt(val: Decimal) -> str:
        return "entrou mais 💰" if val > 0 else ("nada" if val == 0 else "saiu mais 📉")
    
    if requested_currency:
        moeda = requested_currency.upper()
        
        if moeda == "XAU":
            resposta = (
                f"💰 CAIXA OURO (XAU)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo em estoque: {saldo_xau:,.3f} g\n"
                f"Status: {situacao_txt(saldo_xau)}\n"
                "════════════════════════════════"
            )
        elif moeda == "EUR":
            resposta = (
                f"🇪🇺 CAIXA EURO (EUR)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: EUR {saldo_eur:,.2f}\n"
                f"Status: {situacao_txt(saldo_eur)}\n"
                "════════════════════════════════"
            )
        elif moeda == "USD":
            resposta = (
                f"🇺🇸 CAIXA DÓLAR (USD)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: $ {saldo_usd:,.2f}\n"
                f"Status: {situacao_txt(saldo_usd)}\n"
                "════════════════════════════════"
            )
        elif moeda == "SRD":
            resposta = (
                f"🇸🇷 CAIXA SURINAMÊS (SRD)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: SRD {saldo_srd:,.2f}\n"
                f"Status: {situacao_txt(saldo_srd)}\n"
                "════════════════════════════════"
            )
        elif moeda == "BRL":
            resposta = (
                f"🇧🇷 CAIXA REAL (BRL)\n"
                f"Data: {day['date']}\n"
                f"Operações hoje: {ops_hoje}\n"
                "════════════════════════════════\n"
                f"Saldo: R$ {saldo_brl:,.2f}\n"
                f"Status: {situacao_txt(saldo_brl)}\n"
                "════════════════════════════════"
            )
        else:
            resposta = f"Moeda {moeda} não reconhecida. Digite: xau, eur, usd, srd ou brl"
    else:
        # Default: show all 5 caixas
        resposta = (
            f"📊 SALDOS DE TODOS OS 5 CAIXAS\n"
            f"Data: {day['date']}\n"
            f"Operações hoje: {ops_hoje}\n"
            "════════════════════════════════════════════\n"
            f"1) 💰 OURO (XAU):      {saldo_xau:>10,.3f} g\n"
            f"   Status: {situacao_txt(saldo_xau)}\n"
            "\n"
            f"2) 🇪🇺 EURO (EUR):      EUR {saldo_eur:>10,.2f}\n"
            f"   Status: {situacao_txt(saldo_eur)}\n"
            "\n"
            f"3) 🇺🇸 DÓLAR (USD):     $ {saldo_usd:>12,.2f}\n"
            f"   Status: {situacao_txt(saldo_usd)}\n"
            "\n"
            f"4) 🇸🇷 SURINAMÊS (SRD): SRD {saldo_srd:>10,.2f}\n"
            f"   Status: {situacao_txt(saldo_srd)}\n"
            "\n"
            f"5) 🇧🇷 REAL (BRL):      R$ {saldo_brl:>11,.2f}\n"
            f"   Status: {situacao_txt(saldo_brl)}\n"
            "════════════════════════════════════════════\n"
            "Como ler:\n"
            "- 💰 entrou mais: recebemos mais desse caixa\n"
            "- 📉 saiu mais: gastamos mais desse caixa\n"
            "- nada: equilibrado\n"
            "\nPara detalhar um caixa, responda:\n"
            "1 (ouro) | 2 (euro) | 3 (dólar) | 4 (surinamês) | 5 (real)"
        )
    
    return {
        "mensagem": resposta,
        "dados": {
            "intencao": "consultar_relatorio",
            "date": day["date"],
            "saldo_xau": str(saldo_xau),
            "saldo_eur": str(saldo_eur),
            "saldo_usd": str(saldo_usd),
            "saldo_srd": str(saldo_srd),
            "saldo_brl": str(saldo_brl),
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
        response = _build_caixa_response(db)
        _save_session(db, remetente, "await_caixa_detalhe", {"source": "menu_caixa"})
        return response

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
        session: Dict[str, Any] = {
            "estado": db_session.get("estado", ""),
            "contexto": cast(Dict[str, Any], db_session["contexto"]),
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
        "mensagem": (
            f"Iniciando registro de {tipo}.\n"
            "Local da operação:\n"
            "1) balcão\n"
            "2) fora"
            f"{_navigation_hint()}"
        ),
        "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
    }


def _format_resumo(contexto: Dict[str, Any]) -> str:
    """Format operation summary WITHOUT USD as single reference.
    
    New structure: show pagamentos in each currency independently.
    Each caixa is updated with its own value, no conversion.
    """
    pagamentos = contexto.get("pagamentos", [])
    linhas_pagamento: List[str] = []
    for p in pagamentos:
        moeda = p.get('moeda', 'USD')
        valor = p.get('valor_moeda', '0')
        linhas_pagamento.append(f"- {moeda}: {valor}")
    
    linhas_pagamento_texto = "\n".join(linhas_pagamento) if linhas_pagamento else "- Sem pagamentos informados"

    tipo_operacao = str(contexto.get("tipo_operacao") or "")
    pessoa_label = "Vendedor" if tipo_operacao == "compra" else "Comprador"
    lucro_real_usd = contexto.get("lucro_real_usd")
    custo_fifo_usd = contexto.get("custo_fifo_usd")
    lucro_ref_usd = contexto.get("lucro_ref_usd")
    preco_compra_ref_usd = contexto.get("preco_compra_ref_usd")
    lucro_linha = ""
    observacoes_idx = "10"

    if tipo_operacao == "venda" and lucro_real_usd is not None:
        lucro_linha = f"10) Lucro real (FIFO): USD {lucro_real_usd} (custo: USD {custo_fifo_usd})\n"
        observacoes_idx = "11"
    elif tipo_operacao == "venda" and lucro_ref_usd is not None:
        lucro_linha = f"10) Lucro ref.: USD {lucro_ref_usd} (custo-base: USD {preco_compra_ref_usd}/g)\n"
        observacoes_idx = "11"

    if tipo_operacao == "compra":
        return (
            "📋 RESUMO FINAL - COMPRA\n"
            f"1) Tipo: {contexto.get('tipo_operacao')}\n"
            f"2) Origem: {contexto.get('origem')}\n"
            f"3) Teor: {contexto.get('teor')}%\n"
            f"4) Peso: {contexto.get('peso')}g\n"
            f"5) Preço base: {contexto.get('preco_moeda')} {contexto.get('preco_moeda')} / g\n"
            f"6) {pessoa_label}: {contexto.get('pessoa')}\n"
            f"7) Forma de pagamento: {contexto.get('forma_pagamento')}\n"
            f"8) Pagamentos por moeda:\n{linhas_pagamento_texto}\n"
            f"9) Observações: {contexto.get('observacoes') or '(nenhuma)'}\n"
            "════════════════════════════════\n"
            "Se estiver correto, responda: sim\n"
            "Para cancelar, responda: não"
        )

    return (
        "📋 RESUMO FINAL - VENDA\n"
        f"1) Tipo: {contexto.get('tipo_operacao')}\n"
        f"2) Origem: {contexto.get('origem')}\n"
        f"3) Teor: {contexto.get('teor')}%\n"
        f"4) Peso: {contexto.get('peso')}g\n"
        f"5) Fechamento: {contexto.get('fechamento_gramas')}g ({contexto.get('fechamento_tipo')})\n"
        f"6) Preço base: {contexto.get('preco_moeda')} / g\n"
        f"7) {pessoa_label}: {contexto.get('pessoa')}\n"
        f"8) Forma de pagamento: {contexto.get('forma_pagamento')}\n"
        f"9) Pagamentos por moeda:\n{linhas_pagamento_texto}\n"
        f"{lucro_linha}"
        f"{observacoes_idx}) Observações: {contexto.get('observacoes') or '(nenhuma)'}\n"
        "════════════════════════════════\n"
        "Se estiver correto, responda: sim\n"
        "Para cancelar, responda: não"
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
            preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
            if proxima_moeda != preco_moeda:
                _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
                cambio_prompt = _build_pair_cambio_prompt(preco_moeda, proxima_moeda)
                return {
                    "mensagem": (
                        "Pagamento registrado.\n"
                        f"Câmbio {preco_moeda}/{proxima_moeda}: {cambio_prompt}"
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
        proxima_moeda = str(moedas[idx]).upper()
        preco_moeda_adv = str(contexto.get("preco_moeda", "USD")).upper()
        if proxima_moeda != preco_moeda_adv:
            _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
            cambio_prompt = _build_pair_cambio_prompt(preco_moeda_adv, proxima_moeda)
            return {
                "mensagem": (
                    f"Pago até agora: {money(total_pago_parcial)} USD. Restante: {restante} USD.\n"
                    f"Câmbio {preco_moeda_adv}/{proxima_moeda}: {cambio_prompt}"
                ),
                "dados": {"etapa": "await_cambio_moeda_pre_valor"},
            }

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
    fx_notice = "\nObs: referência em USD estimada (sem câmbio explícito informado)." if contexto.get("fx_auto_assumido") else ""

    # Determine display currency: use preco_moeda when all payments are in that currency.
    preco_moeda_disp = str(contexto.get("preco_moeda", "USD")).upper()
    total_moeda_disp = Decimal(str(contexto.get("total_moeda", "0")))
    all_in_preco_moeda = (
        preco_moeda_disp != "USD"
        and total_moeda_disp > 0
        and all(str(p.get("moeda", "")).upper() == preco_moeda_disp for p in pagamentos)
    )
    if all_in_preco_moeda:
        display_pago = sum((Decimal(str(p["valor_moeda"])) for p in pagamentos), Decimal("0"))
        display_diferenca = total_moeda_disp - display_pago
        display_moeda = preco_moeda_disp
    else:
        display_pago = total_pago
        display_diferenca = total_operacao - total_pago
        display_moeda = "USD"

    if tipo_operacao_ctx == "compra":
        peso_ctx = Decimal(str(contexto.get("peso", "0")))
        contexto["fechamento_gramas"] = str(money(peso_ctx))
        contexto["fechamento_tipo"] = "total"
        _save_session(db, remetente, "await_pessoa", contexto)
        return {
            "mensagem": (
                f"Total pago: {money(display_pago)} {display_moeda}.\n"
                f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                f"Nome do vendedor (de quem você comprou)?{fx_notice}"
            ),
            "dados": {"etapa": "await_pessoa"},
        }

    peso_ctx = Decimal(str(contexto.get("peso", "0")))
    if money(display_diferenca) == Decimal("0.00") and peso_ctx > 0:
        contexto["fechamento_gramas"] = str(money(peso_ctx))
        contexto["fechamento_tipo"] = "total"
        _save_session(db, remetente, "await_pessoa", contexto)
        return {
            "mensagem": (
                f"Total pago: {money(display_pago)} {display_moeda}.\n"
                f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
                f"Venda fechada integralmente.\n"
                f"Nome do comprador?{fx_notice}"
            ),
            "dados": {"etapa": "await_pessoa"},
        }

    _save_session(db, remetente, "await_fechamento_gramas", contexto)
    return {
        "mensagem": (
            f"Total pago: {money(display_pago)} {display_moeda}.\n"
            f"Diferença atual: {money(display_diferenca)} {display_moeda}.\n"
            f"Informe as gramas fechadas.{fx_notice}"
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
        tipo_escolhido = {"1": "compra", "2": "venda"}.get(text, text)
        if tipo_escolhido not in {"compra", "venda"}:
            return {
                "mensagem": (
                    "Tipo inválido. Escolha uma opção:\n"
                    "1) compra\n"
                    "2) venda"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": "await_menu_tipo_operacao"},
            }

        contexto.update(
            {
                "tipo_operacao": tipo_escolhido,
                "pagamentos": [],
                "moedas": [],
                "moeda_index": 0,
                "moeda_atual": None,
            }
        )
        _save_session(db, remetente, "await_origem", contexto)
        return {
            "mensagem": (
                f"Operação: {tipo_escolhido}.\n"
                "Local da operação:\n"
                "1) balcão\n"
                "2) fora"
                f"{_navigation_hint()}"
            ),
            "dados": {"intencao": "fluxo_guiado", "etapa": "await_origem"},
        }

    if estado == "await_origem":
        origem = _parse_origem_choice(mensagem)
        if origem is None:
            return {
                "mensagem": (
                    "Origem inválida. Escolha uma opção:\n"
                    "1) balcão\n"
                    "2) fora"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["origem"] = origem
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
                "1) USD\n"
                "2) EUR\n"
                "3) SRD\n"
                "4) BRL\n"
                "Você também pode digitar: dólar, euro, srd ou real."
                f"{_navigation_hint()}"
            ),
            "dados": {"etapa": "await_preco_moeda"},
        }

    if estado == "await_preco_moeda":
        moeda_preco = _parse_single_currency_choice(mensagem)
        if moeda_preco not in _MOEDAS_SUPORTADAS:
            return {
                "mensagem": (
                    "Moeda inválida. Escolha uma opção:\n"
                    "1) USD\n"
                    "2) EUR\n"
                    "3) SRD\n"
                    "4) BRL\n"
                    "Você também pode digitar: dólar, euro, srd ou real."
                    f"{_navigation_hint()}"
                ),
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
                    "(o câmbio será pedido na etapa de pagamento, se necessário)"
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

        primeira_moeda = str(moedas[0]).upper()
        if primeira_moeda != preco_moeda:
            _save_session(db, remetente, "await_cambio_moeda_pre_valor", contexto)
            cambio_prompt = _build_pair_cambio_prompt(preco_moeda, primeira_moeda)
            return {
                "mensagem": (
                    f"{total_txt}\n"
                    f"Câmbio {preco_moeda}/{primeira_moeda}: {cambio_prompt}"
                ),
                "dados": {"etapa": "await_cambio_moeda_pre_valor"},
            }

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
        preco_moeda_cp = str(contexto.get("preco_moeda", "USD")).upper()

        if moeda_atual == "USD" and preco_moeda_cp == "USD":
            # USD payment in USD operation: trivially no exchange needed.
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "Quanto será pago em USD?", "dados": {"etapa": "await_valor_moeda"}}

        if moeda_atual == "USD" and preco_moeda_cp != "USD":
            # Non-USD base, USD payment: prompt was "1 B = R USD".
            cambio_normalizado = _normalize_cambio_para_usd(preco_moeda_cp, cambio)
            _try_set_total_usd_from_base_rate(contexto, cambio_normalizado)
            total_usd_novo = Decimal(str(contexto.get("total_usd", "0")))
            total_moeda_cp = Decimal(str(contexto.get("total_moeda", "0")))
            lines = [f"Câmbio: 1 {preco_moeda_cp} = {money(cambio)} USD."]
            if total_usd_novo > 0:
                lines.append(f"Total equivalente: ~{money(total_usd_novo)} USD.")
            elif total_moeda_cp > 0:
                lines.append(f"Total da operação: {money(total_moeda_cp)} {preco_moeda_cp}.")
            lines.append("Quanto será pago em USD?")
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "\n".join(lines), "dados": {"etapa": "await_valor_moeda"}}

        if preco_moeda_cp != "USD" and moeda_atual != "USD":
            # Both non-USD (e.g. EUR base + SRD pay): prompt was the direct B/P pair.
            pay_per_usd, pair_p_per_b, c_base = _pair_rate_to_payment_per_usd(
                preco_moeda_cp, moeda_atual, cambio, db
            )
            total_moeda_base = Decimal(str(contexto.get("total_moeda", "0")))
            total_in_payment = money(total_moeda_base * pair_p_per_b) if total_moeda_base > 0 else None
            if _MOEDA_STRENGTH.get(preco_moeda_cp, 5) <= _MOEDA_STRENGTH.get(moeda_atual, 5):
                rate_echo = f"1 {preco_moeda_cp} = {money(pair_p_per_b)} {moeda_atual}"
            else:
                inv = fx_rate(Decimal("1") / pair_p_per_b) if pair_p_per_b > 0 else Decimal("0")
                rate_echo = f"1 {moeda_atual} = {money(inv)} {preco_moeda_cp}"
            lines = [f"Câmbio: {rate_echo}."]
            if total_in_payment and total_in_payment > 0:
                lines.append(f"Total estimado: {money(total_in_payment)} {moeda_atual}.")
            lines.append(f"Quanto será pago em {moeda_atual}?")
            if pay_per_usd is not None:
                contexto["cambio_moeda_atual_pre"] = str(pay_per_usd)
                contexto["fx_auto_assumido"] = True
            else:
                contexto.pop("cambio_moeda_atual_pre", None)
                contexto["fx_auto_assumido"] = True
            if c_base is not None:
                _try_set_total_usd_from_base_rate(contexto, c_base)
            _save_session(db, remetente, "await_valor_moeda", contexto)
            return {"mensagem": "\n".join(lines), "dados": {"etapa": "await_valor_moeda"}}

        # USD base, non-USD payment: normalize to P_per_USD.
        cambio_normalizado = _normalize_cambio_para_usd(moeda_atual, cambio)
        contexto["cambio_moeda_atual_pre"] = str(cambio_normalizado)
        _save_session(db, remetente, "await_valor_moeda", contexto)
        return {
            "mensagem": f"Câmbio registrado. Quanto será pago em {moeda_atual}?",
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

        # Se for a mesma moeda-base da precificação, tenta usar último câmbio conhecido
        # para evitar pedir câmbio manual em operações diretas nessa moeda.
        preco_moeda = str(contexto.get("preco_moeda", "USD")).upper()
        if preco_moeda != "USD" and str(moeda_atual).upper() == preco_moeda:
            cambio_auto = db.get_last_cambio_para_usd(preco_moeda)
            cambio_auto_dec = Decimal(str(cambio_auto)) if (cambio_auto and cambio_auto > 0) else Decimal("1")
            # Paying in the same currency as the price: no FX assumption — cambio 1:1 is exact,
            # not an estimate. Only flag fx_auto_assumido for cross-currency fallbacks.
            contexto["fx_auto_assumido"] = False
            valor_usd_auto = money(valor_moeda / cambio_auto_dec)
            pagamentos[-1]["cambio_para_usd"] = str(cambio_auto_dec)
            pagamentos[-1]["valor_usd"] = str(valor_usd_auto)
            contexto["pagamentos"] = pagamentos
            _try_set_total_usd_from_base_rate(contexto, cambio_auto_dec)
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
        fechamento_tipo = _parse_fechamento_tipo_choice(mensagem)
        if fechamento_tipo is None:
            return {
                "mensagem": (
                    "Escolha o tipo de fechamento:\n"
                    "1) total\n"
                    "2) parcial"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["fechamento_tipo"] = fechamento_tipo
        _save_session(db, remetente, "await_pessoa", contexto)
        tipo_op_ft = str(contexto.get("tipo_operacao", "compra"))
        pergunta_pessoa = "Nome do vendedor (de quem você comprou)?" if tipo_op_ft == "compra" else "Nome do comprador?"
        return {"mensagem": pergunta_pessoa, "dados": {"etapa": "await_pessoa"}}

    if estado == "await_pessoa":
        if len(mensagem.strip()) < 2:
            return {"mensagem": "Informe um nome válido.", "dados": {"etapa": estado}}
        contexto["pessoa"] = mensagem.strip()
        _save_session(db, remetente, "await_forma_pagamento", contexto)
        return {
            "mensagem": (
                "Como foi o pagamento?\n"
                "1) dinheiro\n"
                "2) transferência\n"
                "3) cheque\n"
                "4) misto"
                f"{_navigation_hint()}"
            ),
            "dados": {"etapa": "await_forma_pagamento"},
        }

    if estado == "await_forma_pagamento":
        forma = _parse_forma_pagamento_choice(mensagem)
        if forma is None:
            return {
                "mensagem": (
                    "Forma inválida. Escolha uma opção:\n"
                    "1) dinheiro\n"
                    "2) transferência\n"
                    "3) cheque\n"
                    "4) misto"
                    f"{_navigation_hint()}"
                ),
                "dados": {"etapa": estado},
            }
        contexto["forma_pagamento"] = forma
        pagamentos = list(contexto.get("pagamentos", []))
        for pagamento in pagamentos:
            pagamento["forma_pagamento"] = forma
        contexto["pagamentos"] = pagamentos
        _save_session(db, remetente, "await_observacoes", contexto)
        return {"mensagem": "Quer adicionar observações? (ou digite 'nenhuma')", "dados": {"etapa": "await_observacoes"}}

    if estado == "await_observacoes":
        contexto["observacoes"] = "" if _normalize_text(mensagem) in {"nenhuma", "nao", "não"} else mensagem.strip()
        _attach_sale_profit_reference(db, contexto)
        resumo = _format_resumo(contexto)
        _save_session(db, remetente, "await_confirmacao", contexto)
        return {"mensagem": resumo, "dados": {"etapa": "await_confirmacao", "preview": contexto}}

    if estado == "await_confirmacao":
        text_confirm = _normalize_text(mensagem)
        if contexto.get("risk_override_pending") and text_confirm in {"autorizar risco", "autorizar", "override"}:
            contexto["risk_override_approved"] = True
            contexto.pop("risk_override_pending", None)
            _save_session(db, remetente, "await_confirmacao", contexto)
            confirm = True
        else:
            confirm = _extract_confirmacao(mensagem)
        if confirm is None:
            if contexto.get("risk_override_pending"):
                return {
                    "mensagem": "Responda: autorizar risco, não ou voltar.",
                    "dados": {"etapa": estado, "risk_override_pending": True},
                }
            return {"mensagem": "Digite apenas: sim ou não.", "dados": {"etapa": estado}}

        if not confirm:
            _clear_session(db, remetente)
            return {"mensagem": "Operação cancelada com sucesso.", "dados": {"intencao": "fluxo_guiado_cancelado"}}

        peso = Decimal(str(contexto.get("peso")))
        preco = Decimal(str(contexto.get("preco_usd")))
        total = money(peso * preco)
        total_pago = Decimal(str(contexto.get("total_pago_usd", "0")))
        diferenca = money(total - total_pago)
        risco_diferenca = abs(diferenca) >= _RISK_DIFF_LIMIT_USD
        tipo_operacao_confirm = str(contexto.get("tipo_operacao", "compra"))
        if tipo_operacao_confirm == "venda":
            _attach_sale_profit_reference(db, contexto)

        pagamentos = list(contexto.get("pagamentos", []))
        projected = _project_caixa_balances(db.get_saldo_caixa(), tipo_operacao_confirm, peso, pagamentos)
        negative_balances = _find_negative_caixa_balances(projected)
        fifo_shortfall = Decimal(str(contexto.get("fifo_shortfall_grams", "0")))
        risk_lines: List[str] = []
        if negative_balances:
            risk_lines.append("Saldos projetados negativos:")
            risk_lines.extend(_format_negative_caixa_lines(negative_balances))
        if fifo_shortfall > 0:
            risk_lines.append(f"- Estoque FIFO insuficiente: faltam {fifo_shortfall} g")

        if risk_lines and not contexto.get("risk_override_approved"):
            usuario_confirm = db.get_usuario_by_telefone(remetente) or {}
            is_admin_confirm = str(usuario_confirm.get("tipo_usuario", "")).lower() == "admin"
            contexto["risk_override_pending"] = True
            _save_session(db, remetente, "await_confirmacao", contexto)
            if is_admin_confirm:
                return {
                    "mensagem": "⛔ Bloqueio de risco.\n" + "\n".join(risk_lines) + "\nResponda: autorizar risco, não ou voltar.",
                    "dados": {"etapa": estado, "risk_override_pending": True, "risk_blocked": True},
                }
            return {
                "mensagem": "⛔ Bloqueio de risco.\n" + "\n".join(risk_lines) + "\nSomente admin pode autorizar override. Use voltar ou cancelar.",
                "dados": {"etapa": estado, "risk_blocked": True},
            }
        return _persist_gold_operation_from_context(db, remetente, contexto, post_save_session=True)

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
        moeda = _parse_single_currency_choice(mensagem)
        _MOEDAS_VALIDAS = {"USD", "EUR", "SRD", "BRL"}
        if moeda not in _MOEDAS_VALIDAS:
            return {
                "mensagem": (
                    "Moeda inválida. Escolha uma opção:\n"
                    "1) USD\n"
                    "2) EUR\n"
                    "3) SRD\n"
                    "4) BRL\n"
                    "Você também pode digitar: dólar, euro, srd ou real."
                    f"{_navigation_hint()}"
                ),
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

    if estado == "await_caixa_detalhe":
        requested_currency = _extract_caixa_currency(mensagem)
        if not requested_currency:
            return {
                "mensagem": (
                    "Escolha um caixa para detalhar:\n"
                    "1 (ouro) | 2 (euro) | 3 (dolar) | 4 (surinames) | 5 (real)"
                ),
                "dados": {"etapa": "await_caixa_detalhe"},
            }
        day = _build_day_range(None)
        _clear_session(db, remetente)
        return _build_caixa_detail_response(db, requested_currency, day["start"], day["end"], f"Hoje ({day['date']})")

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
        "pagamentos": [
            {
                "moeda": moeda,
                "valor_moeda": str(valor_moeda),
                "cambio_para_usd": str(cambio),
                "valor_usd": str(total_usd),
            }
        ],
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


@app.get("/reports/inventory-status")
def inventory_status_report() -> Dict[str, Any]:
    db = get_db()
    inventory = db.get_gold_inventory_status()
    if not inventory.get("lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status()

    if inventory.get("lots"):
        return {
            "available_grams": str(inventory.get("available_grams", "0")),
            "inventory_cost_usd": str(inventory.get("inventory_cost_usd", "0.00")),
            "avg_cost_usd_per_gram": str(inventory.get("avg_cost_usd_per_gram", "0.00")),
            "open_lots": len(cast(List[Dict[str, Any]], inventory.get("open_lots") or [])),
            "ledger_mode": "persisted",
            "lots": inventory.get("open_lots", []),
        }

    txs = db.get_gold_inventory_transactions()
    metrics = _compute_inventory_metrics(txs)
    return {
        "available_grams": str(metrics["available_grams"]),
        "inventory_cost_usd": str(metrics["inventory_cost_usd"]),
        "avg_cost_usd_per_gram": str(metrics["avg_cost_usd_per_gram"]),
        "open_lots": len(_build_fifo_inventory_lots(txs)),
        "ledger_mode": "reconstructed",
        "lots": _build_fifo_inventory_lots(txs),
    }


@app.get("/admin/dashboard")
def admin_dashboard(x_webhook_token: Optional[str] = Header(default=None)) -> Response:
    validate_webhook_token(x_webhook_token)
    db = get_db()
    day = _build_day_range(None)
    summary = db.get_daily_gold_summary(day["start"], day["end"])
    alerts = db.get_risk_alerts(day["start"], day["end"])
    divergences = db.get_top_divergences(day["start"], day["end"], limit=5)
    saldo = db.get_saldo_caixa()
    recent_runs = db.get_recent_multi_agent_runs(limit=5)
    inventory = db.get_gold_inventory_status()
    if not inventory.get("lots"):
        db.sync_gold_inventory_ledger()
        inventory = db.get_gold_inventory_status()

    if not inventory.get("lots"):
        fallback_metrics = _compute_inventory_metrics(db.get_gold_inventory_transactions())
        inventory = {
            "available_grams": str(fallback_metrics["available_grams"]),
            "inventory_cost_usd": str(fallback_metrics["inventory_cost_usd"]),
            "avg_cost_usd_per_gram": str(fallback_metrics["avg_cost_usd_per_gram"]),
            "open_lots": _build_fifo_inventory_lots(db.get_gold_inventory_transactions()),
        }

    saldo_items = "".join(
        f"<li><strong>{moeda}</strong>: {escape(_format_caixa_movement(moeda, Decimal(str(saldo.get(moeda, '0')))))}</li>"
        for moeda in ["XAU", "USD", "EUR", "SRD", "BRL"]
    )
    alert_items = "".join(
        f"<li>{escape(str(item.get('tipo_alerta', 'alerta')))} - {escape(str(item.get('descricao', item)))}</li>"
        for item in alerts[:10]
    ) or "<li>Sem alertas no dia.</li>"
    divergence_items = "".join(
        f"<li>ID {item.get('id')}: {escape(str(item.get('tipo_operacao', 'op')))} | diff USD {escape(str(item.get('diferenca_usd', '0')))} | operador {escape(str(item.get('operador_id', '')))}</li>"
        for item in divergences
    ) or "<li>Sem divergencias no dia.</li>"
    run_items = "".join(
        f"<li>{escape(str(item.get('criado_em', '')))} - {escape(str(item.get('objective', 'multi-agent')))}</li>"
        for item in recent_runs
    ) or "<li>Sem execucoes multiagente recentes.</li>"
    lot_items = "".join(
        f"<li>Lote tx {escape(str(item.get('source_transaction_id', '')))}: {escape(str(item.get('remaining_grams', '0')))} g a USD {escape(str(item.get('unit_cost_usd', '0')))}</li>"
        for item in cast(List[Dict[str, Any]], inventory.get("open_lots") or [])[:8]
    ) or "<li>Sem lotes abertos.</li>"

    html = f"""
    <html>
        <head>
            <title>Caixa Admin Dashboard</title>
            <style>
                body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #111; }}
                h1, h2 {{ margin-bottom: 8px; }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }}
                .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 16px; background: #fafafa; }}
                ul {{ padding-left: 18px; }}
                .kpi {{ font-size: 20px; font-weight: 700; }}
            </style>
        </head>
        <body>
            <h1>Caixa Admin Dashboard</h1>
            <p>Data: {escape(day['date'])}</p>
            <div class="grid">
                <div class="card">
                    <h2>Resumo Diario</h2>
                    <div class="kpi">Operacoes: {escape(str(summary.get('total_operacoes', 0)))}</div>
                    <p>Total USD: {escape(str(summary.get('total_usd', '0')))}</p>
                    <p>Total pago USD: {escape(str(summary.get('total_pago_usd', '0')))}</p>
                    <p>Diferenca USD: {escape(str(summary.get('total_diferenca_usd', '0')))}</p>
                </div>
                <div class="card">
                    <h2>Estoque Ouro</h2>
                    <p>Disponivel: {escape(str(inventory['available_grams']))} g</p>
                    <p>Custo FIFO aberto: USD {escape(str(inventory['inventory_cost_usd']))}</p>
                    <p>Custo medio aberto: USD {escape(str(inventory['avg_cost_usd_per_gram']))}/g</p>
                    <ul>{lot_items}</ul>
                </div>
                <div class="card">
                    <h2>Saldos dos 5 Caixas</h2>
                    <ul>{saldo_items}</ul>
                </div>
                <div class="card">
                    <h2>Alertas de Risco</h2>
                    <ul>{alert_items}</ul>
                </div>
                <div class="card">
                    <h2>Top Divergencias</h2>
                    <ul>{divergence_items}</ul>
                </div>
                <div class="card">
                    <h2>Runs Multiagente</h2>
                    <ul>{run_items}</ul>
                </div>
            </div>
        </body>
    </html>
    """
    return Response(content=html, media_type="text/html")


@app.get("/saas")
@app.get("/saas/dashboard")
def saas_dashboard(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        return Response(content=_render_saas_login_html(), media_type="text/html")
    return Response(content=_render_saas_dashboard_html(db, session_user), media_type="text/html")


@app.post("/saas/login")
async def saas_login(request: Request) -> Response:
    form = await _request_form_dict(request)
    telefone = _normalize_user_phone(str(form.get("telefone") or ""))
    pin = str(form.get("pin") or "")
    if not telefone or not pin:
        return Response(content=_render_saas_login_html("Informe telefone e PIN.", telefone=telefone), media_type="text/html", status_code=400)

    db = get_db()
    usuario = db.verify_usuario_web_pin(telefone, pin)
    if not usuario:
        return Response(content=_render_saas_login_html("Credenciais inválidas.", telefone=telefone), media_type="text/html", status_code=401)

    response = Response(content=_render_saas_dashboard_html(db, usuario), media_type="text/html")
    _set_saas_session(response, telefone)
    return response


@app.post("/saas/logout")
def saas_logout() -> Response:
    response = Response(content=_render_saas_login_html("Sessão encerrada."), media_type="text/html")
    _clear_saas_session(response)
    return response


@app.post("/saas/profile/pin")
async def saas_profile_pin(request: Request) -> Response:
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response

    form = await _request_form_dict(request)
    current_pin = str(form.get("current_pin") or "")
    new_pin = str(form.get("new_pin") or "")
    confirm_pin = str(form.get("confirm_pin") or "")
    try:
        _validate_web_pin_format(current_pin)
        validated_new_pin = _validate_web_pin_format(new_pin)
    except HTTPException as exc:
        html = _render_saas_dashboard_html(db, session_user, notice=str(exc.detail), notice_kind="error")
        return Response(content=html, media_type="text/html", status_code=exc.status_code)

    if validated_new_pin != confirm_pin:
        html = _render_saas_dashboard_html(db, session_user, notice="Confirmação do novo PIN não confere.", notice_kind="error")
        return Response(content=html, media_type="text/html", status_code=400)
    if not db.verify_usuario_web_pin(str(session_user.get("telefone") or ""), current_pin):
        html = _render_saas_dashboard_html(db, session_user, notice="PIN atual inválido.", notice_kind="error")
        return Response(content=html, media_type="text/html", status_code=401)
    update_result = db.set_usuario_web_pin(str(session_user.get("telefone") or ""), validated_new_pin)
    if not update_result:
        html = _render_saas_dashboard_html(db, session_user, notice="Não foi possível atualizar o PIN.", notice_kind="error")
        return Response(content=html, media_type="text/html", status_code=500)
    if not bool(update_result.get("web_pin_schema_ready", True)):
        html = _render_saas_dashboard_html(
            db,
            session_user,
            notice="Troca de PIN indisponível: aplique a migração do banco que adiciona web_pin_hash e web_pin_updated_em na tabela usuarios.",
            notice_kind="error",
        )
        return Response(content=html, media_type="text/html", status_code=409)

    refreshed_user = db.get_usuario_web_auth(str(session_user.get("telefone") or "")) or session_user
    response = Response(content=_render_saas_dashboard_html(db, refreshed_user, notice="PIN web atualizado com sucesso."), media_type="text/html")
    _set_saas_session(response, str(session_user.get("telefone") or ""))
    return response


@app.post("/saas/console")
async def saas_console(request: Request) -> Response:
    form = await _request_form_dict(request)
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response
    remetente = str(form.get("console_remetente") or "").strip()
    mensagem = str(form.get("console_mensagem") or "").strip()
    values = {k: str(v) for k, v in form.items()}
    if not remetente or not mensagem:
        html = _render_saas_dashboard_html(db, session_user, notice="Preencha remetente e mensagem no console.", notice_kind="error", form_values=values)
        return Response(content=html, media_type="text/html", status_code=400)

    if str(session_user.get("tipo_usuario") or "").lower() != "admin":
        remetente = str(session_user.get("telefone") or remetente)
        values["console_remetente"] = remetente

    try:
        result = _processar_webhook(WhatsAppWebhookPayload(remetente=remetente, mensagem=mensagem), db, None)
        html = _render_saas_dashboard_html(db, session_user, notice="Mensagem processada pelo motor do WhatsApp.", notice_kind="info", assistant_result=result, form_values=values)
        return Response(content=html, media_type="text/html")
    except HTTPException as exc:
        html = _render_saas_dashboard_html(db, session_user, notice=_ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail)), notice_kind="error", form_values=values)
        return Response(content=html, media_type="text/html", status_code=exc.status_code)


@app.post("/saas/operations/quick")
async def saas_quick_operation(request: Request) -> Response:
    form = await _request_form_dict(request)
    db = get_db()
    session_user = _get_saas_authenticated_user(request, db)
    if not session_user:
        response = Response(content=_render_saas_login_html("Faça login para continuar."), media_type="text/html", status_code=401)
        _clear_saas_session(response)
        return response
    values = {k: str(v) for k, v in form.items()}
    try:
        operador_id = _normalize_user_phone(str(form.get("operador_id") or session_user.get("telefone") or ""))
        tipo_operacao = _normalize_text(str(form.get("tipo_operacao") or "compra"))
        origem = _normalize_text(str(form.get("origem") or "balcao"))
        teor = _parse_decimal_web_field(str(form.get("teor") or "0"), "teor")
        peso = _parse_decimal_web_field(str(form.get("peso") or "0"), "peso")
        preco_usd = _parse_decimal_web_field(str(form.get("preco_usd") or "0"), "preco_usd")
        pessoa = str(form.get("pessoa") or "").strip()
        observacoes = str(form.get("observacoes") or "").strip()
        if tipo_operacao not in {"compra", "venda"}:
            raise HTTPException(status_code=400, detail="Tipo de operação inválido")
        if origem not in {"balcao", "fora"}:
            raise HTTPException(status_code=400, detail="Origem inválida")
        if teor < 0 or teor > Decimal("99.99"):
            raise HTTPException(status_code=400, detail="Teor inválido")
        if peso <= 0 or preco_usd <= 0:
            raise HTTPException(status_code=400, detail="Peso e preço devem ser maiores que zero")
        if not pessoa:
            raise HTTPException(status_code=400, detail="Pessoa é obrigatória")

        session_phone = str(session_user.get("telefone") or "")
        is_admin = str(session_user.get("tipo_usuario", "")).lower() == "admin"
        if not operador_id:
            operador_id = session_phone
        if not is_admin and operador_id != session_phone:
            raise HTTPException(status_code=403, detail="Operador web só pode lançar em seu próprio usuário")

        usuario = db.get_usuario_by_telefone(operador_id)
        if not usuario:
            raise HTTPException(status_code=403, detail="Operador não autorizado")

        total_usd = money(peso * preco_usd)
        pagamentos = _parse_web_payments_from_form(db, values)
        total_pago_usd = sum((Decimal(str(item.get("valor_usd") or "0")) for item in pagamentos), Decimal("0"))
        forma_pagamento = _derive_forma_pagamento_summary(pagamentos)

        fechamento_raw = str(form.get("fechamento_gramas") or "").strip()
        fechamento_gramas = peso if not fechamento_raw else _parse_decimal_web_field(fechamento_raw, "fechamento_gramas")
        fechamento_tipo = _normalize_text(str(form.get("fechamento_tipo") or "total"))
        if fechamento_tipo not in {"total", "parcial"}:
            raise HTTPException(status_code=400, detail="Fechamento inválido")
        if fechamento_gramas < 0 or fechamento_gramas > peso:
            raise HTTPException(status_code=400, detail="Fechamento em gramas inválido")

        contexto: Dict[str, Any] = {
            "tipo_operacao": tipo_operacao,
            "origem": origem,
            "teor": str(money(teor)),
            "peso": str(peso),
            "preco_moeda": "USD",
            "preco_usd": str(money(preco_usd)),
            "total_usd": str(total_usd),
            "total_pago_usd": str(money(total_pago_usd)),
            "fechamento_gramas": str(money(fechamento_gramas)),
            "fechamento_tipo": fechamento_tipo,
            "pessoa": pessoa,
            "forma_pagamento": forma_pagamento,
            "observacoes": observacoes,
            "source_message_id": None,
            "pagamentos": pagamentos,
        }
        if tipo_operacao == "venda":
            _attach_sale_profit_reference(db, contexto)

        projected = _project_caixa_balances(db.get_saldo_caixa(), tipo_operacao, peso, cast(List[Dict[str, Any]], contexto["pagamentos"]))
        negative_balances = _find_negative_caixa_balances(projected)
        fifo_shortfall = Decimal(str(contexto.get("fifo_shortfall_grams", "0")))
        risk_lines: List[str] = []
        if negative_balances:
            risk_lines.append("Saldos projetados negativos:")
            risk_lines.extend(_format_negative_caixa_lines(negative_balances))
        if fifo_shortfall > 0:
            risk_lines.append(f"- Estoque FIFO insuficiente: faltam {fifo_shortfall} g")

        wants_override = str(form.get("risk_override") or "") == "1"
        if risk_lines and not (is_admin and wants_override):
            html = _render_saas_dashboard_html(db, session_user, notice="⛔ " + " | ".join(risk_lines), notice_kind="error", form_values=values)
            return Response(content=html, media_type="text/html", status_code=400)

        result = _persist_gold_operation_from_context(db, operador_id, contexto, post_save_session=False)
        gt_id = result.get("dados", {}).get("gold_transaction_id")
        ok_msg = f"Operação web salva com sucesso. GT-{gt_id}" if gt_id else "Operação web salva com sucesso."
        html = _render_saas_dashboard_html(db, session_user, notice=ok_msg, notice_kind="info", assistant_result=result, form_values=values)
        return Response(content=html, media_type="text/html")
    except HTTPException as exc:
        html = _render_saas_dashboard_html(db, session_user, notice=_ERROS_AMIGAVEIS.get(exc.status_code, str(exc.detail)), notice_kind="error", form_values=values)
        return Response(content=html, media_type="text/html", status_code=exc.status_code)


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


def _compute_ai_window_metrics(db: DatabaseClient, days: int) -> Dict[str, Any]:
    window_days = max(1, days)
    now_utc = datetime.now(timezone.utc)
    start_iso = (now_utc - timedelta(days=window_days)).isoformat()
    end_iso = now_utc.isoformat()

    runs = db.get_multi_agent_runs_range(start_iso, end_iso, limit=1000)
    learning_snapshot = db.get_transaction_learning_snapshot(lookback_days=window_days)
    alerts = db.get_risk_alerts(start_iso, end_iso)

    runs_with_risk = 0
    runs_with_fail_safe = 0
    total_risks = 0

    for run in runs:
        response_payload = cast(Dict[str, Any], run.get("response_payload") or {})
        risks = cast(List[Any], response_payload.get("risks") or [])
        transcript = cast(List[Any], response_payload.get("transcript") or [])

        if risks:
            runs_with_risk += 1
            total_risks += len(risks)

        has_fail_safe = False
        for item in transcript:
            if isinstance(item, dict):
                item_dict = cast(Dict[str, Any], item)
                if str(item_dict.get("role", "")).lower() == "fail-safe":
                    has_fail_safe = True
                    break
        if has_fail_safe:
            runs_with_fail_safe += 1

    total_runs = len(runs)
    risk_ratio = round(runs_with_risk / total_runs, 4) if total_runs else 0.0
    fail_safe_ratio = round(runs_with_fail_safe / total_runs, 4) if total_runs else 0.0
    avg_risks_per_run = round(total_risks / total_runs, 4) if total_runs else 0.0
    confidence = _compute_ai_confidence_score(
        total_samples=int(learning_snapshot.get("total_samples", 0) or 0),
        risk_ratio=risk_ratio,
        fail_safe_ratio=fail_safe_ratio,
        risk_alerts=len(alerts),
        total_runs=total_runs,
    )
    total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
    learning_phase = "seed"
    if total_samples >= 300:
        learning_phase = "advanced"
    elif total_samples >= 30:
        learning_phase = "learning_stable"

    return {
        "window_days": window_days,
        "range": {"start": start_iso, "end": end_iso},
        "runs": total_runs,
        "runs_with_risk": runs_with_risk,
        "runs_with_fail_safe": runs_with_fail_safe,
        "risk_ratio": risk_ratio,
        "fail_safe_ratio": fail_safe_ratio,
        "avg_risks_per_run": avg_risks_per_run,
        "risk_alerts": len(alerts),
        "learning_samples": total_samples,
        "learning_phase": learning_phase,
        "confidence_score": confidence["score"],
        "confidence_band": confidence["band"],
        "confidence_profile": confidence["profile"],
        "confidence_profile_mode": confidence["profile_mode"],
    }


def _trend_label(delta: float, good_when_negative: bool = True) -> str:
    eps = 0.0001
    if abs(delta) <= eps:
        return "stable"
    if good_when_negative:
        return "improving" if delta < 0 else "worsening"
    return "improving" if delta > 0 else "worsening"


def _phase_transition_label(from_phase: str, to_phase: str) -> str:
    order = {
        "seed": 0,
        "learning_stable": 1,
        "advanced": 2,
    }
    if from_phase == to_phase:
        return "stable"
    from_rank = order.get(from_phase, 0)
    to_rank = order.get(to_phase, 0)
    if to_rank > from_rank:
        return "maturing"
    if to_rank < from_rank:
        return "regressing"
    return "stable"


def _profile_transition_label(from_profile: str, to_profile: str) -> str:
    if from_profile == to_profile:
        return "stable"
    return f"{from_profile}_to_{to_profile}"


def _compute_ai_confidence_score(
    *,
    total_samples: int,
    risk_ratio: float,
    fail_safe_ratio: float,
    risk_alerts: int,
    total_runs: int,
) -> Dict[str, Any]:
    cfg = _get_ai_conf_config(total_samples)

    weight_total = float(cfg["weight_maturity"]) + float(cfg["weight_stability"]) + float(cfg["weight_alerts"])
    if weight_total <= 0:
        normalized_maturity = 0.45
        normalized_stability = 0.45
        normalized_alerts = 0.10
    else:
        normalized_maturity = float(cfg["weight_maturity"]) / weight_total
        normalized_stability = float(cfg["weight_stability"]) / weight_total
        normalized_alerts = float(cfg["weight_alerts"]) / weight_total

    sample_maturity = min(max(total_samples, 0) / float(cfg["samples_target"]), 1.0)
    stability_penalty = min(max((risk_ratio * float(cfg["risk_weight"])) + (fail_safe_ratio * float(cfg["failsafe_weight"])), 0.0), 1.0)
    stability = 1.0 - stability_penalty
    alerts_per_run = (risk_alerts / max(total_runs, 1)) if total_runs >= 0 else 0.0
    alert_pressure = min(max(alerts_per_run, 0.0), 1.0)

    score_raw = (
        (sample_maturity * normalized_maturity * 100.0)
        + (stability * normalized_stability * 100.0)
        + ((1.0 - alert_pressure) * normalized_alerts * 100.0)
    )
    score = max(0.0, min(100.0, score_raw))

    cut_excellent = max(1, min(100, int(cfg["band_excellent"])))
    cut_good = max(1, min(cut_excellent, int(cfg["band_good"])))
    cut_moderate = max(1, min(cut_good, int(cfg["band_moderate"])))

    band = "low"
    if score >= cut_excellent:
        band = "excellent"
    elif score >= cut_good:
        band = "good"
    elif score >= cut_moderate:
        band = "moderate"

    return {
        "score": round(score, 2),
        "band": band,
        "profile": str(cfg["profile_effective"]),
        "profile_mode": str(cfg["profile_setting"]),
        "components": {
            "sample_maturity": round(sample_maturity, 4),
            "stability": round(stability, 4),
            "alert_pressure": round(alert_pressure, 4),
        },
    }


def _parse_trend_windows_param(windows: str) -> List[int]:
    """Parse query string like '7,30,90' into sanitized unique sorted windows."""
    default_windows = [7, 30]
    if not windows.strip():
        return default_windows

    parsed: List[int] = []
    for raw in windows.split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if 1 <= value <= 365 and value not in parsed:
            parsed.append(value)

    if not parsed:
        return default_windows

    parsed.sort()
    return parsed[:6]


@app.get("/ai/health")
def ai_health_report() -> Dict[str, Any]:
    db = get_db()
    live_context = db.build_multi_agent_live_context(operation_id=None)
    learning_snapshot = cast(Dict[str, Any], live_context.get("learning_snapshot") or {})
    recent_runs = db.get_recent_multi_agent_runs(limit=50)

    total_samples = int(learning_snapshot.get("total_samples", 0) or 0)
    ops_stats = cast(Dict[str, Any], learning_snapshot.get("operations") or {})
    operator_profiles = cast(Dict[str, Any], learning_snapshot.get("operator_profiles") or {})

    runs_24h = 0
    runs_with_risk = 0
    runs_with_fail_safe = 0
    now_utc = datetime.now(timezone.utc)

    for run in recent_runs:
        created_raw = str(run.get("criado_em") or "")
        response_payload = cast(Dict[str, Any], run.get("response_payload") or {})
        risks = cast(List[Any], response_payload.get("risks") or [])
        transcript = cast(List[Any], response_payload.get("transcript") or [])

        if risks:
            runs_with_risk += 1

        has_fail_safe = False
        for item in transcript:
            if isinstance(item, dict):
                item_dict = cast(Dict[str, Any], item)
                if str(item_dict.get("role", "")).lower() == "fail-safe":
                    has_fail_safe = True
                    break
        if has_fail_safe:
            runs_with_fail_safe += 1

        if created_raw:
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_hours = (now_utc - created_dt.astimezone(timezone.utc)).total_seconds() / 3600
                if age_hours <= 24:
                    runs_24h += 1
            except Exception:
                pass

    model_maturity = "seed"
    if total_samples >= 300:
        model_maturity = "advanced"
    elif total_samples >= 100:
        model_maturity = "stable"
    elif total_samples >= 30:
        model_maturity = "learning"

    risk_ratio = 0.0
    fail_safe_ratio = 0.0
    if recent_runs:
        risk_ratio = round(runs_with_risk / len(recent_runs), 4)
        fail_safe_ratio = round(runs_with_fail_safe / len(recent_runs), 4)

    risk_alerts_today = len(cast(List[Any], live_context.get("risk_alerts") or []))
    daily_operations = int(cast(Dict[str, Any], live_context.get("daily_summary") or {}).get("total_operacoes", 0) or 0)
    confidence = _compute_ai_confidence_score(
        total_samples=total_samples,
        risk_ratio=risk_ratio,
        fail_safe_ratio=fail_safe_ratio,
        risk_alerts=risk_alerts_today,
        total_runs=max(len(recent_runs), daily_operations),
    )

    readiness = "ok"
    readiness_reasons: List[str] = []
    if total_samples < 30:
        readiness = "attention"
        readiness_reasons.append("base_historica_baixa")
    if fail_safe_ratio > 0.05:
        readiness = "attention"
        readiness_reasons.append("falha_interna_agentes")
    if risk_ratio > 0.5:
        readiness = "attention"
        readiness_reasons.append("alta_taxa_alertas_risco")

    if not readiness_reasons:
        readiness_reasons.append("operacao_dentro_do_esperado")

    return {
        "status": readiness,
        "confidence": confidence,
        "readiness_reasons": readiness_reasons,
        "learning": {
            "maturity": model_maturity,
            "lookback_days": int(learning_snapshot.get("lookback_days", 0) or 0),
            "total_samples": total_samples,
            "operation_profiles": len(ops_stats),
            "operator_profiles": len(operator_profiles),
            "currency_mix": cast(Dict[str, Any], learning_snapshot.get("currency_mix") or {}),
        },
        "multi_agent": {
            "recent_runs": len(recent_runs),
            "runs_24h": runs_24h,
            "risk_ratio": risk_ratio,
            "fail_safe_ratio": fail_safe_ratio,
            "risk_alerts_today": risk_alerts_today,
        },
        "observability": {
            "top_divergences_today": len(cast(List[Any], live_context.get("top_divergences") or [])),
            "daily_operations": daily_operations,
        },
    }


@app.get("/ai/health/trends")
def ai_health_trends(windows: str = "7,30") -> Dict[str, Any]:
    db = get_db()

    selected_windows = _parse_trend_windows_param(windows)
    metrics_by_window: Dict[int, Dict[str, Any]] = {}
    for days in selected_windows:
        metrics_by_window[days] = _compute_ai_window_metrics(db, days=days)

    short_window = selected_windows[0]
    long_window = selected_windows[-1]
    short_metrics = metrics_by_window[short_window]
    long_metrics = metrics_by_window[long_window]

    risk_ratio_delta = round(short_metrics["risk_ratio"] - long_metrics["risk_ratio"], 4)
    fail_safe_delta = round(short_metrics["fail_safe_ratio"] - long_metrics["fail_safe_ratio"], 4)
    avg_risk_delta = round(short_metrics["avg_risks_per_run"] - long_metrics["avg_risks_per_run"], 4)
    alerts_delta = int(short_metrics["risk_alerts"]) - int(long_metrics["risk_alerts"])
    learning_delta = int(short_metrics["learning_samples"]) - int(long_metrics["learning_samples"])
    confidence_delta = round(float(short_metrics["confidence_score"]) - float(long_metrics["confidence_score"]), 4)

    trend_summary: Dict[str, Dict[str, Any]] = {
        "risk_ratio": {
            "delta": risk_ratio_delta,
            "trend": _trend_label(risk_ratio_delta, good_when_negative=True),
        },
        "fail_safe_ratio": {
            "delta": fail_safe_delta,
            "trend": _trend_label(fail_safe_delta, good_when_negative=True),
        },
        "avg_risks_per_run": {
            "delta": avg_risk_delta,
            "trend": _trend_label(avg_risk_delta, good_when_negative=True),
        },
        "risk_alerts": {
            "delta": alerts_delta,
            "trend": _trend_label(float(alerts_delta), good_when_negative=True),
        },
        "learning_samples": {
            "delta": learning_delta,
            "trend": _trend_label(float(learning_delta), good_when_negative=False),
        },
        "confidence_score": {
            "delta": confidence_delta,
            "trend": _trend_label(confidence_delta, good_when_negative=False),
        },
        "learning_phase": {
            "from": long_metrics["learning_phase"],
            "to": short_metrics["learning_phase"],
            "trend": _phase_transition_label(
                str(long_metrics["learning_phase"]),
                str(short_metrics["learning_phase"]),
            ),
            "transition": f"{long_metrics['learning_phase']} -> {short_metrics['learning_phase']}",
        },
        "confidence_profile": {
            "from": long_metrics["confidence_profile"],
            "to": short_metrics["confidence_profile"],
            "trend": _profile_transition_label(
                str(long_metrics["confidence_profile"]),
                str(short_metrics["confidence_profile"]),
            ),
            "transition": f"{long_metrics['confidence_profile']} -> {short_metrics['confidence_profile']}",
        },
    }

    windows_payload: Dict[str, Any] = {}
    for days in selected_windows:
        windows_payload[f"last_{days}_days"] = metrics_by_window[days]

    return {
        "selected_windows": selected_windows,
        "comparison": {
            "short_window_days": short_window,
            "long_window_days": long_window,
            "short_learning_phase": short_metrics["learning_phase"],
            "long_learning_phase": long_metrics["learning_phase"],
            "short_confidence_profile": short_metrics["confidence_profile"],
            "long_confidence_profile": long_metrics["confidence_profile"],
        },
        "windows": windows_payload,
        "trend_summary": trend_summary,
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
            if _should_reset_guided_session_for_message(mensagem):
                _clear_session(db, remetente)
                session = None
                estado = ""
            elif estado != "await_resume_confirmacao" and _is_guided_session_stale(session):
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
            if _should_reset_guided_session_for_message(mensagem):
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
                "Tente: 'compra', 'venda', 'caixa', 'extrato' ou 'taxa ouro 70.00'."
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
                "Informe o ativo e a quantidade, por exemplo: 'venda ouro 3g'."
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
        if requested_currency:
            day = _build_day_range(None)
            response_payload = _build_caixa_detail_response(
                db,
                requested_currency,
                day["start"],
                day["end"],
                f"Hoje ({day['date']})",
            )
            _clear_session(db, remetente)
        else:
            response_payload = _build_caixa_response(db, requested_currency=requested_currency)
            _save_session(
                db=db,
                remetente=remetente,
                estado="await_caixa_detalhe",
                contexto={"source": "caixa_summary"},
            )
        resposta = response_payload["mensagem"]
        day = {"date": str(response_payload["dados"].get("date", ""))}
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
    kind = str(request.query_params.get("kind") or "transacao").strip().lower()

    if kind == "gold":
        ok = db.cancel_gold_transaction(operation_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Operação guiada não encontrada")
        return {
            "mensagem": f"✅ Operação GT-{operation_id} cancelada",
            "dados": {"id": operation_id, "status": "cancelada", "kind": "gold"},
        }

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
