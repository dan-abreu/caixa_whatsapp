#!/usr/bin/env python3
"""Quick test of the new 5-caixa system"""
# ruff: noqa: E402

import sys
from pathlib import Path
from decimal import Decimal

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.database import DatabaseClient


def test_caixas():
    """Test the new 5-caixa system."""
    print("🧪 Testing 5-caixa system...")

    try:
        db = DatabaseClient()
        print("✅ DatabaseClient initialized")
    except Exception as e:
        print(f"❌ Failed to init DatabaseClient: {e}")
        return

    # Ensure caixas exist
    try:
        db._ensure_caixas_exist()
        print("✅ Caixas ensured to exist")
    except Exception as e:
        print(f"❌ Failed to ensure caixas: {e}")
        return

    # Get current saldo
    try:
        saldo = db.get_saldo_caixa()
        print(f"✅ get_saldo_caixa() returned:\n{saldo}")
    except Exception as e:
        print(f"❌ Failed to get saldo: {e}")
        return
    
    # Test update_caixas_from_transaction (simulated)
    print("\n🔄 Testing update_caixas_from_transaction()...")
    try:
        test_pagamentos = [
            {"moeda": "EUR", "valor_moeda": "100"},
            {"moeda": "USD", "valor_moeda": "50"},
        ]

        # Simulate a compra
        db.update_caixas_from_transaction(
            gold_transaction_id=999,
            tipo_operacao="compra",
            peso_gramas=Decimal("10"),
            pagamentos=test_pagamentos,
            pessoa="Test User",
        )
        print("✅ update_caixas_from_transaction() completed")

        # Get saldo again
        saldo_after = db.get_saldo_caixa()
        print(f"✅ Saldo after transaction:\n{saldo_after}")
        
    except Exception as e:
        print(f"❌ Failed to update caixas: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n🎉 All tests passed!")

if __name__ == "__main__":
    test_caixas()
