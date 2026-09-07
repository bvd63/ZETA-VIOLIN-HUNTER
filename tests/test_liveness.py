"""
Liveness tests — marker detection (offline) + optional live checks against
known dead/alive URLs (skipped when the network is unavailable).
Run with:  python -m tests.test_liveness
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liveness import looks_dead, is_live  # noqa: E402

U = "https://example.com/item/123-zeta-strados"


def main() -> int:
    failures = 0

    def check(cond: bool, label: str):
        nonlocal failures
        print(("ok   " if cond else "FAIL ") + label)
        failures += 0 if cond else 1

    check(looks_dead(404, U, U, "") != "", "404 is dead")
    check(looks_dead(410, U, U, "") != "", "410 is dead")
    check(looks_dead(403, U, U, "blocked") == "", "403 (blocked) is NOT declared dead")
    check(looks_dead(200, U, "https://example.com/", "home") != "", "redirect to homepage is dead")
    check(looks_dead(200, U, "https://example.com/search?q=zeta", "x") != "", "redirect to search is dead")
    check(looks_dead(200, U, U, "<h1>This listing has ended</h1>") != "", "Reverb/eBay 'listing has ended'")
    check(looks_dead(200, U, U, "This posting has expired.") != "", "Craigslist expired")
    check(looks_dead(200, U, U, "Annuncio non più disponibile") != "", "Subito IT dead")
    check(looks_dead(200, U, U, "Diese Anzeige ist nicht mehr verfügbar") != "", "Kleinanzeigen DE dead")
    check(looks_dead(200, U, U, "Zeta Strados 5-string. Add to cart. Ships free.") == "", "live shop page passes")
    check(looks_dead(200, U, U, "Great violin, sold as is, no returns. Buy It Now") == "", "'sold as is' is not dead")
    check(looks_dead(200, U, U, "Currently sold out in blue. Add to cart") == "", "'sold out' variant on live page passes")

    async def live_checks():
        import httpx
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            # Craigslist posting from March 2026 — long expired
            alive, why = await is_live("https://sfbay.craigslist.org/eby/msg/d/oakland-zeta-strados-modern-electric/7901771710.html", c)
            print(f"     craigslist old posting → alive={alive} ({why})")
            check(alive is False, "old Craigslist posting detected as dead")
            # Reverb listing verified live today
            alive, why = await is_live("https://reverb.com/item/2673122-zeta-strados-electric-5-string-midi-violin", c)
            print(f"     reverb live listing → alive={alive} ({why})")
            check(alive is True, "live Reverb listing detected as live")
    try:
        asyncio.run(live_checks())
    except Exception as e:
        print(f"skip live checks (network): {e}")

    print(f"\n{'ALL OK' if not failures else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
