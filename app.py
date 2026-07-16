"""NSE OI Scanner - Kite Connect (primary) -> nsepython (fallback) -> yfinance (last resort)

Fixed 2026-07-16: previous version only ever called bare yfinance, which Yahoo has been
blocking from Render's shared IPs. curl_cffi/bumped yfinance were added to requirements.txt
on 2026-07-15 but never wired into this file - that "fix" never actually ran. This version:
  1. Tries Kite Connect option-chain data first (needs KITE_API_KEY + KITE_ACCESS_TOKEN env
     vars on Render - the access token expires daily and must be refreshed by Ram via his
     usual kite.zerodha.com login flow, then updated in Render's dashboard env vars).
  2. Falls back to nsepython (direct NSE scrape, no daily token needed).
  3. Falls back to yfinance with a curl_cffi browser-impersonation session as a last resort.
  4. On total failure, returns the REAL last exception from each source instead of a generic
     static string, so future debugging doesn't require re-deriving this from scratch.
"""
import datetime
import os
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>NSE Scanner - Finaixram</title>
<style>body{background:#0d1117;color:#e6edf3;font-family:sans-serif;padding:20px}
h1{color:#58a6ff;text-align:center}.card{background:#161b22;border:1px solid #30363d;
border-radius:8px;padding:20px;margin:15px auto;max-width:700px}
.bull{color:#3fb950;font-weight:bold;font-size:1.3em}
.bear{color:#f85149;font-weight:bold;font-size:1.3em}
.neut{color:#d29922;font-weight:bold;font-size:1.3em}
.err{background:#6e2d2d;border:1px solid #f85149;border-radius:6px;padding:10px;margin:10px auto;max-width:700px}
.src{color:#6e7681;font-size:0.8em}
button{background:#238636;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer}</style>
</head><body><h1>NSE Live OI Scanner</h1>
<div id="c">Loading...</div>
<div style="text-align:center;margin:20px"><button onclick="load()">Refresh</button></div>
<script>
async function load(){
  document.getElementById('c').innerHTML='Fetching...';
  try{
    const d=await fetch('/api/data').then(r=>r.json());
    if(!d.success){document.getElementById('c').innerHTML='<div class="err">'+d.error+'</div>';return}
    let h='';
    for(const[n,i]of Object.entries(d.indices||{})){
      const c=i.bias==='BULLISH'?'bull':i.bias==='BEARISH'?'bear':'neut';
      h+='<div class="card"><h2>'+n+' <span class="src">['+(i.source||'?')+']</span></h2>'
        +'<div>Bias: <span class="'+c+'">'+i.bias+'</span></div>'
        +'<div style="color:#8b949e">Spot: <b>'+i.spot+'</b> | PCR: <b>'+i.pcr+'</b> | Max Pain: <b>'+i.max_pain+'</b></div>'
        +'<div style="color:#8b949e">Top CE: '+i.top_ce_strike+' | Top PE: '+i.top_pe_strike+'</div></div>';
    }
    h+='<div class="card" style="color:#8b949e">Updated: '+d.timestamp+' | Signal: <b>'+d.combined_signal+'</b></div>';
    document.getElementById('c').innerHTML=h;
  }catch(e){document.getElementById('c').innerHTML='<div class="err">'+e+'</div>'}
}
load();setInterval(load,300000);
</script></body></html>"""

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_bias(pcr):
    if pcr > 1.3:
        return "BULLISH"
    if pcr < 0.7:
        return "BEARISH"
    return "NEUTRAL"


def _max_pain_dict(ce_oi, pe_oi):
    """ce_oi / pe_oi are {strike: openInterest} dicts."""
    try:
        strikes = sorted(set(ce_oi) | set(pe_oi))
        if not strikes:
            return 0
        best, best_strike = float("inf"), strikes[0]
        for s in strikes:
            pain = sum(v * (s - k) for k, v in ce_oi.items() if k < s)
            pain += sum(v * (k - s) for k, v in pe_oi.items() if k > s)
            if pain < best:
                best, best_strike = pain, s
        return int(best_strike)
    except Exception:
        return 0


# index name -> per-source identifiers
SOURCES = {
    "NIFTY 50":   {"kite_name": "NIFTY",     "kite_spot": "NSE:NIFTY 50",    "yf": "^NSEI",    "nse": "NIFTY"},
    "BANK NIFTY": {"kite_name": "BANKNIFTY", "kite_spot": "NSE:NIFTY BANK",  "yf": "^NSEBANK", "nse": "BANKNIFTY"},
}

# ---------------------------------------------------------------------------
# Source 1: Kite Connect (primary - needs daily-refreshed access token)
# ---------------------------------------------------------------------------

KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "")

_kite_instruments_cache = {"ts": 0, "rows": None}


def _kite_headers():
    return {
        "Authorization": f"token {KITE_API_KEY}:{KITE_ACCESS_TOKEN}",
        "X-Kite-Version": "3",
    }


def _kite_nfo_instruments():
    import csv
    import io
    import time as _time

    if _kite_instruments_cache["rows"] and _time.time() - _kite_instruments_cache["ts"] < 3600:
        return _kite_instruments_cache["rows"]
    r = requests.get("https://api.kite.trade/instruments/NFO", headers=_kite_headers(), timeout=20)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    _kite_instruments_cache["rows"] = rows
    _kite_instruments_cache["ts"] = _time.time()
    return rows


def fetch_index_kite(kite_name, kite_spot):
    if not KITE_API_KEY or not KITE_ACCESS_TOKEN:
        raise RuntimeError("KITE_API_KEY/KITE_ACCESS_TOKEN not set on Render (daily token not configured)")

    import datetime as _dt

    instruments = _kite_nfo_instruments()
    opts = [row for row in instruments if row.get("name") == kite_name and row.get("instrument_type") in ("CE", "PE")]
    if not opts:
        raise ValueError(f"kite: no {kite_name} option instruments in NFO dump")

    today = _dt.date.today().isoformat()
    expiries = sorted(set(row["expiry"] for row in opts))
    future = [e for e in expiries if e >= today]
    expiry = future[0] if future else expiries[-1]
    chain = [row for row in opts if row["expiry"] == expiry]

    symbols = [f"NFO:{row['tradingsymbol']}" for row in chain]
    quotes = {}
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]
        r = requests.get("https://api.kite.trade/quote", headers=_kite_headers(),
                          params=[("i", s) for s in chunk], timeout=20)
        r.raise_for_status()
        quotes.update(r.json().get("data", {}))

    ce_oi, pe_oi = {}, {}
    for row in chain:
        q = quotes.get(f"NFO:{row['tradingsymbol']}")
        if not q:
            continue
        strike = float(row["strike"])
        oi = q.get("oi", 0) or 0
        if row["instrument_type"] == "CE":
            ce_oi[strike] = oi
        else:
            pe_oi[strike] = oi

    total_ce = sum(ce_oi.values())
    if total_ce == 0:
        raise ValueError("kite: zero CE open interest returned (stale token or empty chain)")
    pcr = round(sum(pe_oi.values()) / total_ce, 2)

    spot_r = requests.get("https://api.kite.trade/quote", headers=_kite_headers(),
                           params=[("i", kite_spot)], timeout=20)
    spot_r.raise_for_status()
    spot = spot_r.json()["data"][kite_spot]["last_price"]

    top_ce = max(ce_oi, key=ce_oi.get)
    top_pe = max(pe_oi, key=pe_oi.get) if pe_oi else 0
    return {
        "spot": round(float(spot), 2), "pcr": pcr, "max_pain": _max_pain_dict(ce_oi, pe_oi),
        "bias": get_bias(pcr), "top_ce_strike": int(top_ce), "top_pe_strike": int(top_pe),
        "source": "kite",
    }


# ---------------------------------------------------------------------------
# Source 2: nsepython (direct NSE scrape, no daily token, was already written
# in scrape_oi.py but never wired into this app - the code below mirrors that
# logic against app.py's own return shape).
# ---------------------------------------------------------------------------

def fetch_index_nsepython(nse_name):
    from nsepython import nse_optionchain_scrapper

    data = nse_optionchain_scrapper(nse_name)
    records = data["records"]["data"]
    spot = float(data["records"]["underlyingValue"])
    expiry = data["records"]["expiryDates"][0]

    ce_oi, pe_oi = {}, {}
    for rec in records:
        if rec.get("expiryDate") != expiry:
            continue
        strike = rec["strikePrice"]
        if "CE" in rec:
            ce_oi[strike] = rec["CE"].get("openInterest", 0)
        if "PE" in rec:
            pe_oi[strike] = rec["PE"].get("openInterest", 0)

    total_ce = sum(ce_oi.values())
    if total_ce == 0:
        raise ValueError("nsepython: zero CE open interest in scraped chain")
    pcr = round(sum(pe_oi.values()) / total_ce, 2)
    top_ce = max(ce_oi, key=ce_oi.get)
    top_pe = max(pe_oi, key=pe_oi.get) if pe_oi else 0
    return {
        "spot": round(spot, 2), "pcr": pcr, "max_pain": _max_pain_dict(ce_oi, pe_oi),
        "bias": get_bias(pcr), "top_ce_strike": int(top_ce), "top_pe_strike": int(top_pe),
        "source": "nsepython",
    }


# ---------------------------------------------------------------------------
# Source 3: yfinance (last resort - uses curl_cffi browser impersonation if
# available, since Yahoo has been blocking Render's plain requests session).
# ---------------------------------------------------------------------------

def fetch_index_yfinance(yf_symbol):
    import yfinance as yf

    session = None
    try:
        from curl_cffi import requests as cffi_requests
        session = cffi_requests.Session(impersonate="chrome")
    except Exception:
        session = None

    t = yf.Ticker(yf_symbol, session=session) if session else yf.Ticker(yf_symbol)
    hist = t.history(period="1d")
    if hist.empty:
        raise ValueError("yfinance: empty price history (blocked or market closed)")
    spot = round(float(hist["Close"].iloc[-1]), 2)

    exps = t.options
    if not exps:
        raise ValueError("yfinance: no option expiries returned")
    ch = t.option_chain(exps[0])
    ce_oi = ch.calls.set_index("strike")["openInterest"].to_dict()
    pe_oi = ch.puts.set_index("strike")["openInterest"].to_dict()

    total_ce = sum(ce_oi.values())
    if total_ce == 0:
        raise ValueError("yfinance: zero CE open interest")
    pcr = round(sum(pe_oi.values()) / total_ce, 2)
    top_ce = max(ce_oi, key=ce_oi.get)
    top_pe = max(pe_oi, key=pe_oi.get) if pe_oi else 0
    return {
        "spot": spot, "pcr": pcr, "max_pain": _max_pain_dict(ce_oi, pe_oi),
        "bias": get_bias(pcr), "top_ce_strike": int(top_ce), "top_pe_strike": int(top_pe),
        "source": "yfinance",
    }


# ---------------------------------------------------------------------------
# Orchestration: try each source in order, return (result, None) on first
# success or (None, "<real errors from every source>") if all fail.
# ---------------------------------------------------------------------------

def fetch_index(display_name):
    cfg = SOURCES[display_name]
    errors = []
    for source_name, fn in (
        ("kite", lambda: fetch_index_kite(cfg["kite_name"], cfg["kite_spot"])),
        ("nsepython", lambda: fetch_index_nsepython(cfg["nse"])),
        ("yfinance", lambda: fetch_index_yfinance(cfg["yf"])),
    ):
        try:
            return fn(), None
        except Exception as e:
            errors.append(f"{source_name}: {e}")
    err = " || ".join(errors)
    app.logger.error(f"{display_name}: all sources failed - {err}")
    return None, err


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/data")
def api_data():
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    nifty, nifty_err = fetch_index("NIFTY 50")
    bnifty, bnifty_err = fetch_index("BANK NIFTY")

    if not nifty and not bnifty:
        err = f"NIFTY: {nifty_err} | BANKNIFTY: {bnifty_err}"
        return jsonify({"success": False, "error": err, "timestamp": ts})

    idx = {}
    if nifty:
        idx["NIFTY"] = nifty
    if bnifty:
        idx["BANKNIFTY"] = bnifty

    bs = [v["bias"] for v in idx.values()]
    if all(b == "BULLISH" for b in bs):
        sig = "BULLISH"
    elif all(b == "BEARISH" for b in bs):
        sig = "BEARISH"
    elif "BULLISH" in bs:
        sig = "BULLISH_PARTIAL"
    elif "BEARISH" in bs:
        sig = "BEARISH_PARTIAL"
    else:
        sig = "NEUTRAL"

    return jsonify({"success": True, "indices": idx, "combined_signal": sig, "timestamp": ts})


@app.route("/api/signal")
def api_signal():
    d = api_data().get_json()
    if not d.get("success"):
        return jsonify({"signal": "SKIP", "reason": d.get("error", "unavailable")})
    nifty = d["indices"].get("NIFTY", {})
    return jsonify({
        "signal": d["combined_signal"], "pcr": nifty.get("pcr"),
        "max_pain": nifty.get("max_pain"), "spot": nifty.get("spot"),
        "timestamp": d["timestamp"], "source": nifty.get("source"),
    })


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=10000)
