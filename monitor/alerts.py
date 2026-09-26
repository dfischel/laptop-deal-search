"""Alerts are GitHub issues in this repo. GitHub emails you about each one.

Outside GitHub Actions (no GITHUB_TOKEN) alerts are printed instead of sent.
"""

import os
from datetime import date

import requests

API = "https://api.github.com"


class Alerter:
    def __init__(self, config: dict, dry_run: bool = False):
        self.config = config
        self.repo = os.environ.get("GITHUB_REPOSITORY")
        self.token = os.environ.get("GITHUB_TOKEN")
        self.dry_run = dry_run or not (self.repo and self.token)
        self.mention = config["alerts"].get("github_username", "")
        self.sent: list[tuple[str, str, list[str]]] = []  # every issue this run, sent or not (for tests and dry runs)

    def open_issue(self, title: str, body: str, labels: list[str]) -> None:
        if self.mention:
            # an @mention guarantees an email even if repo "watch" notifications are off
            body += f"\n\n---\n@{self.mention}"
        self.sent.append((title, body, labels))
        if self.dry_run:
            print(f"\n[dry run] would open issue: {title}\n  labels: {labels}\n" + "\n".join(f"  | {line}" for line in body.splitlines()))
            return
        response = requests.post(
            f"{API}/repos/{self.repo}/issues",
            headers=self._headers(),
            json={"title": title[:250], "body": body, "labels": labels},
            timeout=30,
        )
        response.raise_for_status()
        print(f"Opened issue: {response.json()['html_url']}")

    def disable_workflow(self, workflow_file: str) -> None:
        if self.dry_run:
            print(f"[dry run] would disable workflow {workflow_file}")
            return
        response = requests.put(
            f"{API}/repos/{self.repo}/actions/workflows/{workflow_file}/disable",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        print(f"Disabled workflow {workflow_file}")

    def keep_workflow_enabled(self, workflow_file: str) -> None:
        """Belt and braces for GitHub's 60-day inactivity shutoff (the per-run heartbeat commit is the main defense).
        Re-enabling an already-enabled workflow is harmless. A failure here is logged, not fatal."""
        if self.dry_run:
            return
        try:
            requests.put(
                f"{API}/repos/{self.repo}/actions/workflows/{workflow_file}/enable",
                headers=self._headers(),
                timeout=30,
            ).raise_for_status()
        except requests.RequestException as exc:
            print(f"warning: couldn't re-enable workflow: {exc}")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}


# ---------- issue text ----------

def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "?"


def _spec_rows(specs: dict) -> str:
    def fmt(value, unit=""):
        return "not stated" if value is None else f"{value}{unit}"
    storage = specs.get("storage_gb")
    storage_text = "not stated" if storage is None else (f"{storage / 1000:g}TB" if storage >= 1000 else f"{storage}GB")
    return "\n".join([
        "| Spec | Listing says |",
        "|---|---|",
        f"| Price | {_money(specs.get('price'))} |",
        f"| CPU | {fmt(specs.get('cpu'))} |",
        f"| RAM | {fmt(specs.get('ram_gb'), 'GB')} |",
        f"| Storage | {storage_text} |",
        f"| Screen | {fmt(specs.get('screen_in'), ' in')} |",
        f"| Thunderbolt 4 / USB4 | {'yes' if specs.get('thunderbolt') else 'not stated'} |",
    ])


def criteria_summary(config: dict) -> str:
    c = config["criteria"]
    return "\n".join([
        f"- Windows laptop, Intel or AMD (no Snapdragon/Chromebook/Mac)",
        f"- CPU: {c['cpu_description']}",
        f"- {c['min_ram_gb']}GB+ RAM, {c['min_storage_gb'] / 1000:g}TB+ SSD",
        f"- {c['screen_min_in']:g}-{c['screen_max_in']:g}\" screen, at least one Thunderbolt 4 / USB4 port",
        f"- ${c['max_price']} or less, new condition",
    ])


def _days_left(deadline: str, today: date) -> str:
    days = (date.fromisoformat(deadline) - today).days
    return f"{deadline} ({days} days left)"


def deal_issue(listing, verdict, config: dict, today: date) -> tuple[str, str]:
    title = f"Deal: {_money(listing.price)} - {listing.title}"
    verify = "".join(f"\n- [ ] {item}" for item in verdict.verify) or "\n- Nothing - every criterion was stated in the listing."
    body = f"""**{listing.title}**

{listing.url}

Found on **{listing.source}**{f" (retailer: {listing.retailer})" if listing.retailer else ""}.

{_spec_rows(verdict.specs)}

### Check before buying
The listing didn't state these, so confirm them on the product page:{verify}

### If it checks out
Buy it, then return the Acer to Costco before **{_days_left(config['project']['return_deadline'], today)}**.

Close this issue once you've dealt with it."""
    return title, body


def price_adjustment_issue(listing, verdict, config: dict, today: date) -> tuple[str, str]:
    costco = config["costco"]
    title = f"Costco price drop: your Acer is {_money(listing.price)} (you paid {_money(costco['purchase_price'])})"
    body = f"""A deal post says your exact model is cheaper at Costco:

**{listing.title}**
{listing.url}

{_spec_rows(verdict.specs)}

### What to do
1. Check that Costco.com lists **{costco['model']}** at the lower price and that it's in stock.
2. Request a price adjustment through Costco.com (Orders & Returns) or chat. Costco refunds the difference for online orders within 30 days.
3. Deadline: **{_days_left(costco['price_adjustment_deadline'], today)}**."""
    return title, body


def reminder_issue(reminder: dict, config: dict, today: date) -> tuple[str, str]:
    costco = config["costco"]
    adjustment_open = today <= date.fromisoformat(costco["price_adjustment_deadline"])
    adjustment = (
        f"\n\nAlso check whether **{costco['model']}** is below {_money(costco['purchase_price'])}. If it is, request a "
        f"price adjustment before **{_days_left(costco['price_adjustment_deadline'], today)}**."
        if adjustment_open else ""
    )
    body = f"""{reminder['note']}

Costco blocks automated checks, so this one is manual. Browse https://www.costco.com/laptops.html for anything that meets:

{criteria_summary(config)}{adjustment}

Return-and-rebuy deadline: **{_days_left(config['project']['return_deadline'], today)}**."""
    return reminder["title"], body


def source_broken_issue(source: str, failures: int, error: str) -> tuple[str, str]:
    title = f"Deal source '{source}' has failed {failures} runs in a row"
    body = f"""The monitor couldn't read **{source}** for {failures} consecutive runs. The other sources keep running.

Last error:
```
{error}
```

The site may be down, have changed its layout, or started blocking bots. To check it, open the **Actions** tab, pick **Probe sources**, and click **Run workflow**. If the source has died for good, set `enabled = false` under `[sources.{source}]` in `config.toml`.

You'll get this alert again only if the source recovers and then breaks again."""
    return title, body


def health_section(heartbeat: dict) -> str:
    rows = ["| Source | Status | Listings parsed |", "|---|---|---|"]
    for name, info in heartbeat["sources"].items():
        status = info["status"]
        if status == "FAILED":
            status = f"**FAILED** {info['consecutive_failures']}x: {info['error'][:120]}"
        rows.append(f"| {name} | {status} | {info.get('listings', '-')} |")
    return "\n".join(rows)


def canary_issue(listing, verdict, config: dict, today: date, heartbeat: dict) -> tuple[str, str]:
    _, deal_body = deal_issue(listing, verdict, config, today)
    body = f"""This is the weekly **canary**, a fake listing pushed through the real pipeline: price and spec
parsing, matching, dedup, and this email. It proves the monitor is alive and able to alert.

**If a week goes by without one of these, the monitor has stopped.** See "Is it still running?" in the README.

### Source health this run
{health_section(heartbeat)}

<details><summary>What the canary alert looked like (same format as a real deal)</summary>

{deal_body}
</details>

Close this issue."""
    return f"[Canary] Weekly check-in {today.isoformat()}: monitor is alive", body


def canary_failed_issue(listing, verdict) -> tuple[str, str]:
    reasons = "".join(f"\n- {failure}" for failure in verdict.failures)
    body = f"""The weekly canary, a fake listing built to match your criteria, **did not match**. Reasons:
{reasons}

Listing tested: `{listing.title}`

Either `config.toml` was edited so the canary's specs no longer qualify (for example, the Core Ultra 9 288V
was removed from `cpu_allowed`), or the spec parsing is broken, in which case **real deals may be missed too**.
Run `python -m unittest` or the "Probe sources" workflow to narrow it down."""
    return "Monitor problem: the weekly canary failed to match", body


def finished_issue(config: dict) -> tuple[str, str]:
    return (
        "Laptop deal monitor has finished",
        f"The return window closed on {config['project']['return_deadline']}. The monitor has switched its own "
        "schedule off, so there will be no more alerts.\n\nYou can leave this repo as-is, archive it "
        "(Settings -> Archive this repository), or delete it.",
    )


def test_issue(config: dict) -> tuple[str, str]:
    return (
        "Test alert from the laptop deal monitor",
        f"If this reached your email, alerts are working. Close this issue.\n\nCurrently watching for:\n\n{criteria_summary(config)}",
    )
