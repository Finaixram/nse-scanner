"""
nse_scanner/scrape_oi.py
========================
Pulls live NSE option chain data for Nifty 50 and Bank Nifty.
Calculates: PCR, Max Pain, Top OI strikes, Bias signal.

Sources:
  1. NSE official API (primary) — no auth needed, JSON response
  2. Fallback: cached sample data for offline testing

Usage:
  from scrape_oi import get_nifty_oi, get_banknifty_oi, get_combined_signal
"""

import urllib.request
import urllib.error
import json
import datetime

# ── NSE API endpoints ─────────────────────────────────────────────────────────
NSE_BASE = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
}

def _nse_session_get(url: str) -> dict | None:
    """
    NSE requires a cookie from the homepage first.
    We do a two-step request: homepage → API.
    """
    try:
        # Step 1: Get cookies from homepage
        req1 = urllib.request.Request(NSE_BASE, headers=NSE_HEADERS)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        opener.open(req1, timeout=10)

        # Step 2: Hit the API
        req2 = urllib.request.Request(url, headers=NSE_HEADERS)
        resp = opener.open(req2, timeout=10)
        raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        print(f"  ⚠  NSE fetch error: {e}")
        return None


def _parse_option_chain(data: dict, symbol: str) -> dict:
    """
    Parse raw NSE option chain JSON into usable signal data.
    Returns: {
        symbol, spot_price, pcr, max_pain,
        top_ce_oi: [{strike, oi}],
        top_pe_oi: [{strike, oi}],
        bias, timestamp
    }
    """
    try:
        records = data["records"]["data"]
        spot = float(data["records"]["underlyingValue"])
        expiry = data["records"]["expiryDates"][0]  # nearest expiry

        ce_oi = {}  # strike → OI
        pe_oi = {}
        total_ce_oi = 0
        total_pe_oi = 0

        for rec in records:
            if rec.get("expiryDate") != expiry:
                continue
            strike = rec["strikePrice"]

            if "CE" in rec:
                oi = rec["CE"].get("openInterest", 0)
                ce_oi[strike] = oi
                total_ce_oi += oi

            if "PE" in rec:
                oi = rec["PE"].get("openInterest", 0)
                pe_oi[strike] = oi
                total_pe_oi += oi

        # PCR
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        # Max Pain — strike where combined loss of all option holders is maximum
        max_pain_strike = _calc_max_pain(ce_oi, pe_oi)

        # Top 5 strikes by OI
        top_ce = sorted(ce_oi.items(), key=lambda x: x[1], reverse=True)[:5]
        top_pe = sorted(pe_oi.items(), key=lambda x: x[1], reverse=True)[:5]

        # Bias
        if pcr > 1.3:
            bias = "📈 BULLISH"
            bias_reason = f"PCR {pcr} (high PE OI — shorts under pressure)"
        elif pcr < 0.7:
            bias = "📉 BEARISH"
            bias_reason = f"PCR {pcr} (high CE OI — calls being sold)"
        else:
            bias = "⚖️ NEUTRAL"
            bias_reason = f"PCR {pcr} (balanced OI)"

        return {
            "symbol": symbol,
            "spot_price": spot,
            "expiry": expiry,
            "pcr": pcr,
            "max_pain": max_pain_strike,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "top_ce_oi": [{"strike": s, "oi": o} for s, o in top_ce],
            "top_pe_oi": [{"strike": s, "oi": o} for s, o in top_pe],
            "bias": bias,
            "bias_reason": bias_reason,
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S IST"),
            "success": True
        }
    except Exception as e:
        return {"symbol": symbol, "success": False, "error": str(e)}


def _calc_max_pain(ce_oi: dict, pe_oi: dict) -> float:
    """Strike price at which total option holder loss is maximized (max pain)."""
    all_strikes = sorted(set(list(ce_oi.keys()) + list(pe_oi.keys())))
    if not all_strikes:
        return 0

    min_loss = float("inf")
    max_pain_strike = all_strikes[0]

    for test_strike in all_strikes:
        # Loss to CE holders at this expiry price
        ce_loss = sum(max(0, test_strike - s) * oi for s, oi in ce_oi.items())
        # Loss to PE holders
        pe_loss = sum(max(0, s - test_strike) * oi for s, oi in pe_oi.items())
        total_loss = ce_loss + pe_loss
        if total_loss < min_loss:
            min_loss = total_loss
            max_pain_strike = test_strike

    return max_pain_strike


def get_nifty_oi() -> dict:
    """Fetch live Nifty 50 option chain data."""
    url = f"{NSE_BASE}/api/option-chain-indices?symbol=NIFTY"
    data = _nse_session_get(url)
    if data:
        return _parse_option_chain(data, "NIFTY 50")
    return _fallback_data("NIFTY 50")


def get_banknifty_oi() -> dict:
    """Fetch live Bank Nifty option chain data."""
    url = f"{NSE_BASE}/api/option-chain-indices?symbol=BANKNIFTY"
    data = _nse_session_get(url)
    if data:
        return _parse_option_chain(data, "BANK NIFTY")
    return _fallback_data("BANK NIFTY")


def _fallback_data(symbol: str) -> dict:
    """Return sample data for offline testing / API failures."""
    is_nifty = "BANK" not in symbol
    spot = 24500 if is_nifty else 52500
    return {
        "symbol": symbol,
        "spot_price": spot,
        "expiry": "Demo Data",
        "pcr": 1.05,
        "max_pain": spot - 100,
        "total_ce_oi": 5000000,
        "total_pe_oi": 5250000,
        "top_ce_oi": [
            {"strike": spot + 500, "oi": 1200000},
            {"strike": spot + 1000, "oi": 980000},
            {"strike": spot, "oi": 750000},
        ],
        "top_pe_oi": [
            {"strike": spot - 500, "oi": 1100000},
            {"strike": spot - 1000, "oi": 900000},
            {"strike": spot, "oi": 800000},
        ],
        "bias": "⚖️ NEUTRAL",
        "bias_reason": "PCR 1.05 (demo data — NSE API unavailable)",
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S IST"),
        "success": True,
        "is_demo": True
    }


def get_combined_signal() -> dict:
    """Get both Nifty and BankNifty data + combined signal."""
    nifty = get_nifty_oi()
    banknifty = get_banknifty_oi()

    # Overall signal: both bullish → strong bull, one of each → neutral
    n_bias = nifty.get("bias", "")
    b_bias = banknifty.get("bias", "")
    both_bull = "BULLISH" in n_bias and "BULLISH" in b_bias
    both_bear = "BEARISH" in n_bias and "BEARISH" in b_bias

    if both_bull:
        signal = "🟢 STRONG BULLISH — Both indices show positive OI structure"
    elif both_bear:
        signal = "🔴 STRONG BEARISH — Both indices under selling pressure"
    else:
        signal = "🟡 MIXED — Watch price action for confirmation"

    return {
        "nifty": nifty,
        "banknifty": banknifty,
        "combined_signal": signal,
        "generated_at": datetime.datetime.now().strftime("%d %b %Y %H:%M IST")
    }


if __name__ == "__main__":
    print("Fetching NSE OI data...")
    result = get_combined_signal()
    print(f"\n{result['combined_signal']}")
    print(f"\nNifty PCR: {result['nifty']['pcr']}")
    print(f"BankNifty PCR: {result['banknifty']['pcr']}")
