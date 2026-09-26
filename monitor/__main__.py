"""Run one check.

    python -m monitor                    normal run (what the schedule does)
    python -m monitor --dry-run          full run against live sites, prints alerts instead of sending, saves nothing
    python -m monitor --dry-run --max-price 2000
                                         same, with a temporary price limit, to see the pipeline fire on real listings
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from monitor import alerts
from monitor.config import load_config, load_state, save_heartbeat, save_state
from monitor.http import PoliteSession
from monitor.pipeline import new_heartbeat, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print alerts instead of opening issues, save nothing, and always include the canary")
    parser.add_argument("--max-price", "--threshold", type=float, metavar="DOLLARS",
                        help="override the price limit in config.toml for this run only")
    parser.add_argument("--canary", action="store_true", help="inject the canary listing even if not due this week")
    parser.add_argument("--test-alert", action="store_true", help="also open a plain test issue")
    args = parser.parse_args(argv)

    config = load_config()
    if args.max_price is not None:
        print(f"*** price limit overridden for this run: ${args.max_price:,.2f} (config.toml says ${config['criteria']['max_price']}) ***")
        config["criteria"]["max_price"] = args.max_price
    state = load_state()
    before = json.dumps(state, sort_keys=True)
    now = datetime.now(timezone.utc)
    alerter = alerts.Alerter(config, dry_run=args.dry_run)
    http = PoliteSession(config["politeness"]["user_agent"], config["politeness"]["delay_seconds"])
    heartbeat = new_heartbeat(now, config)
    if os.environ.get("GITHUB_RUN_ID"):
        heartbeat["run_url"] = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"

    try:
        if args.test_alert:
            alerter.open_issue(*alerts.test_issue(config), labels=["monitor"])
        run(config, state, alerter, http, now, heartbeat, force_canary=args.canary or args.dry_run)
    except Exception as exc:
        heartbeat["crashed"] = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        # save even if the run dies halfway, so alerts already sent aren't sent again
        print("\nheartbeat:", json.dumps(heartbeat, indent=2))
        if args.dry_run:
            print(f"\n[dry run] {len(alerter.sent)} issue(s) formatted, none sent; state not saved")
        else:
            if json.dumps(state, sort_keys=True) != before:
                save_state(state)
            save_heartbeat(heartbeat)  # every run, so the repo is never "inactive" (see README)
    return 0


if __name__ == "__main__":
    sys.exit(main())
