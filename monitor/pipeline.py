"""One monitor run. Everything it touches is passed in, so tests can drive it with fake HTTP."""

from datetime import date, datetime

from monitor import alerts
from monitor.sources import SOURCES, Listing, fetch_source
from monitor.specs import evaluate, parse_price

WORKFLOW_FILE = "monitor.yml"


def new_heartbeat(now: datetime, config: dict) -> dict:
    return {
        "last_run_utc": now.isoformat(timespec="seconds"),
        "max_price": config["criteria"]["max_price"],
        "sources": {},
        "alerts_sent": 0,
        "canary": "not due",
    }


def run(config: dict, state: dict, alerter: alerts.Alerter, http, now: datetime, heartbeat: dict,
        force_canary: bool = False) -> None:
    today = now.date()
    state.pop("last_keepalive", None)  # replaced by heartbeat.json

    if today > date.fromisoformat(config["project"]["return_deadline"]):
        if not state["finished"]:
            alerter.open_issue(*alerts.finished_issue(config), labels=["monitor"])
            state["finished"] = True
            alerter.disable_workflow(WORKFLOW_FILE)
        heartbeat["finished"] = True
        print("Return window has closed; nothing to do.")
        return

    alerter.keep_workflow_enabled(WORKFLOW_FILE)
    send_reminders(config, state, alerter, today)
    listings = fetch_all(config, state, alerter, http, heartbeat)

    week = "{}-W{:02d}".format(*today.isocalendar()[:2])
    if config["canary"]["enabled"] and (force_canary or state.get("last_canary_week") != week):
        listings.append(make_canary(config, now))
        state["last_canary_week"] = week
        heartbeat["canary"] = "injected"

    process_listings(listings, config, state, alerter, today, heartbeat)


def send_reminders(config: dict, state: dict, alerter: alerts.Alerter, today: date) -> None:
    for reminder in config.get("reminders", []):
        if reminder["date"] not in state["reminders_sent"] and date.fromisoformat(reminder["date"]) <= today:
            alerter.open_issue(*alerts.reminder_issue(reminder, config, today), labels=["reminder"])
            state["reminders_sent"].append(reminder["date"])


def fetch_all(config: dict, state: dict, alerter: alerts.Alerter, http, heartbeat: dict) -> list[Listing]:
    """Fetch every enabled source. A source that errors OR returns an unhealthy page counts as a failure."""
    threshold = config["alerts"]["source_failure_threshold"]
    listings = []
    for name in SOURCES:
        source_cfg = config["sources"].get(name, {})
        if not source_cfg.get("enabled", True):
            heartbeat["sources"][name] = {"status": "disabled"}
            continue
        try:
            found = fetch_source(name, source_cfg, http)
        except Exception as exc:  # one broken source shouldn't stop the others
            failures = state["source_failures"].get(name, 0) + 1
            state["source_failures"][name] = failures
            error = f"{type(exc).__name__}: {exc}"
            heartbeat["sources"][name] = {"status": "FAILED", "consecutive_failures": failures, "error": error[:300]}
            print(f"{name}: FAILED ({failures} in a row): {error}")
            if failures == threshold:
                alerter.open_issue(*alerts.source_broken_issue(name, failures, error), labels=["source-broken"])
            continue
        state["source_failures"][name] = 0
        heartbeat["sources"][name] = {"status": "ok", "listings": len(found)}
        print(f"{name}: parsed {len(found)} listings")
        listings.extend(found)
    return listings


def make_canary(config: dict, now: datetime) -> Listing:
    """A fake listing built from the current criteria, so it should always match.

    It goes through the same price parsing, spec parsing, matching, dedup and alerting as a
    real listing. If the weekly canary email stops, something in that chain (or the schedule) is broken.
    """
    c = config["criteria"]
    title = (f'[CANARY] Weekly pipeline test: {c["screen_min_in"]:g}" laptop, Intel Core Ultra 9 288V, '
             f'{c["min_ram_gb"]}GB RAM, {c["min_storage_gb"] / 1000:g}TB SSD, Thunderbolt 4, ${c["max_price"]:,.2f}')
    return Listing(
        source="canary",
        id=f"canary:{now:%Y%m%dT%H%M%S}",
        title=title,
        url=f"https://canary.invalid/{now:%Y%m%dT%H%M%S}",  # unique per run, so dedup never swallows it
        price=parse_price(title),
        retailer="(synthetic test listing)",
        canary=True,
    )


def process_listings(listings: list[Listing], config: dict, state: dict, alerter: alerts.Alerter,
                     today: date, heartbeat: dict) -> None:
    adjustment_open = today <= date.fromisoformat(config["costco"]["price_adjustment_deadline"])
    for listing in listings:
        key = listing.dedup_key
        if key in state["alerted"]:
            continue
        verdict = evaluate(listing, config["criteria"], config["costco"], adjustment_open)

        if listing.canary:
            if verdict.kind == "deal":
                alerter.open_issue(*alerts.canary_issue(listing, verdict, config, today, heartbeat), labels=["canary"])
                heartbeat["canary"] = "sent"
            else:
                alerter.open_issue(*alerts.canary_failed_issue(listing, verdict), labels=["canary", "monitor-broken"])
                heartbeat["canary"] = f"FAILED to match: {'; '.join(verdict.failures)}"
            continue

        if verdict.kind is None:
            if len(verdict.failures) == 1:  # near misses help tune the criteria
                print(f"  near miss ({verdict.failures[0]}): {listing.title[:100]}")
            continue

        if verdict.kind == "price_adjustment":
            alerter.open_issue(*alerts.price_adjustment_issue(listing, verdict, config, today), labels=["price-adjustment"])
        else:
            alerter.open_issue(*alerts.deal_issue(listing, verdict, config, today), labels=["deal"])
        heartbeat["alerts_sent"] += 1
        state["alerted"][key] = {
            "title": listing.title[:200],
            "price": listing.price,
            "source": listing.source,
            "date": today.isoformat(),
        }
