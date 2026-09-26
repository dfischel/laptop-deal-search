"""Fetch and parse listings from each deal source, then check the result looks healthy.

Parsing is separate from fetching so tests can run the parsers on saved pages (tests/fixtures/).
fetch_source() is the only entry point the monitor uses: it fetches, parses, and raises
SourceBroken if the result looks like a blocked or restructured page instead of a quiet market.
"""

import html
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup

from monitor.http import PoliteSession
from monitor.specs import parse_price


class SourceBroken(Exception):
    """The source answered, but not with a normal page of listings."""


@dataclass
class Listing:
    source: str
    id: str
    title: str
    url: str
    price: float | None
    text: str = ""  # description or feature list; used to fill in specs the title leaves out
    retailer: str = ""
    tags: list[str] = field(default_factory=list)  # e.g. Newegg's "Upgraded Model", "Open Box"
    marketplace: bool = False  # sold by a third-party seller rather than the retailer itself
    canary: bool = False  # synthetic weekly test listing

    @property
    def dedup_key(self) -> str:
        """Same URL at the same price = same listing. A new price is a new alert."""
        parts = urlsplit(self.url)
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(TRACKING_PARAMS)])
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
        price = f"{self.price:.2f}" if self.price is not None else "none"
        return f"{url}|{price}"


TRACKING_PARAMS = ("utm_", "iref", "cm_", "icid")


def _strip_html(markup: str) -> str:
    text = BeautifulSoup(markup, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ---------- RSS feeds (Slickdeals, DealNews) ----------

def parse_feed(source: str, content: bytes) -> list[Listing]:
    feed = feedparser.parse(content)
    listings = []
    for entry in feed.entries:
        title = html.unescape(entry.get("title", "")).strip()
        listings.append(Listing(
            source=source,
            id=f"{source}:{entry.get('id') or entry.get('link')}",
            title=title,
            url=entry.get("link", ""),
            price=parse_price(title),  # title only: descriptions often quote other products' prices
            text=_strip_html(entry.get("summary", "")),
            retailer=entry.get("dealnews_retailer", ""),
        ))
    return listings


def fetch_slickdeals(cfg: dict, http: PoliteSession) -> list[Listing]:
    return parse_feed("slickdeals", http.get(cfg["url"]).content)


def fetch_dealnews(cfg: dict, http: PoliteSession) -> list[Listing]:
    return parse_feed("dealnews", http.get(cfg["url"]).content)


# ---------- Newegg search pages ----------

NEWEGG_SEARCH = "https://www.newegg.com/p/pl"


def newegg_search_url(query: str) -> str:
    # Order=1 sorts lowest price first, so anything under max_price is on page 1.
    # N=8000 is the "Sold by Newegg" filter. Without it, page 1 is all third-party
    # resellers, which would push Newegg's own listings off the page.
    return f"{NEWEGG_SEARCH}?{urlencode({'d': query, 'Order': 1, 'N': 8000})}"


def parse_newegg_page(page: str) -> list[Listing]:
    soup = BeautifulSoup(page, "html.parser")
    return [listing for cell in soup.select("div.item-cell") if (listing := _parse_newegg_cell(cell))]


def fetch_newegg(cfg: dict, http: PoliteSession) -> list[Listing]:
    listings: dict[str, Listing] = {}
    for query in cfg["queries"]:
        response = http.get(newegg_search_url(query))
        page_listings = parse_newegg_page(response.text)
        if not page_listings:
            title = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.S | re.I)
            print(f"  newegg: no listings for {query!r} (page title {title.group(1).strip()[:60] if title else None!r}, "
                  f"{len(response.text)} bytes)")
        for listing in page_listings:
            listings[listing.id] = listing
    return list(listings.values())


def _parse_newegg_cell(cell) -> Listing | None:
    link = cell.select_one("a.item-title")
    if not link:
        return None
    url = link.get("href", "")
    if url.startswith("//"):
        url = "https:" + url
    if match := re.search(r"/p/([A-Za-z0-9-]+)", url):
        item = match.group(1)
        url = url.split("?")[0]
    elif match := re.search(r"ItemList=(Combo\.\d+)", url):  # bundle deals, sold by Newegg
        item = match.group(1)
        url = f"https://www.newegg.com/Product/ComboDealDetails?ItemList={item}"
    else:
        item = url

    price = None
    price_el = cell.select_one("li.price-current")
    if price_el and price_el.strong:
        dollars = price_el.strong.get_text(strip=True).replace(",", "")
        cents = price_el.sup.get_text(strip=True) if price_el.sup else ""
        try:
            price = float(dollars + (cents if re.fullmatch(r"\.\d\d", cents) else ""))
        except ValueError:
            price = None

    tags = [tag.get_text(" ", strip=True) for tag in cell.select(".tag-text")]
    open_box = cell.select_one(".item-open-box-italic")
    if open_box and open_box.get_text(strip=True):
        tags.append("Open Box")

    return Listing(
        source="newegg",
        id=f"newegg:{item}",
        title=link.get_text(" ", strip=True),
        url=url,
        price=price,
        text=" | ".join(li.get_text(" ", strip=True) for li in cell.select("ul.item-features li")),
        retailer="Newegg",
        tags=tags,
        marketplace=not is_sold_by_newegg(item),
    )


def is_sold_by_newegg(item: str) -> bool:
    """Newegg's own item numbers start N82E; bundles are "Combo.NNN". Third-party seller items
    use other formats (9SIA..., or dashed like 1TS-000E-1ED54, which product pages confirm as
    "Sold by PCOnline US" etc.). Unknown formats count as third-party, the safe side."""
    return item.upper().startswith("N82E") or item.startswith("Combo.")


# ---------- health check ----------

def check_health(source: str, listings: list[Listing], cfg: dict) -> None:
    """Raise SourceBroken unless this looks like a normal page of listings.

    A blocked request usually comes back as a challenge page, which parses to zero listings.
    A redesigned site usually still has *some* matching elements but with empty fields.
    Both must alert, never pass as "no deals today".
    """
    minimum = cfg.get("min_listings", 1)
    if len(listings) < minimum:
        raise SourceBroken(f"parsed {len(listings)} listings, expected at least {minimum} (blocked, or the page changed)")
    for name, ok in [("title", lambda l: l.title), ("url", lambda l: l.url.startswith("http"))]:
        missing = sum(1 for listing in listings if not ok(listing))
        if missing:
            raise SourceBroken(f"{missing} of {len(listings)} listings have no {name} (the page layout changed)")
    min_priced = cfg.get("min_priced_fraction", 0)
    priced = sum(1 for listing in listings if listing.price is not None) / len(listings)
    if priced < min_priced:
        raise SourceBroken(f"only {priced:.0%} of listings have a price, expected {min_priced:.0%} (the page layout changed)")


FETCHERS = {
    "slickdeals": fetch_slickdeals,
    "dealnews": fetch_dealnews,
    "newegg": fetch_newegg,
}
SOURCES = list(FETCHERS)


def fetch_source(name: str, cfg: dict, http: PoliteSession) -> list[Listing]:
    listings = FETCHERS[name](cfg, http)
    check_health(name, listings, cfg)
    return listings
