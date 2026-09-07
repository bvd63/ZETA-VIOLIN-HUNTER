"""
Filter regression tests — run with:  python -m tests.test_filters
Plain asserts, no pytest dependency (Railway image has none).
"""

import os
import sys

os.environ["CONDITION"] = "used"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filters import classify, has_zeta_signal  # noqa: E402

URL = "https://example.com/item/123"

# (listing dict, expected reason) — second-hand-only rules
CONDITION_CASES = [
    ({"title": "Zeta Strados Electric Violin", "condition": "Brand New", "url": URL}, "new_stock"),
    ({"title": "Zeta Strados Electric Violin", "condition": "New", "url": URL}, "new_stock"),
    ({"title": "Zeta Strados Electric Violin", "condition": "New other (see details)", "url": URL}, "new_stock"),
    ({"title": "Zeta Strados Electric Violin", "condition": "Open box", "url": URL}, "new_stock"),
    ({"title": "Zeta Strados Electric Violin", "condition": "B-Stock", "url": URL}, "new_stock"),
    ({"title": "Zeta Strados Electric Violin", "condition": "Mint", "url": URL}, ""),
    ({"title": "Zeta Strados Electric Violin", "condition": "Used", "url": URL}, ""),
    ({"title": "Zeta Strados Electric Violin", "condition": "Very Good", "url": URL}, ""),
    ({"title": "Zeta Strados like new", "condition": "Excellent", "url": URL}, ""),
    ({"title": "Zeta Geige wie neu", "condition": "", "url": URL}, ""),
    ({"title": "Brand New Zeta Strados 5-string", "condition": "", "url": URL}, "new_stock"),
    ({"title": "Zeta Jazz Fusion NIB authorized dealer", "condition": "", "url": URL}, "new_stock"),
    ({"title": "ZETA SV24 Strados Modern violin", "condition": "Brand New", "seller": "electricviolinshop", "url": URL}, "new_stock"),
    ({"title": "ZETA SV24 Strados Modern violin", "condition": "Used", "seller": "electricviolinshop", "url": URL}, "new_stock"),
    ({"title": "Zeta Strados violin", "condition": "", "url": "https://www.zetaviolins.com/products/strados"}, "new_stock"),
    ({"title": "Zeta Strados violin", "condition": "", "seller": "hoosierville-mercantile", "url": URL}, ""),
]

# (title, description, expected reason or "" for accept)
CASES = [
    # --- must PASS (real Zeta violins with sales copy that used to trigger substring bugs)
    ("Zeta Strados Electric Violin 4-string w/ hard shell case", "Comes with original hardshell case.", ""),
    ("Zeta Jazz Fusion 5 string electric violin", "Sold as is, no returns. Great condition.", ""),
    ("ZETA JV44 Electric Violin - Black", "If you are looking for a pro violin, this is it. No defects.", ""),
    ("Zeta Strados Modern violin", "Highly recommended. Recently repaired bridge, plays perfectly.", ""),
    ("Zeta electric violin - clear coat gloss black", "Extended range 5-string. Unwanted gift.", ""),
    ("Zeta Strados electric violin", "Comes with skilled setup, clean.", ""),
    ("Strados electric violin 5 string", "", ""),                       # §4.2 Strados alone
    ("JV44 electric violin with MIDI", "", ""),                         # §4.1 model code alone
    ("SV-24 5-string electric violin", "", ""),                         # hyphenated model code
    ("Jean-Luc Ponty signature 5-string electric violin", "", ""),      # §4.1 artist alone
    ("Violín eléctrico Zeta", "", ""),                                  # ES
    ("Zeta elektrische viool", "", ""),                                 # NL
    ("Zeta Geige elektrisch 5 Saiten", "", ""),                         # DE
    ("ゼータ エレキバイオリン ストラドス", "", ""),                             # JP only
    ("ZETA ジャズフュージョン エレキバイオリン 5弦", "", ""),                       # JP + latin brand
    ("Violino Zeta Acoustic Pro 5 Corde", "vendo causa inutilizzo", ""),  # the Taranto listing
    ("ZETA SV24 Strados Modern violin, gloss white", "", ""),
    ("ZETA Jazz Fusion 5, Jean-Luc Ponty Signature Model, tobacco burst", "", ""),
    # --- must be DROPPED
    ("Arc'teryx Zeta SL jacket", "", "noise"),
    ("Arcteryx Zeta LT hardshell", "", "noise"),
    ("Zeta Synthony II Violin Midi Controller - Zeta ZMC 200", "", "noise"),
    ("Tuki Padded Cover for Zeta AP-12 Violin Combo Amp", "", "noise"),
    ("ZETA JC24 Strados Fusion 4-String Cello", "", "noise"),
    ("SolidGold FX Zeta Drive Jfet overdrive pedal", "", "noise"),
    ("Jean-Luc Ponty - Enigmatic Ocean LP vinyl", "", "noise"),
    ("YAMAHA YEV-104 Violino Elettrico", "", "other_brand"),
    ("Fender Electric Violin FV-1 Black", "compare to zeta violin", "other_brand"),
    ("Yamaha SV-250 silent violin", "", "other_brand"),
    ("WTB: Zeta Strados violin", "", "intent"),
    ("Gezocht: Zeta elektrische viool", "", "intent"),                 # NL wanted
    ("Suche Zeta Geige", "", "intent"),                                # DE wanted
    ("Zeta Jazz Violin Viool 5", "Ik ben gevestigd in Bulgarije", ""),   # Marktplaats real listing
    ("Zeta fiolin 5 strenger", "", ""),                                # NO
    ("Zeta електроскрипка", "", ""),                                   # UA
    ("Wanted Zeta violin", "", "intent"),
    ("Zeta Jazz Fusion violin for parts", "pickup not working", "intent"),
    ("Zeta violin case only", "", "intent"),
    ("SOLD - Zeta Strados violin", "", "sold"),
    ("Zeta electronics catalog 2001", "", "noise"),
    ("Zeta Violin Viool 5 Strings Copy Goede staat ophalen", "", "noise"),   # Marktplaats copy
    ("2000年前後　ゼータ　カタログ　ZETA　エレクトリックヴァイオリン", "", "noise"),   # JP catalog
    ("ZETA エレキバイオリン ジャンク", "", "noise"),                                   # JP junk/for parts
    ("Cantini Earphonic Electric/Midi Violin", "", "other_brand"),
    ("Roland 300 GX", "stage piano", "non_zeta"),
    ("Violino elettrico 5 corde", "made in China", "non_zeta"),
    ("Zeta potential analyzer", "", "noise"),
    ("Zeta Phi Beta sorority pin", "", "noise"),
    ("Zeta amplifier", "", "non_zeta"),
]

SIGNAL_CASES = [
    ("Zeta Synthony Electric Violin Midi Interface", True),
    ("Fender Electric Violin FV-1 Black", False),
    ("Cecilio Electric Violin", False),
    ("ゼータ カタログ", True),
    ("strados", True),
    ("Yamaha SV-200", False),
]


def main() -> int:
    failures = 0
    for title, desc, expected in CASES:
        got = classify({"title": title, "description": desc, "url": URL, "platform": "test"})
        ok = got == expected
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} [{expected or 'PASS':<11}] got={got or 'PASS':<11} {title[:60]}")
    for text, expected in SIGNAL_CASES:
        got = has_zeta_signal(text)
        ok = got == expected
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} has_zeta_signal({text[:40]!r}) = {got}")
    for listing, expected in CONDITION_CASES:
        got = classify({"platform": "test", "description": "", **listing})
        ok = got == expected
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} [{expected or 'PASS':<11}] got={got or 'PASS':<11} "
              f"cond={listing.get('condition', '')!r:<26} seller={listing.get('seller', '')!r} {listing['title'][:40]}")
    total = len(CASES) + len(SIGNAL_CASES) + len(CONDITION_CASES)
    print(f"\n{total - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
