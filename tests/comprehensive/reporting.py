from tests.comprehensive.guardrail_checks import FEATURES_TESTED
from tests.comprehensive.webhook_checks import ERROR_TEST_CASES, TEST_CASES


def build_report(
    *,
    timestamp: str,
    endpoint_results: list[dict[str, object]],
    webhook_results: list[dict[str, object]],
    validation_results: list[dict[str, object]],
    sanitization_summary: dict[str, int],
    currency_summary: dict[str, int],
) -> dict[str, object]:
    total_tests = 2 + len(TEST_CASES) + len(ERROR_TEST_CASES) + sanitization_summary["total"] + currency_summary["total"]
    all_ok = all(
        [
            all(item["passed"] for item in endpoint_results),
            all(item["passed"] for item in webhook_results),
            all(item["passed"] for item in validation_results),
            currency_summary["passed"] == currency_summary["total"],
            sanitization_summary["passed"] == sanitization_summary["total"],
        ]
    )
    return {
        "timestamp": timestamp,
        "testes_executados": {
            "endpoints_basicos": 2,
            "webhooks_intencoes": len(TEST_CASES),
            "validacoes_erros": len(ERROR_TEST_CASES),
            "guardrails_ia": sanitization_summary["total"],
            "moedas_suportadas": currency_summary["total"],
            "features_principais": len(FEATURES_TESTED),
        },
        "resumo": {
            "total_testes": total_tests,
            "endpoints_basicos_ok": all(item["passed"] for item in endpoint_results),
            "webhooks_ok": all(item["passed"] for item in webhook_results),
            "validacoes_ok": all(item["passed"] for item in validation_results),
            "guardrails_ok": sanitization_summary["passed"] == sanitization_summary["total"],
            "moedas_ok": currency_summary["passed"] == currency_summary["total"],
        },
        "cobertura": {
            "menu_options": "5/5 opcoes",
            "intencoes": ["consultar_relatorio", "registrar_operacao", "atualizar_taxa", "conversar"],
            "moedas": ["ouro", "usd", "eur", "srd", "brl"],
            "validacoes": ["token", "mensagem", "remetente", "payload_ia"],
        },
        "status": "OK - PRONTO PARA PRODUCAO" if all_ok else "REVISAR",
    }


def print_report(report: dict[str, object]) -> None:
    resumo = report["resumo"]
    cobertura = report["cobertura"]

    print("\n\n" + "=" * 80)
    print("RELATORIO FINAL")
    print("=" * 80)
    print("\nRESUMO EXECUTIVO:")
    print(f"  - Total de testes: {resumo['total_testes']}")
    print(f"  - Endpoints basicos: {'OK' if resumo['endpoints_basicos_ok'] else 'FALHA'}")
    print(f"  - Webhooks: {'OK' if resumo['webhooks_ok'] else 'FALHA'}")
    print(f"  - Validacoes: {'OK' if resumo['validacoes_ok'] else 'FALHA'}")
    print(f"  - Guardrails IA: {'OK' if resumo['guardrails_ok'] else 'FALHA'}")
    print(f"  - Moedas: {'OK' if resumo['moedas_ok'] else 'FALHA'}")

    print("\nCOBERTURA DE TESTES:")
    for key, value in cobertura.items():
        if isinstance(value, list):
            print(f"  - {key}: {', '.join(value)}")
        else:
            print(f"  - {key}: {value}")

    print(f"\nSTATUS: {report['status']}")