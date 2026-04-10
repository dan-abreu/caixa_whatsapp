from tests.comprehensive.common import TestClient, build_mock_db, patch, print_section


def run_endpoint_checks() -> list[dict[str, object]]:
    print_section("[TESTE 1] Endpoints Basicos")
    endpoint_results: list[dict[str, object]] = []

    with patch("app.main.DatabaseClient") as mock_database:
        mock_database.return_value = build_mock_db()

        from app.main import app

        client = TestClient(app)

        print("+ GET /health")
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        endpoint_results.append({"name": "GET /health", "passed": True})
        print(f"  Status: {resp.status_code} OK")

        print("+ GET /menu")
        resp = client.get("/menu")
        assert resp.status_code == 200
        menu_data = resp.json()
        assert "funcionalidades" in menu_data
        assert len(menu_data["funcionalidades"]) == 5
        endpoint_results.append({"name": "GET /menu", "passed": True})
        print(f"  Status: {resp.status_code}")
        print(f"  Total de opcoes: {len(menu_data['funcionalidades'])} OK")
        for func in menu_data["funcionalidades"]:
            print(f"    - Opcao {func['id']}: {func['nome']}")

    return endpoint_results