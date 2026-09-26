"""Run with: python -m unittest"""

import unittest

from monitor.config import load_config
from monitor.http import is_allowed, parse_robots
from monitor.sources import Listing
from monitor.specs import classify_cpu, evaluate, parse_price, parse_ram_gb, parse_screen_in, parse_storage_gb

CONFIG = load_config()


def check(title, price=None, text="", source="slickdeals", tags=None, marketplace=False, adjustment_open=True):
    listing = Listing(source, "id", title, "https://example.com", price if price is not None else parse_price(title),
                      text=text, tags=tags or [], marketplace=marketplace)
    return evaluate(listing, CONFIG["criteria"], CONFIG["costco"], adjustment_open)


class ParsePrice(unittest.TestCase):
    def test_trailing_price(self):
        self.assertEqual(parse_price("GIGABYTE Gaming A16 Pro Laptop, 32GB RAM, 1TB SSD $1799.00"), 1799.00)

    def test_for_price_plus_shipping(self):
        self.assertEqual(parse_price('HP Victus Ryzen 5 7535HS 15.6" Gaming Laptop for $875 + free shipping'), 875)

    def test_skips_discount_amounts(self):
        self.assertEqual(parse_price("$150 off HP OmniBook 5, now $799.99"), 799.99)
        self.assertEqual(parse_price("Save $200: Dell 16 Plus laptop $749"), 749)
        self.assertEqual(parse_price("Laptop was $1,099, now $829"), 829)
        self.assertEqual(parse_price("Laptop $899 + $50 gift card"), 899)

    def test_ignores_tiny_amounts(self):
        self.assertIsNone(parse_price("Silicone Case for Galaxy S25 $4 & More"))


class ParseSpecs(unittest.TestCase):
    def test_ram_from_kit_notation(self):
        self.assertEqual(parse_ram_gb("Intel Core 7 240H, 16GB x 2 (32GB) RAM, 1TB SSD"), 32)

    def test_ram_slash_storage(self):
        self.assertEqual(parse_ram_gb("Ryzen AI 7 350 32GB/1TB"), 32)
        self.assertEqual(parse_storage_gb("Ryzen AI 7 350 32GB/1TB"), 1000)

    def test_ignores_vram_and_storage(self):
        self.assertEqual(parse_ram_gb("RTX 4060 8GB, 16GB DDR5, 512GB SSD"), 16)
        self.assertEqual(parse_ram_gb("GeForce RTX 5070 8GB GDDR7, 32GB LPDDR5X"), 32)

    def test_storage(self):
        self.assertEqual(parse_storage_gb("32GB Memory 1 TB NVMe SSD"), 1000)
        self.assertEqual(parse_storage_gb("16GB RAM, 512GB SSD"), 512)
        self.assertEqual(parse_storage_gb("Memory: 32GB | SSD: 1TB NVMe"), 1000)
        self.assertIsNone(parse_storage_gb("2x Thunderbolt 4 (TB4) ports, 32GB RAM"))

    def test_screen(self):
        self.assertEqual(parse_screen_in('HP 14-em0085cl 14" HD Laptop'), 14)
        self.assertEqual(parse_screen_in("Lenovo 16-inch 2-in-1"), 16)
        self.assertEqual(parse_screen_in("Victus 15.6” FHD"), 15.6)
        self.assertIsNone(parse_screen_in("Acer Aspire 16 AI laptop"))


class ClassifyCpu(unittest.TestCase):
    allowed = CONFIG["criteria"]["cpu_allowed"]

    def assertCpu(self, text, expected):
        self.assertEqual(classify_cpu(text, self.allowed)[0], expected, text)

    def test_allowed(self):
        for text in ["Core Ultra 9 288V", "Intel Core Ultra 7 258V", "Core Ultra7-255H", "Ultra 9 275HX",
                     "Core Ultra X7 358H", "Core Ultra 7 365", "Ryzen AI 7 350", "Ryzen AI 9 HX 370",
                     "Ryzen AI 7 PRO 360", "Ryzen AI Max+ 395", "Ryzen AI 9 465"]:
            self.assertCpu(text, True)

    def test_rejected(self):
        for text in ["AMD Ryzen 7 7730U", "Intel Core 7 240H", "Core Ultra 7 155H", "Core Ultra 7 255U",
                     "Core Ultra 5 225H", "Ryzen AI 5 340", "Core i7-13620H", "Snapdragon X Elite"]:
            self.assertCpu(text, False)

    def test_family_only_is_unclear(self):
        self.assertCpu("Intel Core Ultra 7 laptop", None)
        self.assertCpu("Ryzen AI 9 HX - 32GB RAM", None)
        self.assertCpu("Lenovo IdeaPad 32GB laptop", None)

    def test_marketing_comparison_ignored(self):
        self.assertCpu("AMD Ryzen 5 7535HS (Beats Ultra 7 255H) laptop", False)
        self.assertCpu("Ryzen AI 7 350 (Beat Ultra 7 255HX)", True)


class Evaluate(unittest.TestCase):
    def test_clean_match(self):
        v = check('ASUS Zenbook 14" OLED Laptop, Intel Core Ultra 7 258V, 32GB LPDDR5X, 1TB SSD, Thunderbolt 4 $799.99')
        self.assertEqual(v.kind, "deal")
        self.assertEqual(v.verify, [])

    def test_match_with_things_to_verify(self):
        v = check("Lenovo Yoga 7 2-in-1 Ryzen AI 7 350 32GB/1TB Laptop for $749 + free shipping")
        self.assertEqual(v.kind, "deal")
        self.assertEqual(len(v.verify), 2)  # screen size and Thunderbolt not stated

    def test_newegg_upgraded_reseller_rejected(self):
        v = check('HP 14-em0085cl 14" HD Laptop AMD Ryzen 7 7730U 32GB Memory 1 TB NVMe SSD', price=669.99,
                  source="newegg", tags=["Upgraded Model"])
        self.assertIsNone(v.kind)
        self.assertTrue(any("upgraded" in f for f in v.failures))

    def test_rejections(self):
        cases = {
            "16GB": 'Dell 16 Plus 16" Laptop Core Ultra 7 255H 16GB 1TB $699',
            "price": 'Dell 16 Plus 16" Laptop Core Ultra 7 258V 32GB 1TB $949',
            "snapdragon": 'Surface Laptop 15" Snapdragon X Elite 32GB 1TB $699',
            "refurbished": 'Refurbished Dell 16 Plus 16" Laptop Core Ultra 9 288V 32GB 1TB $649',
            "screen": 'HP OmniBook 7 17.3" Laptop Core Ultra 7 258V 32GB 1TB $799',
            "not a laptop": "ABS Gaming Desktop PC Core Ultra 7 265 32GB 1TB $799",
        }
        for reason, title in cases.items():
            self.assertIsNone(check(title).kind, reason)

    def test_marketplace_rejected(self):
        v = check('Lenovo 16" Laptop Core Ultra 7 258V 32GB 1TB $699', source="newegg", marketplace=True)
        self.assertIsNone(v.kind)

    def test_costco_price_adjustment(self):
        title = "Acer Aspire 16 AI A16-52MT-91B0 Core Ultra 9 288V 32GB 1TB Laptop $799.99 at Costco"
        self.assertEqual(check(title).kind, "price_adjustment")
        # after the adjustment window it's judged as an ordinary deal
        self.assertEqual(check(title, adjustment_open=False).kind, "deal")

    def test_costco_same_price_no_alert(self):
        self.assertNotEqual(check("Acer Aspire 16 A16-52MT-91B0 Laptop $899.99 at Costco").kind, "price_adjustment")


class Robots(unittest.TestCase):
    ROBOTS = """
User-agent: Mediapartners-Google
Disallow:

User-agent: *
Disallow: /deal-feed/*
Disallow: /newsearch.php?*rss=*
Allow: /newsearch.php?*rss=*&allowed=1

User-agent: badbot
Disallow: /
"""

    def test_rules(self):
        rules = parse_robots(self.ROBOTS)
        self.assertFalse(is_allowed(rules, "/newsearch.php?mode=frontpage&rss=1"))
        self.assertTrue(is_allowed(rules, "/newsearch.php?q=laptop"))
        self.assertTrue(is_allowed(rules, "/newsearch.php?x&rss=1&allowed=1"))
        self.assertFalse(is_allowed(rules, "/deal-feed/laptops"))
        self.assertTrue(is_allowed(rules, "/"))

    def test_disallow_all(self):
        self.assertFalse(is_allowed(parse_robots("User-agent: *\nDisallow: /"), "/r/buildapcsales/new/.rss"))


if __name__ == "__main__":
    unittest.main()
