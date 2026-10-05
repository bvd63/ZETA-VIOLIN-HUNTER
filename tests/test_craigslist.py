"""Offline discovery regressions: full bodies, mixed sales, expiry and API budgets."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from bs4 import BeautifulSoup

TEMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(TEMP.name) / "bot.db")
from config import Config
import database
database.DB_PATH = os.environ["DB_PATH"]
from filters import classify
from scrapers.craigslist import CraigslistScraper, MAX_ENRICH
from scrapers.brave import BraveScraper
from scrapers.google import GoogleScraper, US_PRIORITY_QUERIES, GLOBAL_QUERIES, SITE_GROUPS, MATRIX_KEYWORDS
from offer_verifier import inspect_page
from notifier import TelegramNotifier


def listing(title: str = "Zeta electric violin", **kwargs) -> dict:
    return {"id": "fixture", "title": title, "platform": "Craigslist (phoenix)",
            "price": "$2000", "url": "https://www.craigslist.org/view/d/violin/token", **kwargs}


def compact(title: str, number: int = 1) -> list:
    return [number, 0, 1, 2000, "0:0:0~0~0", "", [13, f"token{number}"],
            [6, "fixture"], title]


def page(body: str, dates: str = "", extra: str = "") -> str:
    return f'<html><p class="attrgroup">{dates}</p>{extra}<section id="postingbody">{body}</section></html>'


class CraigslistTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(Config, FILTER_TEXT_YEARS=False, CONDITION="used",
                                     MIN_PRICE=0, MAX_PRICE=99999, GOOGLE_QUERIES_PER_RUN=48,
                                     CRAIGSLIST_MAX_US_CITIES=0, CRAIGSLIST_COUNTRIES="US,CA")
        self.config.start()
        self.addCleanup(self.config.stop)

    def search(self, titles: list, pages: dict, cap: int = MAX_ENRICH) -> tuple:
        requests = []
        scraper = CraigslistScraper()

        def handler(request):
            requests.append(request)
            if request.url.path == "/Areas":
                return httpx.Response(200, json=[{"AreaID": 18, "Hostname": "phoenix", "Country": "US"}])
            if request.url.path.endswith("/search/full"):
                # Generic estate title discovered by a brand in its description.
                selected = titles if request.url.params["query"] == scraper.plan_queries()[0][0] else []
                return httpx.Response(200, json={"data": {"decode": {"minPostingId": 100},
                                                       "items": [compact(t, i+1) for i, t in enumerate(selected)]}})
            token = request.url.path.rsplit("/", 1)[-1]
            status, text = pages.get(token, (200, page("Used electric violin made by Zeta.")))
            return httpx.Response(status, text=text)

        original = scraper.make_client
        with patch.object(scraper, "make_client", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)), \
                patch("scrapers.craigslist.MAX_ENRICH", cap):
            results = asyncio.run(scraper.search())
        return scraper, results, requests

    def test_same_four_searches_span_all_sale_categories(self):
        scraper, _, requests = self.search([], {})
        searches = [r for r in requests if r.url.path.endswith("/search/full")]
        self.assertEqual(len(searches), 4)
        self.assertEqual([r.url.params["searchPath"] for r in searches].count("sss"), 3)
        self.assertEqual(scraper.coverage_stats["areas"], 1)
        self.assertIn("violin", searches[0].url.params["query"])

    def test_unbranded_title_keeps_zeta_after_character_600(self):
        body = "Instrument details. " * 60 + "This electric violin was made by Zeta Music Systems."
        _, found, _ = self.search(["Electric violin 5 strings"], {"token1": (200, page(body))})
        self.assertEqual(len(found), 1)
        self.assertIn("Zeta Music Systems", found[0]["description"])
        self.assertGreater(len(found[0]["description"]), 600)

    def test_generic_estate_title_and_unrelated_goods_keep_explicit_zeta(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
        body = "GLASSWARE, JACKETS, SKIS, PEDALS, ELECTRICAL ZETA VIOLIN, FURNITURE"
        _, found, _ = self.search(["ESTATE SALE"], {"token1": (200, page(body, f"dates: {future}"))})
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["estate_sale"])
        self.assertEqual(found[0]["sale_end"], future)

    def test_past_estate_dates_with_http_200_are_withheld(self):
        scraper, found, _ = self.search(["ESTATE SALE"], {"token1": (200, page("Zeta electric violin", "dates: 2020-08-20 2020-08-22"))})
        self.assertEqual(found, [])
        self.assertEqual(scraper.coverage_stats["detail_dead"], 1)

    def test_estate_post_without_sale_dates_is_unverified(self):
        scraper, found, _ = self.search(["ESTATE SALE"], {"token1": (200, page("Zeta electric violin"))})
        self.assertEqual(found, [])
        self.assertEqual(scraper.coverage_stats["detail_unverified"], 1)

    def test_bad_or_expired_detail_cannot_become_alert(self):
        for status, body in ((403, "blocked"), (200, "This posting has expired."), (200, "captcha")):
            with self.subTest(status=status, body=body):
                _, found, _ = self.search(["Zeta Strados electric violin"], {"token1": (status, body)})
                self.assertEqual(found, [])

    def test_detail_cap_applies_even_to_conclusive_titles(self):
        scraper, found, requests = self.search([f"Zeta Strados electric violin {i}" for i in range(5)], {}, cap=2)
        self.assertEqual(len(found), 2)
        self.assertEqual(scraper.coverage_stats["detail_deferred"], 3)
        self.assertEqual(sum("/view/d/" in r.url.path for r in requests), 2)

    def test_case_and_synthony_titles_do_not_buy_detail_requests(self):
        _, found, requests = self.search(["Zeta violin case", "Zeta Synthony Electric Violin Midi Interface - Trade?"], {})
        self.assertEqual(found, [])
        self.assertEqual(sum("/view/d/" in r.url.path for r in requests), 0)

    def test_local_midnight_uses_posting_offset(self):
        soup = BeautifulSoup('<time class="date timeago" datetime="2026-10-01T12:00:00-0700"></time>', "lxml")
        instant = datetime(2026, 10, 6, 1, tzinfo=timezone.utc)
        with patch("scrapers.craigslist.datetime", wraps=datetime) as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            self.assertEqual(CraigslistScraper._local_today(soup).isoformat(), "2026-10-05")

    def test_posting_date_is_not_an_event_date(self):
        soup = BeautifulSoup('<p class="attrgroup">posted: 2026-10-01</p><time class="date" datetime="2026-10-01T12:00:00Z"></time>', "lxml")
        self.assertEqual(CraigslistScraper._sale_dates(soup), [])

    def test_mixed_inventory_does_not_join_unrelated_zeta_and_violin(self):
        for body in ("Zeta jacket, Yamaha violin", "Zeta phi beta violin poster",
                     "Zeta electric violin case, skis", "manual for Zeta electric violin",
                     "Zeta guitar and violin", "Zeta Synthony MIDI interface, violin"):
            with self.subTest(body=body):
                self.assertNotEqual(classify(listing("ESTATE SALE", description=body)), "")

    def test_inventory_evidence_does_not_override_absence_or_non_sale(self):
        for suffix in ("; violin is not included", "; not for sale"):
            self.assertNotEqual(classify(listing("ESTATE SALE", description="Zeta electric violin" + suffix)), "")

    def test_search_engine_craigslist_estate_also_checks_event_dates(self):
        item = listing("ESTATE SALE", source="search", description="Zeta electric violin")
        text = page("Zeta electric violin", "dates: 2020-08-22")
        self.assertEqual(inspect_page(item, text, 200, item["url"])[0], "dead")

    def test_estate_alert_exposes_instrument_and_date(self):
        item = listing("ESTATE SALE", description="Furniture, Zeta electric violin with case, skis",
                       estate_sale=True, sale_end="2099-10-06")
        message = TelegramNotifier("", "")._format_listing(1, item)
        self.assertIn("În inventar: Zeta electric violin with case", message)
        self.assertIn("2099-10-06", message)
        self.assertIn("confirmă ridicarea sau expedierea", message)

    def test_us_web_priority_does_not_expand_request_budget(self):
        with patch.object(Config, "BRAVE_QUERIES_PER_RUN", 16):
            for scraper in (GoogleScraper("fake", "fake"), BraveScraper()):
                plan, _ = scraper.plan_queries(0)
                self.assertLessEqual(len(plan), 48 if isinstance(scraper, GoogleScraper) else 16)
                for query in US_PRIORITY_QUERIES:
                    self.assertIn(query, [q for q, _ in plan])
        all_queries = GLOBAL_QUERIES + [f"({k}) ({g})" for k in MATRIX_KEYWORDS for g in SITE_GROUPS]
        self.assertLessEqual(max(map(len, all_queries)), 600)
        self.assertLessEqual(max(len(q.split()) for q in all_queries), 75)

    def test_brave_extra_snippet_brand_is_retained_without_spell_correction(self):
        scraper = BraveScraper()
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"web": {"results": [{"url": "https://example.com/item/violin",
                "title": "Electric violin", "description": "Used musical instrument",
                "extra_snippets": ["Owned since 2001. Zeta Strados 5-string electric violin."]}]}})

        original = scraper.make_client
        with patch.object(Config, "BRAVE_API_KEY", "fake"), patch.object(Config, "BRAVE_QUERIES_PER_RUN", 1), \
                patch.object(scraper, "_should_run", return_value=True), patch.object(scraper, "_kv_get", return_value=""), \
                patch.object(scraper, "_kv_set"), patch("scrapers.brave.reserve_request", return_value=True), \
                patch("scrapers.brave.asyncio.sleep"), \
                patch.object(scraper, "make_client", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            found = asyncio.run(scraper.search())
        self.assertIn("Zeta Strados", found[0]["description"])
        self.assertEqual(requests[0].url.params["spellcheck"], "false")
        self.assertEqual(requests[0].url.params["extra_snippets"], "true")


if __name__ == "__main__":
    unittest.main()
