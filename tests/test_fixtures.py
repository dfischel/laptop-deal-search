"""Parsers against real saved responses from each source (tests/fixtures/, saved 2026-09-26).

Two kinds of checks:
- Structure: counts, required fields, health check passes. These should survive a
  `python tools/refresh_fixtures.py`. If they fail on fresh pages, the site changed its markup.
- Pinned values: exact titles and prices of specific listings. These pin the parser to known-correct
  output. After refreshing the fixtures, update them from the new files.
"""

import unittest
from pathlib import Path

from monitor.config import load_config
from monitor.sources import check_health, parse_feed, parse_newegg_page
from monitor.specs import evaluate

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_config()


def read_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def read_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class SlickdealsFixture(unittest.TestCase):
    def setUp(self):
        self.listings = parse_feed("slickdeals", read_bytes("slickdeals.xml"))
        self.by_id = {listing.id: listing for listing in self.listings}

    def test_structure(self):
        self.assertEqual(len(self.listings), 25)
        for listing in self.listings:
            self.assertTrue(listing.id.startswith("slickdeals:thread-"), listing.id)
            self.assertTrue(listing.url.startswith("https://slickdeals.net/f/"), listing.url)
            self.assertTrue(listing.title)
            self.assertTrue(listing.text, "description text is used to fill in missing specs")
        check_health("slickdeals", self.listings, CONFIG["sources"]["slickdeals"])

    def test_pinned_laptop_listing(self):
        listing = self.by_id["slickdeals:thread-20059410"]
        self.assertEqual(listing.title, '16" MSI Crosshair Laptop: QHD+ 240Hz, i7-14650HX, RTX 5070, 16GB DDR5, 512GB SSD $1299 + Free S&H')
        self.assertEqual(listing.price, 1299.0)
        verdict = evaluate(listing, CONFIG["criteria"], CONFIG["costco"], True)
        self.assertEqual(verdict.specs["ram_gb"], 16)  # not confused by "RTX 5070"
        self.assertEqual(verdict.specs["storage_gb"], 512)
        self.assertEqual(verdict.specs["screen_in"], 16)

    def test_pinned_cheap_item_has_no_price(self):
        # $9.50 oven mitts: below the plausible-laptop-price floor, so no price
        listing = self.by_id["slickdeals:thread-20058393"]
        self.assertIn("KitchenAid", listing.title)
        self.assertIsNone(listing.price)


class DealNewsFixture(unittest.TestCase):
    def setUp(self):
        self.listings = parse_feed("dealnews", read_bytes("dealnews.xml"))

    def test_structure(self):
        self.assertEqual(len(self.listings), 25)
        for listing in self.listings:
            self.assertTrue(listing.url.startswith("https://www.dealnews.com/"), listing.url)
            self.assertTrue(listing.title)
            self.assertTrue(listing.retailer, f"dealnews_retailer missing for {listing.title}")
        check_health("dealnews", self.listings, CONFIG["sources"]["dealnews"])

    def test_pinned_listing(self):
        victus = next(listing for listing in self.listings if "22216125" in listing.id)
        self.assertEqual(victus.title, 'HP Victus Ryzen 5 7535HS 15.6" Gaming Laptop for $875 + free shipping')
        self.assertEqual(victus.price, 875.0)
        self.assertEqual(victus.retailer, "Best Buy")
        self.assertEqual(self.listings[0].retailer, "Amazon")


class NeweggFixture(unittest.TestCase):
    def setUp(self):
        self.listings = parse_newegg_page(read_text("newegg_search.html"))
        self.by_id = {listing.id: listing for listing in self.listings}

    def test_structure(self):
        self.assertEqual(len(self.listings), 36)
        self.assertEqual(sum(listing.price is not None for listing in self.listings), 34)
        for listing in self.listings:
            self.assertTrue(listing.url.startswith("https://www.newegg.com/"), listing.url)
            self.assertTrue(listing.title)
        self.assertEqual(len(self.by_id), len(self.listings), "every listing needs a distinct id")
        check_health("newegg", self.listings, CONFIG["sources"]["newegg"])

    def test_pinned_newegg_sold_listing(self):
        listing = self.by_id["newegg:N82E16834236727"]
        self.assertEqual(listing.title, 'ASUS Vivobook S 16" 3K OLED Intel Core Ultra 7 255H - 32GB RAM - 1TB SSD Windows 11 Home (S5606CA-NS79)')
        self.assertEqual(listing.price, 1299.99)
        self.assertEqual(listing.url, "https://www.newegg.com/asus-vivobook-s-16-3k-oled-intel-core-ultra-7-255h-32gb-1tb-ssd-win11home-s5606ca-ns79/p/N82E16834236727")
        self.assertFalse(listing.marketplace)
        self.assertIn("Memory: 32GB LPDDR5X 7500", listing.text)
        self.assertEqual(listing.tags, ["AI Ready"])

    def test_pinned_marketplace_refurb(self):
        listing = self.by_id["newegg:1TS-000A-14SR9"]
        self.assertEqual(listing.price, 948.99)
        self.assertTrue(listing.marketplace)
        self.assertIn("Open Box", listing.tags)

    def test_combo_deals_keep_distinct_ids(self):
        combo = self.by_id["newegg:Combo.4884663"]
        self.assertEqual(combo.url, "https://www.newegg.com/Product/ComboDealDetails?ItemList=Combo.4884663")
        self.assertEqual(combo.price, 1385.99)
        self.assertFalse(combo.marketplace)
        self.assertNotEqual(combo.dedup_key, self.by_id["newegg:Combo.4884662"].dedup_key)

    def test_no_matches_at_real_threshold_but_matches_at_2000(self):
        def hits(max_price):
            criteria = {**CONFIG["criteria"], "max_price": max_price}
            return [listing.id for listing in self.listings
                    if evaluate(listing, criteria, CONFIG["costco"], True).kind == "deal"]
        self.assertEqual(hits(850), [])
        self.assertIn("newegg:N82E16834236727", hits(2000))
        self.assertEqual(len(hits(2000)), 10)


class NeweggAllSellersFixture(unittest.TestCase):
    """The same search without the sold-by-Newegg filter: every listing is a third-party seller."""

    def test_all_flagged_as_marketplace(self):
        listings = parse_newegg_page(read_text("newegg_search_all_sellers.html"))
        self.assertEqual(len(listings), 39)
        self.assertTrue(all(listing.marketplace for listing in listings))
        criteria = {**CONFIG["criteria"], "max_price": 5000}
        self.assertFalse(any(evaluate(listing, criteria, CONFIG["costco"], True).kind for listing in listings))


if __name__ == "__main__":
    unittest.main()
