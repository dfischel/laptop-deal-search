# Laptop deal monitor

Watches deal sites for a laptop that beats the Acer Aspire 16 (A16-52MT-91B0) I bought from Costco for
$899.99, and emails me when it finds one. It runs on GitHub's servers, so my own laptop doesn't need to be on.

It switches itself off after **Dec 24, 2026**, the end of the Costco return window.

## What it alerts on

**A better laptop** (the return-and-rebuy path, until Dec 24). It matches on specs, not model numbers:

- Windows laptop, Intel or AMD (no Snapdragon, Chromebook or Mac)
- CPU at least as good as my Core Ultra 9 288V: Core Ultra 7/9 Series 2 or newer, or Ryzen AI 7/9 300-series or newer
- 32GB+ RAM, 1TB+ SSD, 14-16" screen, a Thunderbolt 4 / USB4 port
- $850 or less, new (no refurbished, open-box, or third-party sellers who upgrade RAM and storage themselves)

Deal titles often leave out ports and screen size. Those listings still alert, and the email lists what to
check on the product page before buying. A listing that only mentions "USB-C 3.2" doesn't count as having
Thunderbolt, so Thunderbolt goes on that checklist.

**My exact Acer cheaper at Costco** (the price-adjustment path, until Oct 25). This gets its own email.
Costco blocks automated checks, so this only catches price drops that someone posts on a deal site.

**Reminders** on Oct 18, Nov 20, Nov 30 and Dec 17 to browse Costco.com by hand.

## The emails I'll get

Each email comes from a GitHub **issue** opened in this repo, and each issue @-mentions my username, so the
email arrives even if I'm not watching the repo. Close each issue once it's dealt with.

| Email | Meaning |
|---|---|
| **Deal: $…** | A laptop that meets the criteria |
| **Costco price drop** | My exact Acer is cheaper: request a price adjustment |
| **Reminder: …** | Time to check Costco.com by hand |
| **[Canary] Weekly check-in** | Once a week (Monday): proof the monitor is alive, plus a health report for each deal site |
| **Deal source '…' has failed** | A site has been blocked, down, or redesigned for 3 runs in a row |
| **Monitor problem: the weekly canary failed** | The matching logic is broken, or `config.toml` was edited so that even the test listing no longer qualifies |
| **Run failed** (sent by GitHub itself) | The monitor crashed |

**One email per listing, per price.** A listing I've already been alerted about stays quiet, unless its
price changes, which sends a new alert. Alerted listings are kept in `state.json`.

## Is it still running?

The weekly **canary** email is the main signal. **If a Monday passes without one, something is wrong.**
The canary is a fake listing pushed through the real pipeline: parsing, matching, dedup and email. Missing
canaries catch the failures nothing else can report, such as the schedule being switched off.

To check directly:

1. **Is the schedule on?** In PowerShell:
   ```powershell
   gh workflow list --all --repo dfischel/laptop-deal-search
   ```
   "Laptop deal monitor" should say **active**. `disabled_inactivity` means GitHub paused it; `disabled_manually`
   means someone switched it off. To turn it back on:
   ```powershell
   gh workflow enable monitor.yml --repo dfischel/laptop-deal-search
   ```
   Or on github.com: the **Actions** tab shows a yellow banner with an **Enable workflow** button.

2. **When did it last run?**
   ```powershell
   gh run list --workflow monitor.yml --repo dfischel/laptop-deal-search --limit 5
   ```
   Runs should be at most about 3 hours apart. Or open [`heartbeat.json`](heartbeat.json) on github.com: it's
   rewritten on every run with the time, what each deal site returned, and a link to the run's log.

**Why the schedule could stop:** GitHub pauses scheduled workflows in a repo with no activity for 60 days, and
doesn't reliably email about it. This project runs about 90 days, so day 60 would land just before Black Friday.
To prevent that, every run commits `heartbeat.json`, which counts as activity, and also asks GitHub to keep the
workflow enabled. The canary is there in case both fail.

---

## Changing the criteria

Everything is in **[`config.toml`](config.toml)**: price limit, RAM, storage, screen size, CPU list, excluded
words, reminder dates, the deadline, and each site's health-check limits.

1. On github.com, open `config.toml` and click the **pencil icon** (Edit).
2. Change the number or text. Keep the formatting: quotes around text, none around numbers.
3. Click **Commit changes**.

The next run uses the new settings. If an edit accidentally rules out the canary's test listing, the next
weekly canary says so.

## Changing the schedule

The schedule is in **[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml)**, in the `cron:` lines.
Edit it the same way as the config.

Cron times are in **UTC**: 4 hours ahead of US Eastern until Nov 1, then 5. The five fields are
`minute hour day-of-month month day-of-week`:

| Line | Meaning |
|---|---|
| `"7 */3 * * *"` | 7 minutes past every 3rd hour, every day (the normal schedule) |
| `"37 * 20-30 11 *"` | 37 minutes past every hour, Nov 20-30 |
| `"37 * 1-5 12 *"` | 37 minutes past every hour, Dec 1-5 |

For example, `"7 */6 * * *"` means every 6 hours. GitHub sometimes starts scheduled runs a few minutes late.

## Testing it

**From github.com** (no setup needed): **Actions** tab → **Laptop deal monitor** → **Run workflow**. The options:

- **Dry run:** does everything against the live sites and prints each alert it *would* send in the run's log.
  Sends nothing and saves nothing.
- **Temporary price limit:** for example `2000`, to see the pipeline fire on real listings. It applies to that
  one run only, and `config.toml` isn't changed. **Tick "Dry run" as well**, or you'll get a real email for
  every listing under $2,000.
- **Send the canary now** or **Also send a plain test alert:** confirm that emails arrive.

**On my own computer** (requires Python 3.11+; in PowerShell, from this folder):

```powershell
pip install -r requirements.txt
python -m monitor --dry-run                    # full live run, prints alerts incl. the canary, changes nothing
python -m monitor --dry-run --max-price 2000   # same, with a temporary price limit
python -m unittest                             # all tests, no network needed
python tools/probe_sources.py                  # check each deal site right now
```

The tests (`tests/`) cover the matching boundaries (32GB vs 16GB, $850 vs $851, USB4 vs USB-C 3.2, Snapdragon,
resellers), dedup, the canary, and "blocked site vs quiet market". They also parse **real saved pages** from
each site (`tests/fixtures/`), so a parser change that breaks extraction is caught immediately.

The saved pages don't change on their own, so they don't detect a site redesign. The monitor's
health check does that on every live run and emails the "source failed" alert. To see what changed when that
happens, run `python tools/refresh_fixtures.py`, then `python -m unittest`, and compare the failures against the
new pages.

## Stopping it

**It stops by itself.** The first run after Dec 24 sends a "monitor has finished" email and switches the
workflow off.

To stop it early: **Actions** tab → **Laptop deal monitor** (left sidebar) → **⋯** menu (top right) →
**Disable workflow**. Or from PowerShell:

```powershell
gh workflow disable monitor.yml --repo dfischel/laptop-deal-search
```

Afterwards you can delete the repo (Settings → scroll to the bottom → Delete this repository) or leave it.

## Where it looks

| Source | What it reads | Normal result |
|---|---|---|
| Slickdeals | Official front-page RSS feed | 25 items |
| DealNews | Laptops RSS feed | 25 items |
| Newegg | Three searches, sold by Newegg, lowest price first | ~36 per search |

Costco.com and BestBuy.com block bots, so they aren't checked directly. Reddit's robots.txt forbids
automated access, so it isn't used. Newegg searches are limited to items sold by Newegg itself: without that
filter the cheapest results are all third-party resellers. Every request respects the site's robots.txt,
identifies itself honestly, and waits 3 seconds between requests to the same site.
