"""Fetch listings from each deal source. Each fetcher returns a list of Listing."""

import html
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import feedparser
from bs4 import BeautifulSoup

from monitor.http import PoliteSession
from monitor.specs import parse_price


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


def _strip_html(markup: str) -> str:
    text = BeautifulSoup(markup, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _fetch_feed(source: str, cfg: dict, http: PoliteSession) -> list[Listing]:
    response = http.get(cfg["url"])
    feed = feedparser.parse(response.content)
    if not feed.entries:
        raise ValueError(f"feed has no entries ({feed.get('bozo_exception', 'empty feed')})")
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
    return _fetch_feed("slickdeals", cfg, http)


def fetch_dealnews(cfg: dict, http: PoliteSession) -> list[Listing]:
    return _fetch_feed("dealnews", cfg, http)


NEWEGG_SEARCH = "https://www.newegg.com/p/pl"


def fetch_newegg(cfg: dict, http: PoliteSession) -> list[Listing]:
    listings: dict[str, Listing] = {}
    empty_pages = []
    for query in cfg["queries"]:
        # Order=1 sorts lowest price first, so anything under max_price is on page 1
        response = http.get(f"{NEWEGG_SEARCH}?{urlencode({'d': query, 'Order': 1})}")
        soup = BeautifulSoup(response.text, "html.parser")
        cells = soup.select("div.item-cell")
        if not cells:
            page_title = soup.title.get_text(strip=True)[:80] if soup.title else "no title"
            empty_pages.append(f"{query!r} -> {response.url} [{page_title!r}, {len(response.text)} bytes]")
            print(f"  newegg: no listings for {empty_pages[-1]}")
        for cell in cells:
            listing = _parse_newegg_cell(cell)
            if listing:
                listings[listing.id] = listing
    if not listings:
        raise ValueError("no listings on any search page (blocked, or Newegg changed its layout): " + "; ".join(empty_pages))
    return list(listings.values())


def _parse_newegg_cell(cell) -> Listing | None:
    link = cell.select_one("a.item-title")
    if not link:
        return None
    url = link.get("href", "").split("?")[0]
    match = re.search(r"/p/([A-Za-z0-9-]+)", url)
    item = match.group(1) if match else url

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
        marketplace=item.upper().startswith("9SI"),  # Newegg item numbers for third-party sellers
    )


SOURCES = {
    "slickdeals": fetch_slickdeals,
    "dealnews": fetch_dealnews,
    "newegg": fetch_newegg,
}
