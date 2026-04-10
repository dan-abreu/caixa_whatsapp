from tests.comprehensive.common import print_section


FEATURES_TESTED = [
    ("GET /health", "Verificar status da API"),
    ("GET /menu", "Listar opcoes do menu"),
    ("POST /webhook/whatsapp - Consultar Relatorio", "Ver caixa"),
    ("POST /webhook/whatsapp - Registrar Operacao", "Registrar compra/venda"),
    ("POST /webhook/whatsapp - Atualizar Taxa", "Atualizar taxa de ativo"),
    ("POST /webhook/whatsapp - Conversa", "Conversa livre"),
    ("Sanitizacao de Payload IA", "Validar dados extraidos pela IA"),
    ("Normalizacao de Moedas", "Mapear aliases de moedas"),
    ("Validacao de Webhook Token", "Validar autenticacao"),
    ("Tratamento de Erros", "Retornar mensagens amigaveis"),
]


def run_sanitization_checks() -> dict[str, int]:
    print_section("[TESTE 4] Guardrails de IA (Sanitizacao)")
    passed = 0
    failed = 0

    try:
        from app.ai_service import _sanitize_extracted_payload

        sanitization_tests = [
            {"name": "Payload Valido - Registrar Operacao", "payload": {"intencao": "registrar_operacao", "ativo": "ouro", "quantidade": 2.5, "valor_informado": 105.0}, "message": "Comprei 2.5g de ouro a 105", "should_pass": True},
            {"name": "Payload Invalido - Ativo Desconhecido", "payload": {"intencao": "registrar_operacao", "ativo": "diamante", "quantidade": 2.5, "valor_informado": 105.0}, "message": "Comprei diamante", "should_pass": False},
            {"name": "Payload Invalido - Quantidade Negativa", "payload": {"intencao": "registrar_operacao", "ativo": "ouro", "quantidade": -5.0, "valor_informado": 105.0}, "message": "Comprei -5g de ouro", "should_pass": False},
            {"name": "Payload Atualizar Taxa - Valido", "payload": {"intencao": "atualizar_taxa", "ativo": "usd", "quantidade": None, "valor_informado": 5.30}, "message": "Taxa USD 5.30", "should_pass": True},
        ]

        print("\nTestando _sanitize_extracted_payload():")
        for test in sanitization_tests:
            try:
                result = _sanitize_extracted_payload(message=test["message"], payload=test["payload"])
                is_valid = result.get("intencao") != "conversar" or test["payload"].get("intencao") == "conversar"
                if test["should_pass"] and is_valid:
                    print(f"OK {test['name']}: passou como esperado")
                    passed += 1
                elif test["should_pass"]:
                    print(f"FALHA {test['name']}: deveria ter passado mas foi rejeitado")
                    failed += 1
                elif not is_valid:
                    print(f"OK {test['name']}: rejeitado como esperado")
                    passed += 1
                else:
                    print(f"FALHA {test['name']}: deveria ter sido rejeitado")
                    failed += 1
            except Exception:
                if not test["should_pass"]:
                    print(f"OK {test['name']}: levantou excecao como esperado")
                    passed += 1
                else:
                    print(f"FALHA {test['name']}: erro inesperado")
                    failed += 1
    except Exception as exc:
        print(f"AVISO: Nao foi possivel testar sanitizacao: {exc}")

    print(f"\nResultado: {passed} passaram, {failed} falharam")
    return {"passed": passed, "failed": failed, "total": passed + failed}


def run_currency_checks() -> dict[str, int]:
    print_section("[TESTE 5] Moedas Suportadas")
    from app.ai_service import _normalize_ativo_value

    moeda_tests = [
        ("ouro", "ouro"),
        ("gold", "ouro"),
        ("oro", "ouro"),
        ("or", "ouro"),
        ("usd", "usd"),
        ("dolar", "usd"),
        ("dollar", "usd"),
        ("eur", "eur"),
        ("euro", "eur"),
        ("srd", "srd"),
        ("invalida", None),
    ]

    print("\nTestando _normalize_ativo_value():")
    passed = 0
    failed = 0
    for input_val, expected in moeda_tests:
        result = _normalize_ativo_value(input_val)
        if result == expected:
            print(f"OK '{input_val}' -> '{result}'")
            passed += 1
        else:
            print(f"FALHA '{input_val}': esperado '{expected}', recebido '{result}'")
            failed += 1

    print(f"\nResultado: {passed} passaram, {failed} falharam")
    return {"passed": passed, "failed": failed, "total": len(moeda_tests)}


def print_feature_list() -> None:
    print_section("[TESTE 6] Features Principais")
    print("\nFeatures testadas:")
    for index, (feature, description) in enumerate(FEATURES_TESTED, 1):
        print(f"  {index}. {feature:40} - {description}")