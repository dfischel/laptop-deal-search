# Laptop deal monitor

Watches deal sites for a laptop that beats the Acer Aspire 16 (A16-52MT-91B0) I bought from Costco for
$899.99, and emails me when it finds one. It runs on GitHub's servers, so my own laptop doesn't need to be on.

It switches itself off after **Dec 24, 2026**, the end of the Costco return window.

## What it alerts on

**A better laptop** (the return-and-rebuy path, until Dec 24). It matches on specs, not model numbers:

- Windows laptop, Intel or AMD (no Snapdragon, Chromebook or Mac)
- CPU at least as good as my Core Ultra 9 288V: Core Ultra 7/9 Series 2 or newer, or Ryzen AI 7/9 300-series or newer
- 32GB+ RAM, 1TB+ SSD, 14-16" screen, a Thunderbolt 4 / USB4 port
- $850 or less, new (no refurbished, open-box or third-party "upgraded" listings)

Deal titles often leave out ports and screen size. Those listings still alert, and the email lists what to
check on the product page before buying.

**My exact Acer cheaper at Costco** (the price-adjustment path, until Oct 25). This gets its own email.
Costco blocks automated checks, so this only catches price drops that someone posts on a deal site.

**Reminders** on Oct 18, Nov 20, Nov 30 and Dec 17 to browse Costco.com by hand.

## How alerts reach me

Each alert is a GitHub **issue** in this repo, which GitHub emails me about. Every issue @-mentions my
username, so the email arrives even if I'm not watching the repo. Close the issue once it's dealt with.

I only get one email per listing. The listings I've already been alerted about are kept in `state.json`.

If a deal site stops working for about a day, I get one "source broken" email. Everything else keeps running.

## Where it looks

| Source | What it reads |
|---|---|
| Slickdeals | Official front-page RSS feed |
| DealNews | Laptops RSS feed |
| Newegg | Three searches sorted by lowest price |

Costco.com and BestBuy.com block bots, so they aren't checked directly. Reddit's robots.txt forbids
automated access, so it isn't used. Every request respects the site's robots.txt, identifies itself
honestly, and waits 3 seconds between requests to the same site.

---

## Changing the criteria

Everything is in **[`config.toml`](config.toml)**: price limit, RAM, storage, screen size, CPU list, excluded
words, reminder dates and the deadline.

1. On github.com, open `config.toml` and click the **pencil icon** (Edit).
2. Change the number or text. Keep the formatting: quotes around text, none around numbers.
3. Click **Commit changes**.

The next run uses the new settings.

## Changing the schedule

The schedule is in **[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml)**, in the `cron:` lines.
Edit it the same way as the config.

Cron times are in **UTC**, which is 5 hours ahead of US Eastern and 8 ahead of Pacific. The five fields are
`minute hour day-of-month month day-of-week`:

| Line | Meaning |
|---|---|
| `"7 */3 * * *"` | 7 minutes past every 3rd hour, every day (the normal schedule) |
| `"37 * 20-30 11 *"` | 37 minutes past every hour, Nov 20-30 |
| `"37 * 1-5 12 *"` | 37 minutes past every hour, Dec 1-5 |

For example, `"7 */6 * * *"` means every 6 hours and `"7 14 * * *"` means once a day at 14:07 UTC. Delete a
line to drop it. GitHub sometimes starts scheduled runs a few minutes late.

## Running it now

**Actions** tab → **Laptop deal monitor** → **Run workflow**. Tick **"Also send a test alert"** to check that
emails arrive.

## Stopping it

**It stops by itself.** The first run after Dec 24 sends a "monitor has finished" email and switches the
workflow off.

To stop it early: **Actions** tab → **Laptop deal monitor** (left sidebar) → **⋯** menu (top right) →
**Disable workflow**. Or from PowerShell:

```powershell
gh workflow disable monitor.yml --repo dfischel/laptop-deal-search
```

Afterwards you can delete the repo (Settings → scroll to the bottom → Delete this repository) or leave it.

## If something breaks

- **A "source broken" email:** Actions tab → **Probe sources** → **Run workflow**. The log shows what
  each site returns. If a source is dead for good, set `enabled = false` under it in `config.toml`.
- **A "Run failed" email from GitHub:** the monitor itself crashed. The run's log (Actions tab → click the
  failed run) shows the error.
- **Too many or wrong alerts:** tighten `config.toml`. Each run's log lists "near misses", listings that
  failed exactly one criterion, which helps show which setting to adjust.

## Running it on my own computer (optional)

Requires Python 3.11+. In PowerShell, from this folder:

```powershell
pip install -r requirements.txt
python -m monitor --dry-run      # check the sites and print what would alert; changes nothing
python tools/probe_sources.py    # check each source is working
python -m unittest               # run the tests
```
