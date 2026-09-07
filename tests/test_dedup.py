"""
Dedup + FX + date-parse tests.  Run with:  python -m tests.test_dedup
"""

import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dedup import is_duplicate, tokens  # noqa: E402
from fx import to_usd, usd_to  # noqa: E402
from scrapers.marktplaats import MarktplaatsScraper  # noqa: E402


def main() -> int:
    failures = 0

    def check(cond: bool, label: str):
        nonlocal failures
        print(("ok   " if cond else "FAIL ") + label)
        failures += 0 if cond else 1

    recent = [
        {"title": "Zeta Strados Electric 5 String Midi Violin", "price_usd": 2749.0, "platform": "Reverb", "url": "r"},
        {"title": "ZETA Jazz Fusion 5, Jean-Luc Ponty Signature Model, tobacco burst", "price_usd": 6100.0, "platform": "Reverb", "url": "r2"},
    ]
    check(bool(is_duplicate("Zeta Strados 5-string electric violin w/ MIDI", 2700.0, recent)), "same Strados on eBay = duplicate")
    check(not is_duplicate("Zeta Strados 5-string electric violin w/ MIDI", 1500.0, recent), "same title, 45% cheaper = different item")
    check(not is_duplicate("Zeta Jazz Modern 4-string violin", 2995.0, recent), "different model = not duplicate")
    check(bool(is_duplicate("Zeta Jazz Fusion 5 Jean Luc Ponty signature tobacco burst", 6000.0, recent)), "JLP relisted = duplicate")
    check(not is_duplicate("Zeta", 100.0, recent), "too few tokens → never duplicate")
    print("     tokens:", sorted(tokens("Zeta Strados Electric 5 String Midi Violin")))

    eur = to_usd(1000, "EUR")
    check(1000 < eur < 1300, f"1000 EUR ≈ {eur} USD")
    check(abs(usd_to(to_usd(500, "GBP"), "GBP") - 500) < 0.5, "round trip GBP")
    check(to_usd(130000, "JPY") < 1500, f"130000 JPY ≈ {to_usd(130000, 'JPY')} USD")

    d = MarktplaatsScraper._date
    check(d("26 aug 26") == "2026-08-26", f"'26 aug 26' → {d('26 aug 26')}")
    check(d("4 jul 26") == "2026-07-04", f"'4 jul 26' → {d('4 jul 26')}")
    check(len(d("Vandaag")) == 10, "'Vandaag' → today")
    check(len(d("Gisteren")) == 10, "'Gisteren' → yesterday")

    print(f"\n{'ALL OK' if not failures else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
