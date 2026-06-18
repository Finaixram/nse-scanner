"""
nse_scanner/app.py
==================
Flask web app — NSE Live OI Scanner Dashboard
Serves live PCR, Max Pain, top OI levels for Nifty & BankNifty.

Run: python app.py
Open: http://localhost:5050
"""

import sys
import os
import json
import datetime
from pathlib import Path

# ── Add parent dir for scrape_oi ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

try:
    from flask import Flask, render_template, jsonify
except ImportError:
    print("❌ Flask not installed.")
    print("   Run: pip install flask --break-system-packages")
    sys.exit(1)

from scrape_oi import get_combined_signal

app = Flask(__name__, template_folder="templates")

# ── In-memory cache (refresh every 3 min) ────────────────────────────────────
_cache = {"data": None, "fetched_at": None}
CACHE_SECONDS = 180


def get_data():
    """Return cached data or fetch fresh."""
    now = datetime.datetime.now()
    if _cache["data"] is None or (now - _cache["fetched_at"]).seconds > CACHE_SECONDS:
        _cache["data"] = get_combined_signal()
        _cache["fetched_at"] = now
    return _cache["data"]


@app.route("/")
def index():
    """Main dashboard."""
    data = get_data()
    return render_template("index.html", data=data)


@app.route("/api/data")
def api_data():
    """JSON endpoint for real-time refresh (called by JS every 3 min)."""
    # Force fresh fetch on API call
    _cache["fetched_at"] = None
    return jsonify(get_data())


@app.route("/api/refresh")
def api_refresh():
    """Force cache refresh."""
    _cache["fetched_at"] = None
    return jsonify({"status": "ok", "refreshed_at": datetime.datetime.now().isoformat()})


if __name__ == "__main__":
    print("=" * 55)
    print("  NSE Live OI Scanner — Starting")
    print("  Open: http://localhost:5050")
    print("  Press Ctrl+C to stop")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5050, debug=False)
