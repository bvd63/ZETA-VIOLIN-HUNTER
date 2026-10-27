"""Run offline regressions and read-only Apify checks before Railway startup."""

import subprocess
import sys


def main():
    for module, arguments in (
        ("tests.test_filters", []),
        ("tests.test_trackers", []),
        ("tests.test_dedup", []),
        ("tests.test_liveness", []),
        ("tests.test_apify", []),
        ("tests.test_craigslist", []),
        ("tests.test_reliability", []),
        ("tests.test_identity_recall", []),
        ("tests.test_coverage_contract", []),
        ("tests.test_marketplace_parsers", []),
        ("tests.test_notifications", []),
        ("tests.test_search_reserves", []),
        ("tests.test_pushover", []),
        ("tests.test_operations", []),
        ("tests.test_search_quality", []),
        ("apify_preview", ["--check"]),
    ):
        print(f"Pre-deploy check: {module}", flush=True)
        subprocess.run([sys.executable, "-m", module, *arguments], check=True)
    print("Pre-deploy checks complete", flush=True)


if __name__ == "__main__":
    main()
