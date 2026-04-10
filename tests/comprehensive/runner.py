from tests.comprehensive.common import print_footer, print_header, utc_now_iso
from tests.comprehensive.endpoint_checks import run_endpoint_checks
from tests.comprehensive.guardrail_checks import print_feature_list, run_currency_checks, run_sanitization_checks
from tests.comprehensive.reporting import build_report, print_report
from tests.comprehensive.webhook_checks import run_validation_checks, run_webhook_checks


def run_comprehensive_test() -> dict[str, object]:
    print_header()

    endpoint_results = run_endpoint_checks()
    webhook_results = run_webhook_checks()
    validation_results = run_validation_checks()
    sanitization_summary = run_sanitization_checks()
    currency_summary = run_currency_checks()
    print_feature_list()

    report = build_report(
        timestamp=utc_now_iso(),
        endpoint_results=endpoint_results,
        webhook_results=webhook_results,
        validation_results=validation_results,
        sanitization_summary=sanitization_summary,
        currency_summary=currency_summary,
    )
    print_report(report)
    print_footer()
    return report


if __name__ == "__main__":
    run_comprehensive_test()