"""Run one check: python -m monitor [--dry-run] [--test-alert]"""

import argparse
import json
import sys
from datetime import date, datetime, timezone

from monitor import alerts
from monitor.config import load_config, load_state, save_state
from monitor.http import PoliteSession
from monitor.sources import SOURCES
from monitor.specs import evaluate

WORKFLOW_FILE = "monitor.yml"
KEEPALIVE_DAYS = 7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of opening issues; don't save state")
    parser.add_argument("--test-alert", action="store_true", help="also open a test issue to confirm emails arrive")
    args = parser.parse_args(argv)

    config = load_config()
    state = load_state()
    before = json.dumps(state, sort_keys=True)
    today = datetime.now(timezone.utc).date()
    alerter = alerts.Alerter(config, dry_run=args.dry_run)

    try:
        if today > date.fromisoformat(config["project"]["return_deadline"]):
            if not state["finished"]:
                alerter.open_issue(*alerts.finished_issue(config), labels=["monitor"])
                state["finished"] = True
                alerter.disable_workflow(WORKFLOW_FILE)
            print("Return window has closed; nothing to do.")
        else:
            if args.test_alert:
                alerter.open_issue(*alerts.test_issue(config), labels=["monitor"])
            send_reminders(config, state, alerter, today)
            check_sources(config, state, alerter, today)
        keepalive(state, today)
    finally:
        # save even if a run dies halfway, so alerts already sent aren't sent again
        if args.dry_run:
            print("\n[dry run] state not saved")
        elif json.dumps(state, sort_keys=True) != before:
            save_state(state)
    return 0


def send_reminders(config: dict, state: dict, alerter: alerts.Alerter, today: date) -> None:
    for reminder in config.get("reminders", []):
        if reminder["date"] not in state["reminders_sent"] and date.fromisoformat(reminder["date"]) <= today:
            alerter.open_issue(*alerts.reminder_issue(reminder, config, today), labels=["reminder"])
            state["reminders_sent"].append(reminder["date"])


def check_sources(config: dict, state: dict, alerter: alerts.Alerter, today: date) -> None:
    http = PoliteSession(config["politeness"]["user_agent"], config["politeness"]["delay_seconds"])
    adjustment_open = today <= date.fromisoformat(config["costco"]["price_adjustment_deadline"])
    threshold = config["alerts"]["source_failure_threshold"]

    for name, fetch in SOURCES.items():
        source_cfg = config["sources"].get(name, {})
        if not source_cfg.get("enabled", True):
            continue
        try:
            listings = fetch(source_cfg, http)
        except Exception as exc:  # one broken source shouldn't stop the others
            failures = state["source_failures"].get(name, 0) + 1
            state["source_failures"][name] = failures
            print(f"{name}: FAILED ({failures} in a row): {type(exc).__name__}: {exc}")
            if failures == threshold:
                alerter.open_issue(*alerts.source_broken_issue(name, failures, f"{type(exc).__name__}: {exc}"), labels=["source-broken"])
            continue
        state["source_failures"][name] = 0

        hits = 0
        for listing in listings:
            if listing.id in state["alerted"]:
                continue
            verdict = evaluate(listing, config["criteria"], config["costco"], adjustment_open)
            if verdict.kind is None:
                if len(verdict.failures) == 1:  # near misses help tune the criteria
                    print(f"  near miss ({verdict.failures[0]}): {listing.title[:100]}")
                continue
            hits += 1
            build = alerts.price_adjustment_issue if verdict.kind == "price_adjustment" else alerts.deal_issue
            label = "price-adjustment" if verdict.kind == "price_adjustment" else "deal"
            alerter.open_issue(*build(listing, verdict, config, today), labels=[label])
            state["alerted"][listing.id] = {
                "title": listing.title[:200],
                "price": listing.price,
                "url": listing.url,
                "date": today.isoformat(),
            }
        print(f"{name}: {len(listings)} listings checked, {hits} new alerts")


def keepalive(state: dict, today: date) -> None:
    """GitHub switches off scheduled workflows in repos with no commits for 60 days.
    Touching state.json weekly guarantees the workflow commits something."""
    last = state.get("last_keepalive")
    if last is None or (today - date.fromisoformat(last)).days >= KEEPALIVE_DAYS:
        state["last_keepalive"] = today.isoformat()


if __name__ == "__main__":
    sys.exit(main())
