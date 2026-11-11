"""Personal stock tracker: checks a (delayed) price and pushes alerts to your phone via ntfy.sh.

Usage:
  py stock_watch.py            # check every few minutes, forever
  py stock_watch.py --once     # check one time and exit
  py stock_watch.py --test     # send a test notification to your phone
  py stock_watch.py --price    # just print the current price

Settings live in config.json next to this file (created on first run).
Only uses the Python standard library.
"""

import json
import os
import secrets
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
TOPIC_PATH = HERE / "ntfy_topic.txt"

DEFAULT_CONFIG = {
    "symbol": "TTWO",
    # Random topic name so strangers can't guess it. Anyone who knows it can read your alerts.
    "ntfy_topic": None,
    "ntfy_server": "https://ntfy.sh",
    "check_every_minutes": 5,
    # Alert when price goes above / below these levels (set to null to turn off).
    "alert_above": 250.0,
    "alert_below": 210.0,
    # Alert when the price moves this many percent (up or down) in one day.
    "alert_daily_move_percent": 4.0,
    # Send one summary after the market closes each trading day.
    "daily_summary": True,
}

USER_AGENT = "Mozilla/5.0 (personal stock watcher)"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config():
    """Settings come from config.json. The private ntfy topic is kept OUT of it (the repo is public):
    it comes from the NTFY_TOPIC environment variable (a GitHub secret) or the git-ignored ntfy_topic.txt."""
    if not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, {k: v for k, v in DEFAULT_CONFIG.items() if k != "ntfy_topic"})
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}

    topic = os.environ.get("NTFY_TOPIC", "").strip() or cfg.get("ntfy_topic")
    if not topic and TOPIC_PATH.exists():
        topic = TOPIC_PATH.read_text(encoding="utf-8").strip()
    if not topic:
        if os.environ.get("GITHUB_ACTIONS"):
            sys.exit("NTFY_TOPIC secret is not set. Add it under Settings > Secrets and variables > Actions.")
        topic = "stockwatch-" + secrets.token_hex(6)
        TOPIC_PATH.write_text(topic, encoding="utf-8")
        print(f"Created your private topic: {topic} (saved in ntfy_topic.txt)")
    cfg["ntfy_topic"] = topic
    return cfg


def get_quote(symbol):
    """Fetch a delayed quote from Yahoo Finance's public chart endpoint (unofficial, may change)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    meta = data["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    regular = meta["currentTradingPeriod"]["regular"]
    return {
        "symbol": meta["symbol"],
        "name": meta.get("shortName", symbol).strip().rstrip(","),
        "price": price,
        "prev_close": prev,
        "change_pct": (price - prev) / prev * 100 if prev else 0.0,
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "market_time": meta.get("regularMarketTime", 0),
        "session_start": regular["start"],
        "session_end": regular["end"],
    }


def notify(cfg, title, message, priority="default", tags=""):
    url = f"{cfg['ntfy_server'].rstrip('/')}/{cfg['ntfy_topic']}"
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).close()
    print(f"  -> sent: {title} | {message}")


def fmt(q):
    arrow = "▲" if q["change_pct"] >= 0 else "▼"
    return f"${q['price']:.2f} {arrow} {q['change_pct']:+.2f}% today"


def check(cfg):
    q = get_quote(cfg["symbol"])
    state = load_json(STATE_PATH, {})
    now = time.time()
    day = datetime.fromtimestamp(q["session_start"]).strftime("%Y-%m-%d")
    sym = q["symbol"]
    print(f"[{datetime.now():%H:%M}] {sym} {fmt(q)}")

    # A new trading day resets the "already alerted" flags for daily moves.
    if state.get("day") != day:
        state = {**state, "day": day, "move_alerted": False, "summary_sent": False}

    # Price level alerts: fire once when the price crosses the line, re-arm when it goes back.
    above, below = cfg.get("alert_above"), cfg.get("alert_below")
    if above is not None:
        if q["price"] >= above and not state.get("above_hit"):
            notify(cfg, f"{sym} above ${above:.2f}", fmt(q), "high", "chart_with_upwards_trend")
            state["above_hit"] = True
        elif q["price"] < above:
            state["above_hit"] = False
    if below is not None:
        if q["price"] <= below and not state.get("below_hit"):
            notify(cfg, f"{sym} below ${below:.2f}", fmt(q), "high", "chart_with_downwards_trend")
            state["below_hit"] = True
        elif q["price"] > below:
            state["below_hit"] = False

    # Big daily move.
    move = cfg.get("alert_daily_move_percent")
    if move and abs(q["change_pct"]) >= move and not state.get("move_alerted"):
        notify(cfg, f"{sym} big move today", fmt(q), "high", "rotating_light")
        state["move_alerted"] = True

    # End-of-day summary (after the regular session closes).
    if cfg.get("daily_summary") and now > q["session_end"] and not state.get("summary_sent"):
        msg = f"Close {fmt(q)}\nDay range ${q['day_low']:.2f} – ${q['day_high']:.2f}"
        notify(cfg, f"{sym} daily summary", msg, "low", "bell")
        state["summary_sent"] = True

    save_json(STATE_PATH, state)
    return q


def main():
    cfg = load_config()
    args = set(sys.argv[1:])

    if "--price" in args:
        q = get_quote(cfg["symbol"])
        print(f"{q['name']} ({q['symbol']}): {fmt(q)}")
        return
    if "--test" in args:
        q = get_quote(cfg["symbol"])
        notify(cfg, f"{q['symbol']} watcher is working", fmt(q), "default", "white_check_mark")
        return
    if "--once" in args:
        check(cfg)
        return

    print(f"Watching {cfg['symbol']} every {cfg['check_every_minutes']} min. Ctrl+C to stop.")
    print(f"Phone topic: {cfg['ntfy_topic']}")
    while True:
        try:
            check(cfg)
        except Exception as e:  # network hiccups shouldn't kill the watcher
            print(f"  error: {e}")
        time.sleep(cfg["check_every_minutes"] * 60)


if __name__ == "__main__":
    main()
