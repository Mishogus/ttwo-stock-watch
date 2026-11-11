# TTWO stock watcher

Take-Two (TTWO) share price in euros: a phone app plus push alerts.
It runs free on GitHub Actions, so your PC can stay off.

## The app
Open **https://mishogus.github.io/ttwo-stock-watch/** in Chrome on your phone, then tap **⋮ → Add to Home screen → Install**.
It opens full-screen from its own icon and shows the price, a chart (1D / 1M / 3M / 6M), the day and 52-week ranges, and how far you are from your alerts.
Prices update every 5 minutes during US market hours.

## Alerts (ntfy)
1. Install **ntfy** from the Play Store.
2. Subscribe to your private topic (the repo secret `NTFY_TOPIC`), with instant delivery on.

Change alert levels in `config.json` (in euros):
- `alert_above` / `alert_below`: price lines (`null` turns one off)
- `alert_daily_move_percent`: alert on a big up/down day
- `daily_summary`: one recap after the market closes
- `currency`: `EUR`, `USD`, `GBP`, …

## Useful
- **Test it:** Actions tab → *Stock watch* → *Run workflow* → `test`.
- **Alerts stopped?** GitHub may pause schedules in quiet repos after ~60 days. Re-enable it in the Actions tab.
- Prices come from Yahoo's free, unofficial endpoint (about 15 min delayed) and could stop working someday.
- Euro prices use the current USD→EUR rate. The % change is in dollars, so exchange-rate moves don't look like stock moves.
