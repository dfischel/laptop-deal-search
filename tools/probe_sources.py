"""Check that each deal source still returns usable data.

Run this if alerts go quiet or a source starts erroring:
    python tools/probe_sources.py
It is also run on GitHub's servers by the "Probe sources" workflow.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.config import load_config  # noqa: E402
from monitor.http import PoliteSession  # noqa: E402
from monitor.sources import SOURCES  # noqa: E402


def main() -> int:
    config = load_config()
    http = PoliteSession(config["politeness"]["user_agent"], config["politeness"]["delay_seconds"])
    failed = 0
    for name, fetch in SOURCES.items():
        source_cfg = config["sources"].get(name, {})
        start = time.monotonic()
        try:
            listings = fetch(source_cfg, http)
            status = "OK" if listings else "EMPTY"
            if not listings:
                failed += 1
            print(f"{name:<12} {status:<6} {len(listings):>3} listings  ({time.monotonic() - start:.1f}s)")
            for listing in listings[:3]:
                price = f"${listing.price:,.2f}" if listing.price else "no price"
                print(f"    - {price:>10}  {listing.title[:90]}")
        except Exception as exc:  # report every failure, keep probing the rest
            failed += 1
            print(f"{name:<12} FAIL   {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
