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

from filters import classify, is_zeta_violin
from offer_verifier import inspect_page, verify_offer
from scrapers.reverb import ReverbScraper
from scrapers.shopgoodwill import ShopGoodwillScraper
from keywords import preference_matches


def listing(**fields: object) -> dict:
    return {"id": "fixture", "platform": "Test", "title": "Rare black instrument",
            "description": "", "condition": "Used", "price": "2000 USD",
            "url": "https://example.com/item/123", **fields}


class IdentityRecallTests(unittest.TestCase):
    def test_complete_violin_with_other_brand_or_midi_equipment_is_retained(self) -> None:
        titles = [
            'Zeta JV5 electric violin + Yamaha amplifier',
            'Zeta Jazz Fusion violin + Synthony II MIDI controller',
            'Zeta JV5 with Fender amp', 'Yamaha amplifier + Zeta JV5 violin',
            'Zeta violon avec Yamaha ampli', 'Zeta Geige mit Synthony II',
            'Zeta viool met MIDI controller', 'Zeta violino con Yamaha amplificatore',
            'Zeta 5 strings + Synthony II', 'ZETAバイオリン + YAMAHA amp',
            'Zeta JV5 with original manual and Yamaha amplifier',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(classify(listing(title=title)), '')

    def test_accessory_and_recording_bundles_do_not_borrow_instrument_identity(self) -> None:
        for title in ('Yamaha violin with Zeta pickup', 'Yamaha violin + Zeta JV5 pickup',
                      'Zeta Synthony II MIDI controller with power adapter',
                      'Jean Luc Ponty CD with Zeta violin', 'Case for Zeta JV5 violin',
                      'Zeta JV5 violin + case, instrument not included'):
            with self.subTest(title=title):
                self.assertNotEqual(classify(listing(title=title)), '')

    def test_design_brand_and_furniture_are_not_modernist_violins(self) -> None:
        for title in ('Vintage Studio Zeta Modernist bookshelf', 'Studio Zeta Modernist cabinet',
                      'Zeta Modernist bookshelf', 'Studio Zeta Modernist shelving'):
            self.assertEqual(classify(listing(title=title)), 'noise', title)
        for title in ('Zeta Modernist', 'Zeta Modernist violin', 'Studio Zeta violin 5 strings'):
            self.assertEqual(classify(listing(title=title)), '', title)

    def test_real_gumtree_studio_zeta_table_is_not_a_modernist_violin(self) -> None:
        item = listing(title='Italian Modernist flat bar chrome glass coffee table Studio Zeta Cidue style',
                       description='Vintage chrome and glass coffee table. Studio Zeta design.',
                       url='https://www.gumtree.com/p/coffee-table/italian-modernist-flat-bar-chrome-glass-coffee-table-studio-zeta-cidue-style/1804020876')
        self.assertEqual(classify(item), 'noise')
        self.assertEqual(classify(listing(title='Zeta Modernist')), '')

    def test_furniture_context_does_not_hide_real_instrument(self) -> None:
        for item in (
            listing(title='Zeta JV5 violin photographed on coffee table'),
            listing(title='Zeta Modernist', description='Five strings. Photographed next to my coffee table.'),
            listing(title='Zeta near coffee table', description='Verkaufe Zeta Geige, 5 Saiten.'),
            listing(title='Zeta near coffee table', description='Zeta JV5, 5 strings, used.'),
            listing(title='Zeta Modernist', categories=['Electric Violins']),
        ):
            with self.subTest(item=item):
                self.assertEqual(classify(item), '')

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

    def test_typographic_model_hyphens_preserve_identity_and_priority(self) -> None:
        for dash in ('\u2010','\u2011','\u2012','\u2013','\u2014','\u2212'):
            for model in ('JV5','JV45','JLP5'):
                title = 'Zeta ' + model.replace('JV','JV'+dash).replace('JLP','JLP'+dash)
                with self.subTest(title=title):
                    item = listing(title=title)
                    self.assertEqual(classify(item), '')
                    self.assertTrue(preference_matches(item))
        self.assertNotEqual(classify(listing(title='Yamaha SV\u2011200 electric violin')), '')
        self.assertNotEqual(classify(listing(title='Zeta JV\u20115 hard case')), '')

    def test_native_korean_brand_and_included_case_identify_the_instrument(self) -> None:
        for title in ('제타 전자바이올린','제타전자바이올린 5현',
                      '제타ZETA전자바이올린 판매', 'Zeta 제타 전자바이올린 케이스 포함',
                      '제타 바이올린 케이스포함', '제타 바이올린 케이스와 함께',
                      '케이스 포함 제타 전자바이올린', '제타 전자바이올린만 판매, 케이스 미포함',
                      '제타 전자바이올린 케이스 미포함', '제타 전자바이올린 케이스 별도'):
            with self.subTest(title=title):
                self.assertEqual(classify(listing(title=title,description='케이스에 사용감 있음. 중고. 거이 새거.')), '')
        self.assertEqual(classify(listing(description='4현 제타 전자바이올린입니다. 중고.')), '')
        for accessory in ('픽업', '케이스'):
            self.assertEqual(classify(listing(title='제타 바이올린 '+accessory,description=
                '중고 제타 전자바이올린을 판매합니다. 픽업도 포함되어 있습니다.')), '')

    def test_korean_accessory_intent_and_status_do_not_become_violin_alerts(self) -> None:
        for title in ('제타 바이올린 케이스', '제타바이올린용케이스만 판매',
                      '제타 바이올린 줄만 판매','제타 바이올린 활','제타 바이올린 현 세트',
                      '제타 바이올린 픽업 단품', '제타 바이올린 픽업만 판매합니다',
                      '제타 바이올린 줄 판매합니다', '제타 첼로 4현', '제타 5현 베이스',
                      '제타 바이올린 픽업 오리지널', '오리지널 제타 바이올린 픽업',
                      '제타 바이올린 정품 픽업',
                      '제타 5현 기타', '제타 5현', '제타 바이올린 구합니다'):
            with self.subTest(title=title):
                self.assertNotEqual(classify(listing(title=title)), '')
        for body in ('바이올린 미포함', '바이올린은 미포함', '케이스만 판매합니다.'):
            self.assertNotEqual(classify(listing(title='제타 바이올린',description=body)), '')
        for body in ('케이스만 약간 스크래치 있습니다.', '케이스만 판매하지 않습니다.'):
            self.assertEqual(classify(listing(title='제타 바이올린',description=body)), '')
        self.assertEqual(classify(listing(title='제타 바이올린 픽업 오리지널',description=
            '중고 제타 전자바이올린을 판매합니다. 오리지널 픽업도 포함되어 있습니다.')), '')
        self.assertEqual(classify(listing(title='제타 바이올린 오리지널 픽업 포함')), '')
        for title in ('[판매완료] 제타 전자바이올린', '제타 전자바이올린 거래완료'):
            self.assertEqual(classify(listing(title=title)), 'sold')
        for title in ('판매완료예정 제타 전자바이올린','판매완료 아님 제타 전자바이올린'):
            self.assertEqual(classify(listing(title=title)), '')
        self.assertFalse(is_zeta_violin('제타',categories=['바이올린 케이스']))
        self.assertFalse(is_zeta_violin('제타',categories=['바이올린 줄']))
        self.assertFalse(is_zeta_violin('제타',categories=['바이올린 현 세트']))

    def test_primary_korean_description_replaces_the_discovery_snippet(self) -> None:
        item = listing(title='Rare instrument', source='search', description='Unrelated index summary')
        product = {'@type':'Product','name':'Rare instrument','brand':{'name':'제타'},
                   'model':'JV\u20115','category':'바이올린',
                   'description':'중고 제타 전자바이올린을 판매합니다. 케이스에 사용감 있음.',
                   'offers':{'price':'1000','priceCurrency':'USD','availability':'https://schema.org/InStock'}}
        page = '<main><h1>Rare instrument</h1><script type="application/ld+json">'+json.dumps(product)+'</script></main>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'live')
        self.assertNotIn('Unrelated',item['description'])
        self.assertTrue(item['_primary_content_checked'])
        self.assertEqual(classify(item),'')
        self.assertTrue(preference_matches(item))

    def test_latin_brand_and_model_touching_asian_script_are_recognized(self) -> None:
        for title in ("ZETAバイオリン", "ZETA小提琴", "ZETA바이올린", "JV5バイオリン",
                      "小提琴ZETA", "ＺＥＴＡバイオリン", "JLP5バイオリン"):
            with self.subTest(title=title):
                self.assertEqual(classify(listing(title=title)), "")
        self.assertTrue(preference_matches(listing(title="JLP5バイオリン")))
        self.assertNotEqual(classify(listing(title="YAMAHAバイオリン")), "")
        self.assertNotEqual(classify(listing(title="ZETABETA violin")), "")

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
        self.assertIn("finisaj sunburst / burst menționat",
                      preference_matches(listing(title="Zeta JV5", finish="Tobacco Sunburst")))
        self.assertEqual(classify(listing(title="Zeta JV5 1997 - Black")), "")
        self.assertTrue(preference_matches(listing(title="Zeta JV5 1997 - Black")))
        self.assertEqual(classify(listing(title="Zeta Strados violin",finish="Sunburst")), "")
        self.assertEqual(preference_matches(listing(title="Zeta Strados violin",finish="Sunburst")), [])
        for title in ('제타 스트라도스 전자바이올린','ゼータ ストラドス バイオリン'):
            self.assertEqual(classify(listing(title=title,finish='Sunburst')), '')
            self.assertEqual(preference_matches(listing(title=title,finish='Sunburst')), [])

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

    def test_accessory_condition_is_not_accessory_only_sale_intent(self) -> None:
        for body in ("Case only has minor scuffs.", "The bow only needs rosin.",
                     "Stand not for sale. Violin and case included."):
            with self.subTest(body=body):
                self.assertEqual(classify(listing(title="Zeta JV5 1997", description=body)), "")
        self.assertEqual(classify(listing(title="Zeta", description=
                         "This is a 5-string with a volume pedal included.")), "")
        for body in ("Case only. Instrument not included.", "Violin not for sale.",
                     "Bow only.", "For parts only."):
            with self.subTest(body=body):
                self.assertNotEqual(classify(listing(title="Zeta JV5", description=body)), "")

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
