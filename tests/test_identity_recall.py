"""Identity regressions from a real archived violin and multilingual sale shapes.

Offline only: no marketplace requests, paid discovery or notifications.
"""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

os.environ["CONDITION"] = "used"
TEMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(TEMP.name) / "bot.db")

from filters import classify
from offer_verifier import inspect_page, verify_offer
from scrapers.reverb import ReverbScraper
from scrapers.shopgoodwill import ShopGoodwillScraper
from keywords import preference_matches


def listing(**fields: object) -> dict:
    return {"id": "fixture", "platform": "Test", "title": "Rare black instrument",
            "description": "", "condition": "Used", "price": "2000 USD",
            "url": "https://example.com/item/123", **fields}


class IdentityRecallTests(unittest.TestCase):
    def test_real_archived_jv5_without_instrument_word(self) -> None:
        # Historical positive identity, NOT an active sale or notification.
        # https://reverb.com/item/97447559-zeta-jv5-1997-black
        item = listing(title="Zeta JV5 1997 - Black", description=
                       "Rare 1997 model JV5. Hand made. With original protec case. Rare find.")
        self.assertEqual(classify(item), "")

    def test_language_does_not_require_english_title(self) -> None:
        for word in ("violon", "viool", "Geige", "violino", "violín", "skrzypce",
                     "fiolin", "viulu", "hegedű", "housle", "скрипка", "цигулка",
                     "小提琴", "바이올린", "βιολί", "keman", "バイオリン"):
            with self.subTest(word=word):
                self.assertEqual(classify(listing(title=f"Zeta {word}")), "")
                self.assertEqual(classify(listing(description=f"Zeta {word}, 1993.")), "")

    def test_model_and_instrument_can_be_in_different_fields(self) -> None:
        self.assertEqual(classify(listing(title="JV44 1993", description="Violon's MIDI output works.")), "")
        self.assertEqual(classify(listing(title="Rare black instrument", description="Zeta JV5, 1997.")), "")
        self.assertEqual(classify(listing(title="ＺＥＴＡ ＪＶ５ 1997")), "")

    def test_configuration_can_identify_instrument_without_literal_name(self) -> None:
        for title in ("Zeta 4-strings 1993", "Zeta 5 strings MIDI", "Zeta 5 cordes",
                      "Zeta 4 Saiten", "ゼータ 5弦"):
            with self.subTest(title=title):
                self.assertEqual(classify(listing(title=title)), "")

    def test_jlp_and_burst_priorities_do_not_exclude_other_zeta_violins(self) -> None:
        for title in ("Zeta JLP", "Zeta JLP4", "Zeta JLP5", "Zeta Jean Luc Ponty"):
            with self.subTest(title=title):
                self.assertEqual(classify(listing(title=title)), "")
                self.assertTrue(preference_matches(listing(title=title)))
        self.assertEqual(preference_matches(listing(title="Zeta JV5", finish="Tobacco Sunburst")),
                         ["finisaj sunburst / burst menționat"])
        self.assertEqual(classify(listing(title="Zeta JV5 1997 - Black")), "")
        self.assertEqual(preference_matches(listing(title="Zeta JV5 1997 - Black")), [])

    def test_primary_product_metadata_fills_missing_title_words(self) -> None:
        self.assertEqual(classify(listing(brand="Zeta", categories=["Band and Orchestra / Violins"])), "")
        self.assertEqual(classify(listing(brand="Zeta", model="JV5")), "")
        self.assertEqual(classify(listing(title="JV44", categories=["Violins"])), "")
        self.assertEqual(classify(listing(title="Rare black instrument with case", brand="Zeta", model="JV5")), "")

    def test_accessories_and_unrelated_codes_do_not_gain_identity(self) -> None:
        examples = [
            listing(title="JV44: The Galland Circus"),
            listing(title="Fw190 D-9 JV44 1/72 scale"),
            listing(title="Zeta JV5 hard case"),
            listing(title="Zeta 5-string string set"),
            listing(title="Zeta 5-string MIDI controller"),
            listing(title="Rare black case", brand="Zeta", categories=["Violin Cases"]),
            listing(title="Zeta", categories=["Violin Strings"]),
            listing(title="MIDI gear", description="ZETA Synthony II for violin."),
            listing(title="Zeta amplifier"),
            listing(title="Jean-Luc Ponty violin CD"),
        ]
        for item in examples:
            with self.subTest(title=item["title"]):
                self.assertNotEqual(classify(item), "")

    def test_real_violin_body_can_mention_included_accessories(self) -> None:
        self.assertEqual(classify(listing(title="Electric violin 5 string", description=
                         "It is a Zeta Strados, comes with a Boss pedal and original case.")), "")
        self.assertEqual(classify(listing(title="Rare instrument", description=
                         "Zeta JV5. Includes a volume pedal and a copy of the receipt.")), "")
        self.assertEqual(classify(listing(title="Electric violin", description=
                         "A Zeta Strados with a volume pedal and a copy of the receipt.")), "")
        self.assertEqual(classify(listing(title="Electric violin with case", description="Zeta JV5, 1997.")), "")
        self.assertNotEqual(classify(listing(title="Electric violin", description=
                            "Zeta Synthony II, violin not included.")), "")

    def test_reverb_search_keeps_primary_metadata(self) -> None:
        item = {"id": 123, "title": "Rare black instrument", "make": "Zeta", "model": "JV5",
                "categories": [{"full_name": "Band and Orchestra / Violins"}],
                "condition": {"display_name": "Used"}, "state": {"slug": "live"},
                "_links": {"web": {"href": "https://reverb.com/item/123-test"}}}
        scraper = ReverbScraper()
        original = scraper.make_client
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"listings": [item] if request.url.params["page"] == "1" else []})
        with patch.object(scraper, "make_client", side_effect=lambda **kw: original(
                transport=httpx.MockTransport(handler), **kw)):
            items = asyncio.run(scraper.search())
        self.assertEqual(len(items), 1)
        self.assertEqual(classify(items[0]), "")

    def test_shopgoodwill_description_tail_reaches_classifier(self) -> None:
        description = "Original finish, good working condition. " * 20 + "Zeta JV5."
        item = {"itemId": 123, "title": "Electric violin", "description": description,
                "currentPrice": 1000}
        scraper = ShopGoodwillScraper()
        original = scraper.make_client
        with patch.object(scraper, "make_client", side_effect=lambda **kw: original(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
                    "searchResults": {"items": [item]}})), **kw)):
            items = asyncio.run(scraper.search())
        self.assertEqual(len(items), 1)
        self.assertEqual(classify(items[0]), "")
        self.assertEqual(items[0]["description"], description)

    def test_structured_offer_keeps_metadata_and_complete_description(self) -> None:
        item = listing(source="search")
        description = "Good condition. " * 120 + "Made by Zeta, JV5."
        product = {"@type": "Product", "name": item["title"], "brand": {"@type": "Brand", "name": "Zeta"},
                   "model": "JV5", "category": "Violins", "description": description,
                   "offers": {"price": 2000, "priceCurrency": "USD"}}
        page = '<main><h1>Rare black instrument</h1><script type="application/ld+json">' + json.dumps(product) + '</script></main>'
        self.assertEqual(inspect_page(item, page, 200, item["url"])[0], "live")
        self.assertEqual(item["description"], description)
        self.assertEqual(classify(item), "")

    def test_recommended_product_cannot_supply_missing_identity(self) -> None:
        item = listing(source="search", title="Black hard case")
        products = [{"@type": "Product", "name": "Black hard case", "url": item["url"],
                     "offers": {"price": 100}},
                    {"@type": "Product", "name": "Zeta JV5", "brand": "Zeta", "category": "Violins",
                     "url": "https://example.com/item/999", "offers": {"price": 2000}}]
        page = '<main><h1>Black hard case</h1><script type="application/ld+json">' + json.dumps(products) + '</script></main>'
        self.assertEqual(inspect_page(item, page, 200, item["url"])[0], "live")
        self.assertNotEqual(classify(item), "")

    def test_item_description_is_read_without_jsonld(self) -> None:
        item = listing(source="search", title="Black instrument")
        page = '<main><h1>Black instrument</h1><div itemprop="description">Zeta JV5 1997. With original case.</div><button>Buy now</button></main><footer>Zeta violin case only</footer>'
        self.assertEqual(inspect_page(item, page, 200, item["url"])[0], "live")
        self.assertEqual(classify(item), "")
        self.assertNotIn("case only", item["description"])

    def test_unrelated_sidebar_description_cannot_identify_generic_item(self) -> None:
        item = listing(source="search", title="Black instrument")
        page = '<main><h1>Black instrument</h1><aside><div itemprop="description">Zeta JV5</div></aside><p>Unbranded instrument for sale, $100.</p></main>'
        self.assertEqual(inspect_page(item, page, 200, item["url"])[0], "live")
        self.assertNotEqual(classify(item), "")

    def test_reverb_verifier_keeps_description_tail_and_metadata(self) -> None:
        item = listing(source="search", url="https://reverb.com/item/123-test")
        description = "Good condition. " * 120 + "Zeta JV5."
        payload = {"state": {"slug": "live"}, "title": item["title"], "make": "Zeta", "model": "JV5",
                   "categories": [{"full_name": "Violins"}], "description": description}
        async def run() -> tuple:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, json=payload))) as client:
                with patch("offer_verifier.public_dns", AsyncMock(return_value=True)):
                    return await verify_offer(item, client)
        self.assertEqual(asyncio.run(run())[0], "live")
        self.assertEqual(item["description"], description)
        self.assertEqual(classify(item), "")


if __name__ == "__main__":
    unittest.main()
