#!/usr/bin/env python3

import json

from tests.comprehensive.runner import run_comprehensive_test


if __name__ == "__main__":
    report = run_comprehensive_test()
    with open("test_report.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print("\nRelatorio salvo em: test_report.json")
