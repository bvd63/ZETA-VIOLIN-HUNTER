"""Run offline regressions and read-only Apify checks before Railway startup."""

import subprocess
import sys


def main():
    for module, arguments in (
        ("tests.test_filters", []),
        ("tests.test_apify", []),
        ("apify_preview", ["--check"]),
    ):
        print(f"Pre-deploy check: {module}", flush=True)
        subprocess.run([sys.executable, "-m", module, *arguments], check=True)
    print("Pre-deploy checks complete", flush=True)


if __name__ == "__main__":
    main()
