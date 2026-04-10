#!/usr/bin/env python3

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("WEBHOOK_TOKEN", "test_token_12345")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("TZ_OFFSET_HOURS", "-3")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def print_header() -> None:
    print("=" * 80)
    print("TESTE COMPLETO - CAIXA INTELIGENTE")
    print("=" * 80)
    print(f"Inicio: {utc_now_iso()}\n")


def print_footer() -> None:
    print("\n\nTESTE COMPLETO FINALIZADO COM SUCESSO!")
    print(f"Fim: {utc_now_iso()}")
    print("=" * 80)


def print_section(title: str) -> None:
    print(f"\n\n{title}")
    print("-" * 80)


def build_mock_db() -> MagicMock:
    mock_db = MagicMock()
    mock_db.get_usuario_by_telefone.return_value = {
        "id": 1,
        "telefone": "+5511999999999",
        "nome": "Usuario Teste",
        "tipo_usuario": "operator",
    }
    mock_db.get_saldo_caixa.return_value = {
        "gold_gramas": "5.5",
        "moedas": {"USD": "1250.00", "EUR": "950.00", "SRD": "500.00"},
    }
    mock_db.get_daily_gold_summary.return_value = {
        "total_operacoes": 3,
        "total_peso": "15.2",
        "total_usd": "1750.00",
    }
    mock_db.get_ativo_by_nome.return_value = {"id": 1, "nome": "Ouro"}
    mock_db.get_taxa_atual.return_value = {"preco_compra": "65.50"}
    mock_db.get_processed_message.return_value = None
    mock_db.get_conversation_session.return_value = None
    mock_db.save_processed_message.return_value = None
    mock_db.save_conversation_session.return_value = None
    mock_db.clear_conversation_session.return_value = None
    return mock_db