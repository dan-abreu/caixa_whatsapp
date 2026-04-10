from typing import Any, Dict, List


MARKET_MONITOR_CARDS: List[Dict[str, Any]] = [
    {"field": "eur_brl_raw", "label": "EUR/REAL", "prefix": "", "suffix": "", "decimals": 4, "alert_enabled": False, "priority": "primary"},
    {"field": "usd_brl_raw", "label": "USD/BRL", "prefix": "", "suffix": "", "decimals": 4, "alert_enabled": False, "priority": "primary"},
    {"field": "xau_usd_raw", "label": "XAU/USD", "prefix": "USD ", "suffix": "", "decimals": 2, "alert_enabled": True, "priority": "secondary"},
    {"field": "grama_ref_raw", "label": "Grama referencia", "prefix": "USD ", "suffix": "/g", "decimals": 2, "alert_enabled": True, "priority": "secondary"},
]

ERROS_AMIGAVEIS: Dict[int, str] = {
    400: "Solicitacao nao compreendida. Utilize, por exemplo: compra | venda | caixa | extrato | taxa ouro 70.00",
    401: "Acesso negado. Token de autenticacao invalido.",
    403: "Permissao insuficiente para esta operacao.",
    404: "Recurso nao localizado. Envie 'menu' para consultar as opcoes disponiveis.",
    422: "Dados insuficientes para processamento. Reformule a mensagem com maior objetividade.",
    500: "Falha interna de processamento. Tente novamente em alguns segundos.",
    502: "O servico de IA nao respondeu no momento. Tente novamente.",
}

MOEDAS_SUPORTADAS = ["USD", "SRD", "EUR", "BRL"]

GUIDED_FLOW_STATES = {
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