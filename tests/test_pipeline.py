"""Whole-run tests: fake HTTP serving the saved fixtures, alerts captured instead of sent."""

import contextlib
import copy
import io
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

from monitor import __main__ as cli
from monitor.alerts import Alerter
from monitor.config import EMPTY_STATE, load_config
from monitor.pipeline import new_heartbeat, process_listings, run
from monitor.sources import Listing

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_config()  # the deployed config: direct Newegg scraping is off
CONFIG_WITH_NEWEGG = copy.deepcopy(CONFIG)  # for tests of the Newegg scraper, kept in case it's re-enabled
CONFIG_WITH_NEWEGG["sources"]["newegg"]["enabled"] = True
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)  # a Monday, inside the return window

CHALLENGE_PAGE = b"""<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="challenge-platform">Checking your browser before accessing the site.</div></body></html>"""
EMPTY_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title></channel></rss>"""


# URL fragments that pick out each source's request
SLICKDEALS, DEALNEWS, DEALNEWS_NEWEGG, NEWEGG = "feedburner.com", "Computers/Laptops", "s504/Newegg", "newegg.com/p/pl"


class FakeHttp:
    """Serves fixture files by URL. `overrides` maps a URL fragment to bytes or an exception."""

    def __init__(self, overrides=None):
        self.routes = list((overrides or {}).items()) + [
            (SLICKDEALS, (FIXTURES / "slickdeals.xml").read_bytes()),
            (DEALNEWS, (FIXTURES / "dealnews.xml").read_bytes()),
            (DEALNEWS_NEWEGG, (FIXTURES / "dealnews_newegg.xml").read_bytes()),
            (NEWEGG, (FIXTURES / "newegg_search.html").read_bytes()),
        ]

    def get(self, url):
        for fragment, body in self.routes:
            if fragment in url:
                if isinstance(body, Exception):
                    raise body
                return SimpleNamespace(content=body, text=body.decode("utf-8", "replace"), url=url)
        raise AssertionError(f"unexpected request: {url}")


def fresh_state():
    return copy.deepcopy(EMPTY_STATE)


def do_run(state, http=None, config=CONFIG, now=NOW, force_canary=False):
    alerter = Alerter(config, dry_run=True)
    heartbeat = new_heartbeat(now, config)
    with contextlib.redirect_stdout(io.StringIO()):
        run(config, state, alerter, http or FakeHttp(), now, heartbeat, force_canary=force_canary)
    return alerter.sent, heartbeat


def labels(sent):
    return [label for _, _, issue_labels in sent for label in issue_labels]


class HealthyRun(unittest.TestCase):
    def test_parsed_listings_none_matched_is_quiet(self):
        state = fresh_state()
        state["last_canary_week"] = "2026-W41"  # canary already sent this week
        sent, heartbeat = do_run(state)
        self.assertEqual(sent, [])
        self.assertEqual(heartbeat["sources"], {
            "slickdeals": {"status": "ok", "listings": 25},
            "dealnews": {"status": "ok", "listings": 25},
            "dealnews_newegg": {"status": "ok", "listings": 20},
            "newegg": {"status": "disabled"},
        })
        self.assertEqual(state["source_failures"], {"slickdeals": 0, "dealnews": 0, "dealnews_newegg": 0})


def blank_titles(feed: bytes) -> bytes:
    return re.sub(rb"<title>.*?</title>", b"<title></title>", feed, flags=re.S)


class SilentFailure(unittest.TestCase):
    """A blocked or redesigned source must count as broken, never as "no deals today"."""

    BROKEN = {
        "challenge page instead of a feed": {DEALNEWS_NEWEGG: CHALLENGE_PAGE},
        "HTML challenge from the feed host": {SLICKDEALS: CHALLENGE_PAGE},
        "valid feed with zero items": {DEALNEWS: EMPTY_FEED},
        "feed items lost their titles (redesign)": {SLICKDEALS: blank_titles((FIXTURES / "slickdeals.xml").read_bytes())},
        "connection error": {DEALNEWS: requests.ConnectionError("connection refused")},
        "HTTP 403 (what Newegg did)": {DEALNEWS_NEWEGG: requests.HTTPError("403 Client Error: Forbidden")},
    }

    def test_each_breakage_counts_as_failure(self):
        for description, override in self.BROKEN.items():
            with self.subTest(description):
                state = fresh_state()
                _, heartbeat = do_run(state, FakeHttp(override))
                broken = [name for name, info in heartbeat["sources"].items() if info["status"] == "FAILED"]
                self.assertEqual(len(broken), 1, heartbeat["sources"])
                self.assertEqual(state["source_failures"][broken[0]], 1)

    def test_alerts_once_at_threshold_then_resets_on_recovery(self):
        threshold = CONFIG["alerts"]["source_failure_threshold"]
        state = fresh_state()
        blocked = FakeHttp({DEALNEWS_NEWEGG: CHALLENGE_PAGE})
        broken_alerts = []
        for _ in range(threshold + 2):
            sent, _ = do_run(state, blocked)
            broken_alerts += [title for title, _, issue_labels in sent if "source-broken" in issue_labels]
        self.assertEqual(len(broken_alerts), 1, "exactly one alert per outage, not one per run")
        self.assertIn("dealnews_newegg", broken_alerts[0])

        do_run(state)  # recovers
        self.assertEqual(state["source_failures"]["dealnews_newegg"], 0)
        for _ in range(threshold):
            sent, _ = do_run(state, blocked)
        self.assertIn("source-broken", labels(sent), "a second outage alerts again")

    def test_other_sources_still_checked_when_one_breaks(self):
        _, heartbeat = do_run(fresh_state(), FakeHttp({DEALNEWS_NEWEGG: CHALLENGE_PAGE}))
        self.assertEqual(heartbeat["sources"]["slickdeals"]["status"], "ok")
        self.assertEqual(heartbeat["sources"]["dealnews"]["status"], "ok")

    def test_disabled_source_is_never_fetched(self):
        http = FakeHttp({NEWEGG: AssertionError("disabled source was fetched")})
        _, heartbeat = do_run(fresh_state(), http)
        self.assertEqual(heartbeat["sources"]["newegg"], {"status": "disabled"})

    def test_reenabled_source_still_broken_alerts_again(self):
        # Newegg was disabled after 80 straight failures. If re-enabled while still blocked,
        # it must alert at the threshold again, not count on silently from 81.
        state = fresh_state()
        state["source_failures"]["newegg"] = 80
        do_run(state)  # disabled: count forgotten
        self.assertNotIn("newegg", state["source_failures"])
        blocked = FakeHttp({NEWEGG: requests.HTTPError("403 Client Error: Forbidden")})
        alerts = []
        for _ in range(CONFIG["alerts"]["source_failure_threshold"]):
            sent, _ = do_run(state, blocked, config=CONFIG_WITH_NEWEGG)
            alerts += [title for title, _, issue_labels in sent if "source-broken" in issue_labels]
        self.assertEqual(len(alerts), 1)


class NeweggScraperIfReenabled(unittest.TestCase):
    """Direct Newegg scraping is off (Newegg blocks GitHub's servers), but its HTML health checks stay tested."""

    BROKEN = {
        "challenge page instead of results": CHALLENGE_PAGE,
        "listings present but prices gone (redesign)":
            (FIXTURES / "newegg_search.html").read_bytes().replace(b"price-current", b"price-renamed"),
        "listings present but titles gone (redesign)":
            (FIXTURES / "newegg_search.html").read_bytes().replace(b"item-title", b"item-heading"),
    }

    def test_healthy(self):
        _, heartbeat = do_run(fresh_state(), config=CONFIG_WITH_NEWEGG)
        self.assertEqual(heartbeat["sources"]["newegg"], {"status": "ok", "listings": 36})

    def test_each_breakage_counts_as_failure(self):
        for description, page in self.BROKEN.items():
            with self.subTest(description):
                _, heartbeat = do_run(fresh_state(), FakeHttp({NEWEGG: page}), config=CONFIG_WITH_NEWEGG)
                self.assertEqual(heartbeat["sources"]["newegg"]["status"], "FAILED")


def listing(url="https://www.newegg.com/p/N82E1", price=799.99, source="newegg"):
    title = f'Lenovo Yoga 16" Laptop, Intel Core Ultra 7 258V, 32GB RAM, 1TB SSD, Thunderbolt 4 ${price}'
    return Listing(source, f"{source}:x", title, url, price)


class Dedup(unittest.TestCase):
    def alerts_for(self, listings, state):
        alerter = Alerter(CONFIG, dry_run=True)
        with contextlib.redirect_stdout(io.StringIO()):
            process_listings(listings, CONFIG, state, alerter, NOW.date(), new_heartbeat(NOW, CONFIG))
        return [title for title, _, _ in alerter.sent]

    def test_same_listing_twice_in_one_run_alerts_once(self):
        self.assertEqual(len(self.alerts_for([listing(), listing()], fresh_state())), 1)

    def test_same_listing_next_run_does_not_alert(self):
        state = fresh_state()
        self.assertEqual(len(self.alerts_for([listing()], state)), 1)
        self.assertEqual(len(self.alerts_for([listing()], state)), 0)

    def test_price_change_realerts(self):
        state = fresh_state()
        self.assertEqual(len(self.alerts_for([listing(price=799.99)], state)), 1)
        realert = self.alerts_for([listing(price=749.99)], state)
        self.assertEqual(len(realert), 1)
        self.assertIn("$749.99", realert[0])
        self.assertEqual(len(self.alerts_for([listing(price=749.99)], state)), 0)

    def test_different_listings_same_price_both_alert(self):
        self.assertEqual(len(self.alerts_for(
            [listing(url="https://www.newegg.com/p/N82E1"), listing(url="https://www.newegg.com/p/N82E2")],
            fresh_state())), 2)

    def test_tracking_parameters_ignored(self):
        a = listing(url="https://slickdeals.net/f/123-laptop?utm_source=rss&utm_medium=RSS2", source="slickdeals")
        b = listing(url="https://slickdeals.net/f/123-laptop?utm_source=email", source="slickdeals")
        self.assertEqual(a.dedup_key, b.dedup_key)

    def test_meaningful_query_kept(self):
        a = listing(url="https://www.newegg.com/Product/ComboDealDetails?ItemList=Combo.1")
        b = listing(url="https://www.newegg.com/Product/ComboDealDetails?ItemList=Combo.2")
        self.assertNotEqual(a.dedup_key, b.dedup_key)


class Canary(unittest.TestCase):
    def test_weekly(self):
        state = fresh_state()
        sent, heartbeat = do_run(state)
        self.assertEqual(labels(sent), ["canary"])
        self.assertEqual(heartbeat["canary"], "sent")
        sent, _ = do_run(state, now=NOW + timedelta(hours=3))
        self.assertEqual(sent, [], "only once a week")
        sent, _ = do_run(state, now=NOW + timedelta(days=7))
        self.assertEqual(labels(sent), ["canary"], "again the next week")

    def test_canary_goes_through_real_formatting_and_reports_health(self):
        sent, _ = do_run(fresh_state(), FakeHttp({DEALNEWS_NEWEGG: CHALLENGE_PAGE}))
        title, body, _ = sent[0]
        self.assertIn("[Canary]", title)
        self.assertIn("| Price | $850.00 |", body)  # built at exactly the price limit
        self.assertIn("| RAM | 32GB |", body)
        self.assertIn("| dealnews_newegg | **FAILED** 1x", body)
        self.assertIn("| slickdeals | ok | 25 |", body)
        self.assertIn("| newegg | disabled | - |", body)

    def test_canary_that_stops_matching_raises_alarm(self):
        config = copy.deepcopy(CONFIG)
        config["criteria"]["cpu_allowed"] = [r"ryzen\s*ai\s*9"]  # an edit that makes the canary's CPU ineligible
        sent, heartbeat = do_run(fresh_state(), config=config)
        self.assertEqual(labels(sent), ["canary", "monitor-broken"])
        self.assertIn("did not match", sent[0][1])
        self.assertTrue(heartbeat["canary"].startswith("FAILED"))

    def test_canary_follows_price_override(self):
        config = copy.deepcopy(CONFIG)
        config["criteria"]["max_price"] = 2000
        sent, _ = do_run(fresh_state(), config=config)
        canary = next(body for title, body, _ in sent if "[Canary]" in title)
        self.assertIn("| Price | $2,000.00 |", canary)


class PriceOverride(unittest.TestCase):
    def test_2000_fires_on_real_listings(self):
        # The saved feeds have no match even at $2,000, so use the saved Newegg search page,
        # which has 10 real listings between $850 and $2,000.
        config = copy.deepcopy(CONFIG_WITH_NEWEGG)
        config["criteria"]["max_price"] = 2000
        state = fresh_state()
        state["last_canary_week"] = "2026-W41"
        sent, heartbeat = do_run(state, config=config)
        self.assertEqual(heartbeat["alerts_sent"], 10)
        vivobook = next(body for title, body, _ in sent if "Vivobook S 16" in title and "N82E16834236727" in body)
        self.assertIn("| Price | $1,299.99 |", vivobook)
        self.assertIn("| CPU | Ultra 7 255H |", vivobook)
        self.assertIn("- [ ] Thunderbolt 4 / USB4 port", vivobook)


class EndOfSeason(unittest.TestCase):
    def test_finishes_once_and_disables(self):
        state = fresh_state()
        after = datetime(2026, 12, 25, 3, 0, tzinfo=timezone.utc)
        with mock.patch.object(Alerter, "disable_workflow") as disable:
            sent, heartbeat = do_run(state, now=after)
            self.assertEqual([title for title, _, _ in sent], ["Laptop deal monitor has finished"])
            disable.assert_called_once()
            sent, _ = do_run(state, now=after + timedelta(hours=3))
            self.assertEqual(sent, [])
        self.assertTrue(heartbeat["finished"])


class DryRunSmoke(unittest.TestCase):
    """`python -m monitor --dry-run` end to end, with fixtures instead of the live sites."""

    def run_cli(self, *args):
        out = io.StringIO()
        with mock.patch.object(cli, "PoliteSession", lambda *a, **k: FakeHttp()), \
             mock.patch.object(cli, "save_state") as save_state, \
             mock.patch.object(cli, "save_heartbeat") as save_heartbeat, \
             mock.patch.dict("os.environ", {"GITHUB_TOKEN": "x", "GITHUB_REPOSITORY": "x/y"}), \
             mock.patch("monitor.alerts.requests") as http_client, \
             contextlib.redirect_stdout(out):
            cli.main(list(args))
        return out.getvalue(), save_state, save_heartbeat, http_client

    def test_dry_run_formats_everything_sends_nothing_saves_nothing(self):
        output, save_state, save_heartbeat, http_client = self.run_cli("--dry-run")
        self.assertIn("[dry run] would open issue: [Canary]", output)
        self.assertIn("| Price | $850.00 |", output)
        self.assertIn('"dealnews_newegg": {\n      "status": "ok",\n      "listings": 20', output)
        self.assertIn("1 issue(s) formatted, none sent; state not saved", output)
        http_client.post.assert_not_called()
        http_client.put.assert_not_called()
        save_state.assert_not_called()
        save_heartbeat.assert_not_called()

    def test_dry_run_with_price_override(self):
        output, *_ = self.run_cli("--dry-run", "--max-price", "2000")
        self.assertIn("price limit overridden for this run: $2,000.00", output)
        self.assertIn("| Price | $2,000.00 |", output)  # the canary is built at the overridden limit

    def test_threshold_alias(self):
        output, *_ = self.run_cli("--dry-run", "--threshold", "2000")
        self.assertIn("| Price | $2,000.00 |", output)

    def test_override_does_not_touch_config_file(self):
        before = (Path(__file__).parent.parent / "config.toml").read_bytes()
        self.run_cli("--dry-run", "--max-price", "2000")
        self.assertEqual((Path(__file__).parent.parent / "config.toml").read_bytes(), before)


class Heartbeat(unittest.TestCase):
    def test_written_every_run_even_with_nothing_to_report(self):
        out = io.StringIO()
        state = fresh_state()
        state["last_canary_week"] = "{}-W{:02d}".format(*datetime.now(timezone.utc).isocalendar()[:2])
        with mock.patch.object(cli, "PoliteSession", lambda *a, **k: FakeHttp()), \
             mock.patch.object(cli, "load_state", return_value=state), \
             mock.patch.object(cli, "save_state"), \
             mock.patch.object(cli, "save_heartbeat") as save_heartbeat, \
             mock.patch.dict("os.environ", {}, clear=True), \
             contextlib.redirect_stdout(out):
            cli.main([])
        save_heartbeat.assert_called_once()
        heartbeat = save_heartbeat.call_args.args[0]
        self.assertEqual(heartbeat["alerts_sent"], 0)
        self.assertEqual(heartbeat["sources"]["dealnews_newegg"]["status"], "ok")
        json.dumps(heartbeat)  # must be serializable

    def test_written_even_when_run_crashes(self):
        with mock.patch.object(cli, "PoliteSession", lambda *a, **k: FakeHttp()), \
             mock.patch.object(cli, "load_state", return_value=fresh_state()), \
             mock.patch.object(cli, "save_state"), \
             mock.patch.object(cli, "save_heartbeat") as save_heartbeat, \
             mock.patch.object(cli, "run", side_effect=RuntimeError("boom")), \
             mock.patch.dict("os.environ", {}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                cli.main([])
        self.assertIn("boom", save_heartbeat.call_args.args[0]["crashed"])


if __name__ == "__main__":
    unittest.main()
