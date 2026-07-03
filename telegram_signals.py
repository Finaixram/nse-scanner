"""
telegram_signals.py
===================
Polls /api/data every 30 min during market hours and sends Telegram
alerts when bias CHANGES. Skips is_demo=True or success=False data.

Env vars: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SCANNER_URL
"""

import os, time, datetime, urllib.request, json

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "8798747663:AAERtT14sv1oS8msRHVlGgky4fZpfOBydFM")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1332778697")
SCANNER_URL      = os.environ.get("SCANNER_URL",      "https://nse-scanner-bz8s.onrender.com")
POLL_SECONDS     = 30 * 60

_MARKET_OPEN  = (9, 15)
_MARKET_CLOSE = (15, 30)
_last_bias    = {"nifty": None, "banknifty": None}


def _ist_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def _is_market_open():
    now = _ist_now()
    if now.weekday() >= 5:
        return False
    cur       = now.hour * 60 + now.minute
    open_min  = _MARKET_OPEN[0]  * 60 + _MARKET_OPEN[1]
    close_min = _MARKET_CLOSE[0] * 60 + _MARKET_CLOSE[1]
    return open_min <= cur <= close_min


def _send_tg(text):
    try:
        url  = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
        req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[TG] Error: {e}")


def _fetch_data():
    try:
        url  = SCANNER_URL.rstrip("/") + "/api/data"
        resp = urllib.request.urlopen(url, timeout=20)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Poll] Fetch error: {e}")
        return None


def _bias_key(s):
    s = (s or "").upper()
    for kw in ("BULLISH", "BEARISH", "NEUTRAL", "UNAVAILABLE"):
        if kw in s: return kw
    return "UNKNOWN"


def _check_and_alert():
    data = _fetch_data()
    if not data: return
    for key, label in [("nifty", "Nifty 50"), ("banknifty", "Bank Nifty")]:
        idx = data.get(key, {})
        if idx.get("is_demo") or not idx.get("success"):
            continue
        bias_raw = idx.get("bias", "")
        bkey     = _bias_key(bias_raw)
        prev     = _last_bias[key]
        if prev is None:
            _last_bias[key] = bkey
            print(f"[Poll] {label}: initial bias = {bkey}")
            continue
        if bkey != prev:
            msg = (
                "\U0001f514 <b>BIAS CHANGE \u2014 " + label + "</b>\n\n"
                + prev + " \u27a1 " + bias_raw + "\n\n"
                + "Spot: <b>" + str(idx.get("spot_price")) + "</b>  |  PCR: <b>" + str(idx.get("pcr")) + "</b>\n"
                + "Max Pain: <b>" + str(idx.get("max_pain")) + "</b>\n"
                + str(idx.get("bias_reason", "")) + "\n\n"
                + "Source: " + str(idx.get("source", "unknown")) + "\n"
                + "\U0001f550 " + _ist_now().strftime("%d %b %Y %H:%M IST")
            )
            _send_tg(msg)
            _last_bias[key] = bkey
        else:
            print(f"[Poll] {label}: no change ({bkey})")


def run():
    print("[NSE Signal Bot] Starting.")
    _send_tg(
        "\U0001f680 <b>NSE Signal Bot started</b>\n"
        "Watching Nifty 50 + Bank Nifty for bias changes.\n"
        "Alerts fire on NEUTRAL \u2194 BULLISH \u2194 BEARISH transitions."
    )
    while True:
        try:
            if _is_market_open():
                print(f"[Poll] {_ist_now().strftime('%H:%M IST')} - checking...")
                _check_and_alert()
            else:
                print(f"[Poll] {_ist_now().strftime('%H:%M IST')} - market closed.")
        except Exception as e:
            print(f"[Poll] error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
