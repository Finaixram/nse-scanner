"""One-shot NSE signal check + Telegram alert on bias change. Called by GitHub Actions cron."""
import requests, os, sys

SCANNER = "https://nse-scanner-bz8s.onrender.com/api/signal"
BOT  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
LAST_FILE = "last_signal.txt"

EMOJI = {
    "BULLISH":         "\U0001F7E2",
    "BEARISH":         "\U0001F534",
    "NEUTRAL":         "\U0001F7E1",
    "BULLISH_PARTIAL": "\U0001F4C8",
    "BEARISH_PARTIAL": "\U0001F4C9",
}

try:
    r = requests.get(SCANNER, timeout=30)
    d = r.json()
except Exception as e:
    print(f"ERROR: {e}")
    raise SystemExit(0)

sig = d.get("signal", "SKIP")
if sig == "SKIP":
    print("Market closed or data unavailable")
    raise SystemExit(0)

last = open(LAST_FILE).read().strip() if os.path.exists(LAST_FILE) else ""
print(f"Previous: {last!r}  Current: {sig!r}")

if sig == last:
    print("No change - no alert")
    raise SystemExit(0)

emoji = EMOJI.get(sig, "")
msg = (f"{emoji} NSE Bias: {sig}\n"
       f"PCR: {d.get('pcr','N/A')} | Max Pain: {d.get('max_pain','N/A')}\n"
       f"Spot: {d.get('spot','N/A')} | {d.get('timestamp','')}")

if BOT and CHAT:
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT}/sendMessage",
        json={"chat_id": CHAT, "text": msg}, timeout=10)
    print(f"Telegram: {resp.status_code}")

open(LAST_FILE, "w").write(sig)
print(f"State saved: {sig}")
