"""Spec parsing and match rules. Run all tests with: python -m unittest"""

import unittest

from monitor.config import load_config
from monitor.http import is_allowed, parse_robots
from monitor.sources import Listing
from monitor.specs import (classify_cpu, evaluate, has_thunderbolt, parse_price, parse_ram_gb, parse_screen_in,
                           parse_storage_gb)

CONFIG = load_config()


def check(title, price=None, text="", source="slickdeals", tags=None, marketplace=False, adjustment_open=True,
          config=CONFIG):
    listing = Listing(source, "id", title, "https://example.com", price if price is not None else parse_price(title),
                      text=text, tags=tags or [], marketplace=marketplace)
    return evaluate(listing, config["criteria"], config["costco"], adjustment_open)


# A listing that meets every criterion. Each table row changes one part of it.
GOOD = {
    "screen": '16"',
    "cpu": "Intel Core Ultra 7 258V",
    "ram": "32GB RAM",
    "storage": "1TB SSD",
    "port": "Thunderbolt 4",
    "price": "$799.99",
    "prefix": "Lenovo Yoga Slim 7",
}


def good_title(**changes) -> str:
    p = {**GOOD, **changes}
    return f'{p["prefix"]} {p["screen"]} Laptop, {p["cpu"]}, {p["ram"]}, {p["storage"]}, {p["port"]} {p["price"]}'


class FilterBoundaries(unittest.TestCase):
    """Table-driven: (description, changes to the good listing, should it alert?)"""

    def run_table(self, table):
        for description, changes, expected in table:
            with self.subTest(description):
                verdict = check(good_title(**changes))
                self.assertEqual(verdict.kind == "deal", expected, f"{good_title(**changes)} -> {verdict.failures}")

    def test_baseline_matches(self):
        verdict = check(good_title())
        self.assertEqual(verdict.kind, "deal")
        self.assertEqual(verdict.verify, [])

    def test_ram(self):
        self.run_table([
            ("32GB matches", {"ram": "32GB RAM"}, True),
            ("32 GB with a space matches", {"ram": "32 GB LPDDR5X"}, True),
            ("64GB matches", {"ram": "64GB DDR5"}, True),
            ("16GB doesn't", {"ram": "16GB RAM"}, False),
            ("24GB doesn't", {"ram": "24GB RAM"}, False),
            ("RAM not stated doesn't", {"ram": "Windows 11"}, False),
        ])

    def test_storage_normalizes_to_1tb(self):
        for text in ["1024GB", "1024GB SSD", "1 TB", "1TB SSD", "1TB", "1 TB NVMe SSD"]:
            with self.subTest(text):
                self.assertEqual(parse_storage_gb(f"32GB RAM, {text}"), 1000)
        self.assertEqual(parse_storage_gb("2048GB SSD"), 2000)
        self.run_table([
            ("1024GB matches", {"storage": "1024GB SSD"}, True),
            ("1 TB matches", {"storage": "1 TB"}, True),
            ("2TB matches", {"storage": "2TB SSD"}, True),
            ("512GB doesn't", {"storage": "512GB SSD"}, False),
            ("1TB HDD doesn't", {"storage": "1TB HDD"}, False),
        ])

    def test_thunderbolt(self):
        for text, counts in [("USB4", True), ("USB 4", True), ("Thunderbolt 4", True), ("TB4", True),
                             ("Thunderbolt 5", True), ("USB-C 3.2", False), ("USB 3.2 Gen 2", False),
                             ("USB-C", False), ("HDMI 2.1", False)]:
            with self.subTest(text):
                self.assertEqual(bool(has_thunderbolt(text)), counts)
        # "USB-C 3.2" doesn't count as Thunderbolt. It isn't proof there's no Thunderbolt port
        # either, so the listing still alerts, with Thunderbolt on the "verify" list (agreed rule
        # for specs a listing doesn't confirm).
        verdict = check(good_title(port="USB-C 3.2"))
        self.assertEqual(verdict.kind, "deal")
        self.assertIn("Thunderbolt 4 / USB4 port", verdict.verify)
        self.assertNotIn("Thunderbolt 4 / USB4 port", check(good_title(port="USB4")).verify)

    def test_arm_excluded(self):
        self.run_table([
            ("Snapdragon X Elite", {"cpu": "Snapdragon X Elite X1E-80-100"}, False),
            ("Qualcomm Snapdragon X Plus", {"cpu": "Qualcomm Snapdragon X Plus"}, False),
            ("Snapdragon in the model name", {"prefix": "Surface Laptop 7 Snapdragon"}, False),
            ("Chromebook", {"prefix": "Lenovo Chromebook Plus"}, False),
        ])

    def test_price(self):
        self.run_table([
            ("$850 matches", {"price": "$850"}, True),
            ("$850.00 matches", {"price": "$850.00"}, True),
            ("$849.99 matches", {"price": "$849.99"}, True),
            ("$850.01 doesn't", {"price": "$850.01"}, False),
            ("$851 doesn't", {"price": "$851"}, False),
            ("no price doesn't", {"price": ""}, False),
        ])

    def test_resellers_excluded(self):
        self.run_table([
            ("'Professionally Upgraded' in title", {"prefix": "(Professionally Upgraded) HP OmniBook 5"}, False),
            ("'Upgraded to 32GB' in title", {"ram": "Upgraded to 32GB RAM"}, False),
            ("'Customized' in title", {"prefix": "Customized Dell 16 Plus"}, False),
            ("refurbished", {"prefix": "Refurbished Dell 16 Plus"}, False),
            ("open box", {"prefix": "Open Box Dell 16 Plus"}, False),
        ])
        # the phrase only in the description (fine print)
        verdict = check(good_title(), text="This unit has been professionally upgraded by Acme Computers.")
        self.assertIsNone(verdict.kind)
        # Newegg: third-party seller, or tagged "Upgraded Model" by Newegg
        self.assertIsNone(check(good_title(), source="newegg", marketplace=True).kind)
        self.assertIsNone(check(good_title(), source="newegg", tags=["Upgraded Model"]).kind)
        # sanity: same listing sold by the retailer itself, no reseller language, matches
        self.assertEqual(check(good_title(), source="newegg").kind, "deal")

    def test_cpu(self):
        self.run_table([
            ("Core Ultra 9 288V (yours)", {"cpu": "Intel Core Ultra 9 288V"}, True),
            ("Panther Lake Core Ultra X7 358H", {"cpu": "Intel Core Ultra X7 358H"}, True),
            ("Ryzen AI 9 HX 370", {"cpu": "AMD Ryzen AI 9 HX 370"}, True),
            ("Core Ultra 7 155H (older)", {"cpu": "Intel Core Ultra 7 155H"}, False),
            ("Core Ultra 5 226V", {"cpu": "Intel Core Ultra 5 226V"}, False),
            ("Ryzen 7 7730U", {"cpu": "AMD Ryzen 7 7730U"}, False),
        ])

    def test_screen(self):
        self.run_table([
            ('14" matches', {"screen": '14"'}, True),
            ('16.1" matches (16-inch class)', {"screen": '16.1"'}, True),
            ('13.3" doesn\'t', {"screen": '13.3"'}, False),
            ('17.3" doesn\'t', {"screen": '17.3"'}, False),
        ])


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
        self.assertEqual(parse_ram_gb("RTX 5080 16GB, 512GB SSD"), None)

    def test_ram_right_after_gpu_name(self):
        # found in the Slickdeals fixture: RAM listed straight after the GPU
        self.assertEqual(parse_ram_gb("i7-14650HX, RTX 5070, 16GB DDR5, 512GB SSD"), 16)
        self.assertEqual(parse_ram_gb("GeForce RTX 5060 - 32GB RAM - 1TB SSD"), 32)
        self.assertEqual(parse_ram_gb("RTX 5060 32GB DDR5 1TB"), 32)

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
    def test_match_with_things_to_verify(self):
        v = check("Lenovo Yoga 7 2-in-1 Ryzen AI 7 350 32GB/1TB Laptop for $749 + free shipping")
        self.assertEqual(v.kind, "deal")
        self.assertEqual(len(v.verify), 2)  # screen size and Thunderbolt not stated

    def test_not_a_laptop(self):
        self.assertIsNone(check("ABS Gaming Desktop PC Core Ultra 7 265 32GB 1TB $799").kind)

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
