"""Personal stock tracker: checks a (delayed) price, shows it in your currency, and pushes alerts
to your phone via ntfy.sh. Also writes the data for the phone app (site/data.json).

Usage:
  py stock_watch.py            # check every few minutes, forever
  py stock_watch.py --once     # check one time and exit
  py stock_watch.py --test     # send a test notification to your phone
  py stock_watch.py --price    # just print the current price
  py stock_watch.py --app-data # write site/data.json for the app

Settings live in config.json next to this file. Only uses the Python standard library.
"""

import base64
import json
import os
import secrets
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
TOPIC_PATH = HERE / "ntfy_topic.txt"
APP_DATA_PATH = HERE / "site" / "data.json"

DEFAULT_CONFIG = {
    "symbol": "TTWO",
    "currency": "EUR",
    "ntfy_server": "https://ntfy.sh",
    "check_every_minutes": 5,
    # Alert when price goes above / below these levels, in your currency (null turns one off).
    "alert_above": 225.0,
    "alert_below": 190.0,
    # Alert when the price moves this many percent (up or down) in one day.
    "alert_daily_move_percent": 4.0,
    # Send one summary after the market closes each trading day.
    "daily_summary": True,
}

USER_AGENT = "Mozilla/5.0 (personal stock watcher)"
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


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
        save_json(CONFIG_PATH, DEFAULT_CONFIG)
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}

    # strip("﻿") drops an invisible byte-order mark that Windows tools sometimes add.
    topic = os.environ.get("NTFY_TOPIC", "").strip().strip("﻿").strip()
    if not topic and TOPIC_PATH.exists():
        topic = TOPIC_PATH.read_text(encoding="utf-8-sig").strip()
    cfg["ntfy_topic"] = topic or None
    return cfg


def require_topic(cfg):
    if cfg["ntfy_topic"]:
        return
    if os.environ.get("GITHUB_ACTIONS"):
        sys.exit("NTFY_TOPIC secret is not set. Add it under Settings > Secrets and variables > Actions.")
    cfg["ntfy_topic"] = "stockwatch-" + secrets.token_hex(6)
    TOPIC_PATH.write_text(cfg["ntfy_topic"], encoding="utf-8")
    print(f"Created your private topic: {cfg['ntfy_topic']} (saved in ntfy_topic.txt)")


def chart(symbol, range_="1d", interval="1d"):
    """Yahoo Finance's public chart endpoint (unofficial, delayed, may change)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)["chart"]["result"][0]


def series(result):
    """(timestamp, close) pairs, skipping gaps."""
    ts = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    return [(t, c) for t, c in zip(ts, closes) if c is not None]


def fx_pair(src, dst):
    return None if src == dst else f"{src}{dst}=X"


def get_quote(cfg):
    meta = chart(cfg["symbol"])["meta"]
    src = meta.get("currency", "USD")
    dst = cfg.get("currency") or src
    pair = fx_pair(src, dst)
    rate = chart(pair)["meta"]["regularMarketPrice"] if pair else 1.0

    price_src = meta["regularMarketPrice"]
    prev_src = meta.get("chartPreviousClose") or meta.get("previousClose") or price_src
    regular = meta["currentTradingPeriod"]["regular"]
    conv = lambda v: None if v is None else round(v * rate, 2)
    return {
        "symbol": meta["symbol"],
        "name": meta.get("longName") or meta.get("shortName", cfg["symbol"]),
        "currency": dst,
        "source_currency": src,
        "fx_rate": rate,
        "fx_pair": pair,
        "price": conv(price_src),
        "price_source": price_src,
        "prev_close": conv(prev_src),
        # % change in the stock's own currency, so currency moves don't fake a stock move.
        "change_pct": (price_src - prev_src) / prev_src * 100 if prev_src else 0.0,
        "day_high": conv(meta.get("regularMarketDayHigh")),
        "day_low": conv(meta.get("regularMarketDayLow")),
        "week52_high": conv(meta.get("fiftyTwoWeekHigh")),
        "week52_low": conv(meta.get("fiftyTwoWeekLow")),
        "market_time": meta.get("regularMarketTime", 0),
        "session_start": regular["start"],
        "session_end": regular["end"],
    }


def money(cfg_or_q, value):
    cur = cfg_or_q.get("currency", "USD")
    sym = SYMBOLS.get(cur)
    return f"{sym}{value:.2f}" if sym else f"{value:.2f} {cur}"


def env_num(name):
    """Private numbers (your buy price / shares) come from GitHub secrets, never from the public repo."""
    raw = os.environ.get(name, "").strip().strip("﻿").replace(",", ".")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def position_line(q):
    """Your profit/loss line, if BUY_PRICE is set.
    With INVESTED (money put in) or SHARES it shows the total and what it's worth now; otherwise per share."""
    buy = env_num("BUY_PRICE")
    if not buy:
        return ""
    invested = env_num("INVESTED")
    shares = env_num("SHARES") or (invested / buy if invested else None)
    per = q["price"] - buy
    pct = per / buy * 100
    if shares:
        total = per * shares
        sign = "+" if total >= 0 else "-"
        return (f"\nYou: {sign}{money(q, abs(total))} ({pct:+.2f}%)"
                f" · worth {money(q, q['price'] * shares)}")
    sign = "+" if per >= 0 else "-"
    return f"\nYou: {sign}{money(q, abs(per))} per share ({pct:+.2f}%)"


def fmt(q):
    arrow = "▲" if q["change_pct"] >= 0 else "▼"
    return f"{money(q, q['price'])} {arrow} {q['change_pct']:+.2f}% today{position_line(q)}"


def notify(cfg, title, message, priority="default", tags=""):
    require_topic(cfg)
    url = f"{cfg['ntfy_server'].rstrip('/')}/{cfg['ntfy_topic']}"
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        # RFC 2047 encoding so "€" survives in the HTTP header; ntfy decodes it.
        headers={"Title": "=?UTF-8?B?" + base64.b64encode(title.encode("utf-8")).decode() + "?=",
                 "Priority": priority, "Tags": tags},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).close()
    print(f"  -> sent: {title} | {message}")


def check(cfg):
    q = get_quote(cfg)
    state = load_json(STATE_PATH, {})
    now = time.time()
    day = datetime.fromtimestamp(q["session_start"], timezone.utc).strftime("%Y-%m-%d")
    sym = q["symbol"]
    print(f"[{datetime.now():%H:%M}] {sym} {money(q, q['price'])} {q['change_pct']:+.2f}% today")

    # A new trading day resets the "already alerted" flags for daily moves.
    if state.get("day") != day:
        state = {**state, "day": day, "move_alerted": False, "summary_sent": False}

    # Price level alerts: fire once when the price crosses the line, re-arm when it goes back.
    above, below = cfg.get("alert_above"), cfg.get("alert_below")
    if above is not None:
        if q["price"] >= above and not state.get("above_hit"):
            notify(cfg, f"{sym} above {money(q, above)}", fmt(q), "high", "chart_with_upwards_trend")
            state["above_hit"] = True
        elif q["price"] < above:
            state["above_hit"] = False
    if below is not None:
        if q["price"] <= below and not state.get("below_hit"):
            notify(cfg, f"{sym} below {money(q, below)}", fmt(q), "high", "chart_with_downwards_trend")
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
        msg = f"Close {fmt(q)}\nDay range {money(q, q['day_low'])} – {money(q, q['day_high'])}"
        notify(cfg, f"{sym} daily summary", msg, "low", "bell")
        state["summary_sent"] = True

    save_json(STATE_PATH, state)
    return q


def write_app_data(cfg, q=None):
    """Everything the phone app shows, in one small JSON file."""
    q = q or get_quote(cfg)
    rate = q["fx_rate"]

    # Daily closes for ~6 months, each converted with that day's exchange rate.
    hist = series(chart(cfg["symbol"], "6mo", "1d"))
    if q["fx_pair"]:
        fx_by_day = {datetime.fromtimestamp(t, timezone.utc).date(): c
                     for t, c in series(chart(q["fx_pair"], "6mo", "1d"))}
        last = rate
        history = []
        for t, c in hist:
            last = fx_by_day.get(datetime.fromtimestamp(t, timezone.utc).date(), last)
            history.append([t, round(c * last, 2)])
    else:
        history = [[t, round(c, 2)] for t, c in hist]

    intraday = [[t, round(c * rate, 2)] for t, c in series(chart(cfg["symbol"], "1d", "5m"))]

    now = time.time()
    data = {
        **{k: q[k] for k in ("symbol", "name", "currency", "price", "prev_close", "change_pct",
                             "day_high", "day_low", "week52_high", "week52_low", "market_time")},
        "change": round(q["price"] - q["prev_close"], 2),
        "fx": {"pair": q["fx_pair"], "rate": rate, "source_currency": q["source_currency"]},
        "market_open": q["session_start"] <= now <= q["session_end"],
        "updated": int(now),
        "alerts": {
            "above": cfg.get("alert_above"),
            "below": cfg.get("alert_below"),
            "move_pct": cfg.get("alert_daily_move_percent"),
        },
        "intraday": intraday,
        "history": history,
    }
    APP_DATA_PATH.parent.mkdir(exist_ok=True)
    APP_DATA_PATH.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"  -> wrote {APP_DATA_PATH.relative_to(HERE)} ({len(history)} days, {len(intraday)} intraday points)")


def main():
    cfg = load_config()
    args = set(sys.argv[1:])

    if "--price" in args:
        q = get_quote(cfg)
        print(f"{q['name']} ({q['symbol']}): {fmt(q)}")
        return
    if "--test" in args:
        q = get_quote(cfg)
        notify(cfg, f"{q['symbol']} watcher is working", fmt(q), "default", "white_check_mark")
        return
    if "--once" in args or "--app-data" in args:
        q = check(cfg) if "--once" in args else None
        if "--app-data" in args:
            write_app_data(cfg, q)
        return

    require_topic(cfg)
    print(f"Watching {cfg['symbol']} every {cfg['check_every_minutes']} min. Ctrl+C to stop.")
    print(f"Phone topic: {cfg['ntfy_topic']}")
    while True:
        try:
            q = check(cfg)
            write_app_data(cfg, q)
        except Exception as e:  # network hiccups shouldn't kill the watcher
            print(f"  error: {e}")
        time.sleep(cfg["check_every_minutes"] * 60)


if __name__ == "__main__":
    main()
