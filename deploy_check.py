"""Run offline regressions and read-only Apify checks before Railway startup."""

import subprocess
import sys
import os
import tempfile
from pathlib import Path


def main():
    checks = (
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
        ("tests.test_auction_recovery", []),
        ("tests.test_duckduckgo", []),
        ("tests.test_public_node_fetch", []),
        ("tests.test_source_review", []),
        ("tests.test_willhaben_reader", []),
        ("tests.test_willhaben_reader_contract", []),
        ("tests.test_reverb_api", []),
        ("tests.test_reverb_discovery", []),
        ("tests.test_reverb_workflow", []),
        ("tests.test_native_recall", []),
        ("tests.test_native_cards", []),
        ("tests.test_native_asia_cards", []),
        ("tests.test_apify_compare", []),
        ("tests.test_facebook_unknown", []),
        ("apify_preview", ["--check"]),
    )
    # Railway injects the live DB_PATH into pre-deploy commands. Every child
    # gets an isolated file even if a future test forgets its own fixture.
    with tempfile.TemporaryDirectory(prefix='zeta-predeploy-') as directory:
        for module, arguments in checks:
            print(f"Pre-deploy check: {module}", flush=True)
            env = {**os.environ, 'DB_PATH':str(Path(directory) / (module + '.db'))}
            subprocess.run([sys.executable, "-m", module, *arguments], check=True, env=env)
    print("Pre-deploy checks complete", flush=True)


if __name__ == "__main__":
    main()
