# TTWO stock watcher

Checks Take-Two (TTWO) every 5 minutes during US market hours and pushes alerts to your phone with [ntfy](https://ntfy.sh).
It runs free on GitHub Actions, so your PC can stay off.

## Phone setup (one time)
1. Install **ntfy** from the Play Store.
2. Tap **+** and subscribe to your private topic (it's in the repo secret `NTFY_TOPIC`).
3. In Android settings, let ntfy run in the background without battery limits.

## Alerts
Edit `config.json` in the repo:
- `alert_above` / `alert_below`: price lines (`null` turns one off)
- `alert_daily_move_percent`: alert on a big up/down day
- `daily_summary`: one recap after the market closes

## Useful
- **Test it:** Actions tab → *Stock watch* → *Run workflow* → `test`.
- **Alerts stopped?** GitHub may pause schedules in quiet repos after ~60 days. Re-enable it in the Actions tab.
- Prices come from Yahoo's free, unofficial endpoint. They can lag and could stop working someday.
- Scheduled runs can start a few minutes late.

## Run on your PC instead
`py stock_watch.py` (keeps checking) or `py stock_watch.py --price`. Your topic is in `ntfy_topic.txt`, which isn't uploaded.
