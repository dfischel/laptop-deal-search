"""Pull laptop specs out of free-text deal titles and decide whether a listing is a hit.

Deal titles are messy ("16GB x 2 (32GB) RAM", "32GB/1TB", "RTX 4060 8GB"), so each parser
is a heuristic. Every spec comes back as a value or None (not mentioned). A mentioned spec
that misses the criteria rules the listing out; an unmentioned one goes on the "verify" list.
"""

import re
from dataclasses import dataclass, field

# ---------- price ----------

PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?")
# "$150 off", "Save $200", "was $1,099", "$60 gift card": not the selling price
PRICE_SKIP_BEFORE = re.compile(r"(save|extra|up to|was|reg\.?|regularly|list|msrp|orig\.?|originally)\s*:?\s*$", re.I)
PRICE_SKIP_AFTER = re.compile(r"^\s*(off|gift|credit|coupon|reward|savings|back|rebate|discount|less)", re.I)
MIN_PLAUSIBLE_LAPTOP_PRICE = 150


def parse_price(title: str) -> float | None:
    """First dollar amount in the title that looks like a selling price."""
    for m in PRICE_RE.finditer(title):
        before, after = title[max(0, m.start() - 15):m.start()], title[m.end():m.end() + 15]
        if PRICE_SKIP_BEFORE.search(before) or PRICE_SKIP_AFTER.search(after):
            continue
        value = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        if value >= MIN_PLAUSIBLE_LAPTOP_PRICE:
            return value
    return None


# ---------- memory and storage ----------

GB_RE = re.compile(r"(\d{1,4})\s*GB\b", re.I)
TB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*TB\b", re.I)
RAM_SIZES = {4, 6, 8, 12, 16, 18, 24, 32, 36, 48, 64, 96, 128}
STORAGE_SIZES = {128, 256, 512, 1000, 1024, 2000, 2048, 4000, 4096}
STORAGE_WORDS = re.compile(r"^[\s,:/-]*(pcie\s*(gen\s*\d)?\s*)?(ssd|nvme|storage|emmc|hdd|m\.2|ufs|rom|flash|solid)", re.I)
RAM_WORDS = re.compile(r"^[\s,:/-]*(\(?\d+\s*x\s*\d+\s*GB\)?\s*)?(ram|memory|lpddr|ddr|unified|soldered)", re.I)
VRAM_WORDS_AFTER = re.compile(r"^[\s,:/-]*(gddr|vram|graphics|video)", re.I)
# "RTX 4060 8GB" is graphics memory; "RTX 5070, 16GB DDR5" is system RAM, so only an
# unseparated number right after the GPU name counts as graphics memory.
GPU_BEFORE = re.compile(r"(rtx|gtx|rx|arc|geforce|radeon)\s*[a-z]?\s*\d{3,4}\w*\s*(laptop\s*gpu)?\s*$", re.I)
HDD_AFTER = re.compile(r"^[\s,:/-]*(hdd|hard\s*drive|sata\s*hdd|\d+\s*rpm)", re.I)


def parse_ram_gb(text: str) -> int | None:
    found = []
    for m in GB_RE.finditer(text):
        value = int(m.group(1))
        before, after = text[max(0, m.start() - 25):m.start()], text[m.end():m.end() + 30]
        if STORAGE_WORDS.search(after) or VRAM_WORDS_AFTER.search(after):
            continue
        if GPU_BEFORE.search(before) and not RAM_WORDS.search(after):
            continue
        if value in RAM_SIZES or (RAM_WORDS.search(after) and value <= 128):
            found.append(value)
    return max(found) if found else None


def parse_storage_gb(text: str) -> int | None:
    """Storage in decimal GB: "1TB", "1 TB" and "1024GB" all come back as 1000."""
    found = []
    for m in TB_RE.finditer(text):
        if not HDD_AFTER.search(text[m.end():m.end() + 20]):
            found.append(round(float(m.group(1)) * 1000))
    for m in GB_RE.finditer(text):
        value, after = int(m.group(1)), text[m.end():m.end() + 30]
        if HDD_AFTER.search(after) or RAM_WORDS.search(after):
            continue
        if STORAGE_WORDS.search(after) or value in STORAGE_SIZES:
            found.append(value // 1024 * 1000 if value >= 1024 and value % 1024 == 0 else value)
    return max(found) if found else None


# ---------- screen and ports ----------

SCREEN_RE = re.compile(r"(?<![\d.])(1\d(?:\.\d{1,2})?)\s*(?:\"|”|″|''|-?\s?inch(?:es)?\b|-?in\b)", re.I)
THUNDERBOLT_RE = re.compile(r"thunderbolt|\btb\s?[45]\b|\busb\s?4\b|usb-?c\s*\(?\s*40\s*gb", re.I)


def parse_screen_in(text: str) -> float | None:
    m = SCREEN_RE.search(text)
    return float(m.group(1)) if m else None


def has_thunderbolt(text: str) -> bool | None:
    """True if Thunderbolt/USB4 is mentioned; None otherwise (titles rarely list ports)."""
    return True if THUNDERBOLT_RE.search(text) else None


# ---------- CPU ----------

# Any CPU mention at all. If one of these appears but nothing in cpu_allowed matches,
# the CPU is known and not good enough.
CPU_MENTION_RE = re.compile(
    r"core\s*ultra\s*x?\d|\bcore\s*i\d|\bi[3579][\s-]\d{4,5}|\bcore\s*[3579]\s*\d{3}|ryzen|celeron|pentium|athlon"
    r"|intel\s*(processor\s*)?n\d{2,3}\b|snapdragon|mediatek|apple\s*m\d",
    re.I,
)
# Family named without a model number ("Core Ultra 7 laptop"): alert, but ask to verify.
CPU_FAMILY_ONLY_RE = re.compile(
    r"core\s*ultra\s*x?[79](?![\s-]*\d{3})\b|ryzen\s*ai\s*(max\+?|[79])(?!\s*(pro\s*)?(hx\s*)?\d{3})\b",
    re.I,
)


# Marketing comparisons like "(Beats Ultra 7 255H)" or "(>Core i9)" name a CPU the laptop doesn't have.
CPU_COMPARISON_RE = re.compile(r"\((?:[^)]*\b(?:beats?|faster|outperforms|vs\.?|comparable)\b|\s*>)[^)]*\)", re.I)


def classify_cpu(text: str, allowed: list[str]) -> tuple[bool | None, str | None]:
    """(True, name) if on the allowed list, (False, name) if a lesser CPU, (None, name-or-None) if unclear."""
    text = CPU_COMPARISON_RE.sub(" ", text)
    for pattern in allowed:
        m = re.search(pattern, text, re.I)
        if m:
            return True, m.group(0)
    m = CPU_FAMILY_ONLY_RE.search(text)
    if m:
        return None, m.group(0)
    m = CPU_MENTION_RE.search(text)
    if m:
        # widen the match to include the model number for a readable log line
        return False, text[m.start():m.end() + 12].split(",")[0].strip()
    return None, None


# ---------- verdict ----------

LAPTOP_RE = re.compile(r"\b(laptop|notebook|2-in-1|2 in 1|ultrabook|copilot\+?\s*pc|convertible)\b", re.I)


@dataclass
class Verdict:
    kind: str | None  # "deal", "price_adjustment", or None (no alert)
    specs: dict = field(default_factory=dict)
    verify: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _spec(title: str, text: str, parser):
    """Prefer what the title says; fall back to the description."""
    value = parser(title)
    return value if value is not None else parser(text)


def evaluate(listing, criteria: dict, costco: dict, price_adjustment_open: bool) -> Verdict:
    title, text = listing.title, listing.text
    title_l = title.lower()
    everything = f"{title} | {text} | {listing.url} | {listing.retailer}".lower()

    specs = {
        "price": listing.price,
        "ram_gb": _spec(title, text, parse_ram_gb),
        "storage_gb": _spec(title, text, parse_storage_gb),
        "screen_in": _spec(title, text, parse_screen_in),
        "thunderbolt": _spec(title, text, has_thunderbolt),
    }
    cpu_ok, cpu_name = classify_cpu(title, criteria["cpu_allowed"])
    if cpu_ok is None and cpu_name is None:
        cpu_ok, cpu_name = classify_cpu(text, criteria["cpu_allowed"])
    specs["cpu"] = cpu_name

    # Costco price-adjustment path: the exact model you own, cheaper, at Costco.
    if (
        price_adjustment_open
        and any(k.lower() in everything for k in costco["model_keywords"])
        and "costco" in everything
        and listing.price is not None
        and listing.price < costco["purchase_price"]
    ):
        return Verdict("price_adjustment", specs)

    failures, verify = [], []
    if not (LAPTOP_RE.search(title) or specs["screen_in"]):
        failures.append("not a laptop")
    title_and_tags = f"{title_l} | {' '.join(listing.tags).lower()}"
    excluded = [k for k in criteria["exclude_keywords"] if re.search(rf"(?<!\w){re.escape(k.lower())}(?!\w)", title_and_tags)]
    if excluded:
        failures.append(f"excluded keyword: {excluded[0]}")
    if listing.marketplace and criteria["exclude_marketplace_sellers"]:
        failures.append("third-party marketplace seller")
    # Resellers put "professionally upgraded" in descriptions too, not just titles.
    reseller = [p for p in criteria["reseller_phrases"] if p.lower() in f"{title_l} | {text.lower()}"]
    if reseller:
        failures.append(f"third-party reseller: {reseller[0]}")

    if listing.price is None:
        failures.append("no single price")
    elif listing.price > criteria["max_price"]:
        failures.append(f"price ${listing.price:,.2f} > ${criteria['max_price']}")

    if specs["ram_gb"] is None:
        failures.append("RAM not stated")
    elif specs["ram_gb"] < criteria["min_ram_gb"]:
        failures.append(f"RAM {specs['ram_gb']}GB")

    if specs["storage_gb"] is None:
        failures.append("storage not stated")
    elif specs["storage_gb"] < criteria["min_storage_gb"]:
        failures.append(f"storage {specs['storage_gb']}GB")

    if cpu_ok is False:
        failures.append(f"CPU {cpu_name}")
    elif cpu_ok is None:
        verify.append(f"CPU model ({cpu_name or 'not stated'}): needs {criteria['cpu_description']}")

    screen = specs["screen_in"]
    if screen is None:
        verify.append("screen size (14-16 inch)")
    elif not criteria["screen_min_in"] <= screen <= criteria["screen_max_in"]:
        failures.append(f'screen {screen}"')

    if criteria["require_thunderbolt_or_usb4"] and not specs["thunderbolt"]:
        verify.append("Thunderbolt 4 / USB4 port")

    return Verdict(None if failures else "deal", specs, verify, failures)
