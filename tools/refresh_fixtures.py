"""Save a fresh copy of each live source into tests/fixtures/.

    python tools/refresh_fixtures.py

tests/test_fixtures.py checks that the parsers pull the right fields out of these saved pages.
After refreshing, run `python -m unittest`. The structural tests should still pass. The tests
that pin exact titles and prices will fail; update their expected values to match the new
pages (open the fixture file and look), and review the diff to see what changed on the site.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.config import load_config  # noqa: E402
from monitor.http import PoliteSession  # noqa: E402
from monitor.sources import newegg_search_url  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    config = load_config()
    http = PoliteSession(config["politeness"]["user_agent"], config["politeness"]["delay_seconds"])
    targets = {
        "slickdeals.xml": config["sources"]["slickdeals"]["url"],
        "dealnews.xml": config["sources"]["dealnews"]["url"],
        "newegg_search.html": newegg_search_url(config["sources"]["newegg"]["queries"][0]),
        # Same search without the sold-by-Newegg filter: mostly third-party resellers,
        # used to test that they're detected and excluded.
        "newegg_search_all_sellers.html": newegg_search_url(config["sources"]["newegg"]["queries"][0]).replace("&N=8000", ""),
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for filename, url in targets.items():
        content = http.get(url).content
        (FIXTURES / filename).write_bytes(content)
        print(f"saved {filename}: {len(content):,} bytes from {url}")


if __name__ == "__main__":
    main()
