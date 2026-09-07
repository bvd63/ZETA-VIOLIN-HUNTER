"""
Tracker tests — price-drop detection and watchdog streaks on a temp SQLite DB.
Run with:  python -m tests.test_trackers
"""

import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from price_tracker import PriceTracker  # noqa: E402
from status_tracker import StatusTracker  # noqa: E402


def main() -> int:
    failures = 0

    def check(cond: bool, label: str):
        nonlocal failures
        print(("ok   " if cond else "FAIL ") + label)
        failures += 0 if cond else 1

    pt = PriceTracker()
    listing = {"id": "abc", "title": "Zeta Strados", "price": "3000.00 USD", "platform": "Reverb", "url": "https://x/1"}
    ctx = pt.record_listing(listing)
    check(ctx["price_usd"] == 3000.0, "record_listing parses USD")
    check(pt.update_price({**listing, "price": "2900.00 USD"}) == {}, "3% drop is ignored")
    drop = pt.update_price({**listing, "price": "2000.00 USD"})
    check(bool(drop) and drop["drop_pct"] >= 30, f"31% drop detected: {drop}")
    check(pt.update_price({**listing, "price": "2000.00 USD"}) == {}, "same price again → no alert")
    check(pt.update_price({**listing, "price": "2500.00 USD"}) == {}, "price increase → no alert")
    usd, cur = pt._parse_price("2000 €")
    check(cur == "EUR" and usd == 2160.0, f"EUR parse {usd} {cur}")
    usd, cur = pt._parse_price("¥130000")
    check(cur == "JPY", f"JPY parse {usd} {cur}")
    from price_tracker import parse_amount
    check(parse_amount("€ 6.900") == 6900.0, "'€ 6.900' is 6900 (EU thousands)")
    check(parse_amount("2,749.00 USD") == 2749.0, "'2,749.00' is 2749")
    check(parse_amount("2.000,00 €") == 2000.0, "'2.000,00' is 2000")
    check(parse_amount("1 500 SEK") == 1500.0, "'1 500' is 1500")
    check(parse_amount("$1,500") == 1500.0, "'$1,500' is 1500")
    check(parse_amount("6,90 €") == 6.9, "'6,90' is 6.9")
    check(parse_amount("See listing") is None, "no number → None")
    pt.close()

    st = StatusTracker()
    st.start_cycle()
    for _ in range(3):
        st.record_scraper("Reverb", raw=0, new=0)
    st.record_scraper("Craigslist", raw=1400, new=0)
    st.record_scraper("eBay", raw=0, new=0, error="ERROR")
    st.record_scraper("eBay", raw=0, new=0, error="ERROR")
    streaks = st.get_streaks()
    check(streaks["Reverb"]["zero"] == 3, f"Reverb zero streak = {streaks['Reverb']}")
    check(streaks["Craigslist"]["zero"] == 0, f"Craigslist zero streak = {streaks['Craigslist']}")
    check(streaks["eBay"]["error"] == 2, f"eBay error streak = {streaks['eBay']}")
    st.record_scraper("Reverb", raw=12, new=1)
    check(st.get_streaks()["Reverb"]["zero"] == 0, "streak resets after a non-zero cycle")
    st.end_cycle(total_sent=1)
    status = st.get_status()
    check(status.get("last_cycle", {}).get("sent_to_telegram") == 1, "status has last cycle")
    st.close()

    print(f"\n{'ALL OK' if not failures else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
