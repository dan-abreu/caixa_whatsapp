from tests.comprehensive.common import TestClient, build_mock_db, patch, print_section


TEST_CASES = [
    {
        "name": "Consultar Relatorio (Caixa)",
        "remetente": "+5511988776655",
        "mensagem": "caixa",
        "expected_status": 200,
    },
    {
        "name": "Registrar Operacao (Simples)",
        "remetente": "+5511988776656",
        "mensagem": "Comprei 2g de ouro a 105",
        "expected_status": 200,
    },
    {
        "name": "Atualizar Taxa (Admin)",
        "remetente": "+5511988776657",
        "mensagem": "Taxa Ouro 70.50",
        "expected_status": 200,
    },
    {
        "name": "Conversa Geral",
        "remetente": "+5511988776658",
        "mensagem": "Oi, tudo bem?",
        "expected_status": 200,
    },
    {
        "name": "Menu Request",
        "remetente": "+5511988776659",
        "mensagem": "menu",
        "expected_status": 200,
    },
]

ERROR_TEST_CASES = [
    {
        "name": "Webhook sem Token",
        "headers": {},
        "json": {"remetente": "+5511988776660", "mensagem": "teste"},
        "expected_status": 200,
        "expected_error_code": 401,
    },
    {
        "name": "Webhook com Token Invalido",
        "headers": {"X-Webhook-Token": "token_errado"},
        "json": {"remetente": "+5511988776661", "mensagem": "teste"},
        "expected_status": 200,
        "expected_error_code": 401,
    },
    {
        "name": "Webhook sem Mensagem",
        "headers": {"X-Webhook-Token": "test_token_12345"},
        "json": {"remetente": "+5511988776662", "mensagem": ""},
        "expected_status": 400,
    },
    {
        "name": "Webhook sem Remetente",
        "headers": {"X-Webhook-Token": "test_token_12345"},
        "json": {"remetente": "", "mensagem": "teste"},
        "expected_status": 200,
    },
]


def _mock_ai_response(msg: str) -> dict[str, object]:
    msg_lower = msg.lower()
    if "caixa" in msg_lower or "extrato" in msg_lower:
        return {"intencao": "consultar_relatorio", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": None}
    if "comprei" in msg_lower or "compra" in msg_lower:
        return {"intencao": "registrar_operacao", "ativo": "ouro", "quantidade": 2.0, "valor_informado": 105.0, "resposta": None}
    if "taxa" in msg_lower:
        return {"intencao": "atualizar_taxa", "ativo": "ouro", "quantidade": None, "valor_informado": 70.50, "resposta": None}
    return {"intencao": "conversar", "ativo": None, "quantidade": None, "valor_informado": None, "resposta": "Ola! Como posso ajudar?"}


def run_webhook_checks() -> list[dict[str, object]]:
    print_section("[TESTE 2] Webhooks com Diferentes Intencoes")
    results: list[dict[str, object]] = []

    with patch("app.main.DatabaseClient") as mock_database, patch("app.services.app_composition_runtime.extract_message_data") as mock_ai:
        mock_db = build_mock_db()
        mock_db.include_ignored_files = True
        mock_database.return_value = mock_db
        mock_ai.side_effect = _mock_ai_response

        from app.main import app

        client = TestClient(app)
        for test_case in TEST_CASES:
            print(f"+ {test_case['name']}")
            resp = client.post(
                "/webhook/whatsapp",
                headers={"X-Webhook-Token": "test_token_12345"},
                json={"remetente": test_case["remetente"], "mensagem": test_case["mensagem"]},
            )
            status_ok = resp.status_code == test_case["expected_status"]
            result = {"name": test_case["name"], "status": resp.status_code, "passed": status_ok}
            results.append(result)
            symbol = "OK" if status_ok else "FALHA"
            print(f"  Status: {resp.status_code} {symbol}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Resposta: {data.get('mensagem', '')[:60]}...")

    return results


def run_validation_checks() -> list[dict[str, object]]:
    print_section("[TESTE 3] Validacoes e Tratamento de Erros")
    validation_results: list[dict[str, object]] = []

    with patch("app.main.DatabaseClient") as mock_database:
        mock_database.return_value = build_mock_db()

        from app.main import app

        client = TestClient(app)
        for test_case in ERROR_TEST_CASES:
            print(f"+ {test_case['name']}")
            resp = client.post("/webhook/whatsapp", headers=test_case["headers"], json=test_case["json"])
            response_json = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            payload_error = response_json.get("dados", {}).get("erro") if isinstance(response_json, dict) else None
            status_ok = resp.status_code == test_case["expected_status"]
            expected_error_code = test_case.get("expected_error_code")
            if expected_error_code is not None:
                status_ok = status_ok and payload_error == expected_error_code
            validation_results.append(
                {
                    "name": test_case["name"],
                    "status": resp.status_code,
                    "payload_error": payload_error,
                    "passed": status_ok,
                }
            )
            if expected_error_code is not None:
                print(
                    "  Status Esperado: "
                    f"{test_case['expected_status']} / erro {expected_error_code}, "
                    f"Recebido: {resp.status_code} / erro {payload_error} {'OK' if status_ok else 'FALHA'}"
                )
            else:
                print(
                    f"  Status Esperado: {test_case['expected_status']}, "
                    f"Recebido: {resp.status_code} {'OK' if status_ok else 'FALHA'}"
                )

    return validation_results