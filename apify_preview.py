"""Isolated API preview. Never imports main or Telegram, and never marks alerts.

python -m apify_preview --reference-run RUN_ID  (reads an existing dataset only)
python -m apify_preview --search                (bounded, durable collection)
"""

import argparse
import asyncio
import json
import logging
import sys

from config import Config
from filters import classify
from scrapers.apify_facebook import ApifyFacebookScraper, normalize, available


async def preview(run_id: str, search: bool, check: bool = False) -> dict:
    scraper = ApifyFacebookScraper()
    scraper.reset_health()
    if check and (not Config.APIFY_TOKEN or not Config.APIFY_ENABLED):
        return {"configured": False, "mode": "disabled", "errors": ""}
    if not scraper.is_configured():
        raise RuntimeError("Apify is not configured")
    if search:
        listings = await scraper.search()
    else:
        async with scraper.make_client(timeout=30, follow_redirects=False,
                headers={"Authorization": "Bearer " + Config.APIFY_TOKEN}) as client:
            if check:
                for task in (Config.APIFY_DISCOVERY_TASK_ID, Config.APIFY_DETAILS_TASK_ID):
                    response = await scraper._request(client, "GET", f"/actor-tasks/{task}")
                    if response["data"].get("id") != task:
                        raise RuntimeError("Configured Apify task is not accessible")
            rows = await scraper.read_run(client, run_id) if run_id else []
        listings = [normalize(row) for row in rows]
        listings = [item for item in listings if item and available(item) and not classify(item)]
    return {
        "configured": True, "mode": "search" if search else "api-check" if check else "existing-run-read",
        "tasks_verified": 2 if check else 0,
        "monthly_reservation_limit_usd": Config.APIFY_MONTHLY_LIMIT_USD,
        "telegram": "not imported or called", "errors": scraper.health_error(),
        "verified_count": len(listings),
        "listings": [{key: item.get(key) for key in ("title", "price", "location", "url")} for item in listings],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reference-run")
    group.add_argument("--search", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    result = asyncio.run(preview(args.reference_run or (Config.APIFY_VALIDATION_RUN_ID if args.check else ""), args.search, args.check))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
