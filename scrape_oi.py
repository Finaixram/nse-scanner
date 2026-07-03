"""
nse_scanner/scrape_oi.py
========================
Sources (tried in order):
  1. nsepython  - handles NSE cookie/session auth automatically
  2. yfinance   - Yahoo Finance option chain (no NSE auth needed)
  3. Explicit error dict if both fail - no silent fake/demo data
"""

import datetime

try:
    from nsepython import nse_optionchain_scrapper as _nsp
    _NSP_OK = True
except ImportError:
    _NSP_OK = False
    print("WARNING: nsepython not installed")

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False
    print("WARNING: yfinance not installed")


def _compute_bias(pcr):
    if pcr > 1.3:
        return "\U0001f4c8 BULLISH", f"PCR {pcr} (high PE OI - shorts under pressure)"
    elif pcr < 0.7:
        return "\U0001f4c9 BEARISH", f"PCR {pcr} (high CE OI - calls being sold)"
    return "\u2696\ufe0f NEUTRAL", f"PCR {pcr} (balanced OI)"


def _calc_max_pain(ce_oi, pe_oi):
    all_strikes = sorted(set(list(ce_oi.keys()) + list(pe_oi.keys())))
    if not all_strikes:
        return 0
    min_loss = float("inf")
    result = all_strikes[0]
    for s in all_strikes:
        ce_loss = sum(max(0, s - k) * v for k, v in ce_oi.items())
        pe_loss = sum(max(0, k - s) * v for k, v in pe_oi.items())
        loss = ce_loss + pe_loss
        if loss < min_loss:
            min_loss = loss
            result = s
    return result


def _error_result(symbol, msg):
    return {
        "symbol": symbol, "success": False, "error": msg,
        "spot_price": 0, "pcr": 0,
        "bias": "\u274c DATA UNAVAILABLE", "bias_reason": msg,
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S IST"),
    }


def _parse_nse_chain(data, symbol):
    try:
        records = data["records"]["data"]
        spot    = float(data["records"]["underlyingValue"])
        expiry  = data["records"]["expiryDates"][0]
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
        total_pe = sum(pe_oi.values())
        pcr = round(total_pe / total_ce, 2) if total_ce > 0 else 0
        bias, bias_reason = _compute_bias(pcr)
        top_ce = sorted(ce_oi.items(), key=lambda x: x[1], reverse=True)[:5]
        top_pe = sorted(pe_oi.items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "symbol": symbol, "spot_price": spot, "expiry": expiry,
            "pcr": pcr, "max_pain": _calc_max_pain(ce_oi, pe_oi),
            "total_ce_oi": int(total_ce), "total_pe_oi": int(total_pe),
            "top_ce_oi": [{"strike": s, "oi": o} for s, o in top_ce],
            "top_pe_oi": [{"strike": s, "oi": o} for s, o in top_pe],
            "bias": bias, "bias_reason": bias_reason,
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S IST"),
            "success": True, "source": "nsepython",
        }
    except Exception as e:
        return {"symbol": symbol, "success": False, "error": str(e)}


def _parse_yfinance(ticker_sym, symbol):
    try:
        t = yf.Ticker(ticker_sym)
        expiries = t.options
        if not expiries:
            raise ValueError("No expiry dates from yfinance")
        expiry = expiries[0]
        info = t.fast_info
        spot = float(info.get("last_price") or info.get("regularMarketPrice") or 0)
        if spot == 0:
            hist = t.history(period="1d")
            spot = float(hist["Close"].iloc[-1]) if not hist.empty else 0
        chain = t.option_chain(expiry)
        calls, puts = chain.calls, chain.puts
        ce_oi = dict(zip(calls["strike"].tolist(), calls["openInterest"].tolist()))
        pe_oi = dict(zip(puts["strike"].tolist(),  puts["openInterest"].tolist()))
        total_ce = sum(ce_oi.values())
        total_pe = sum(pe_oi.values())
        pcr = round(total_pe / total_ce, 2) if total_ce > 0 else 0
        bias, bias_reason = _compute_bias(pcr)
        top_ce = sorted(ce_oi.items(), key=lambda x: x[1], reverse=True)[:5]
        top_pe = sorted(pe_oi.items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "symbol": symbol, "spot_price": spot, "expiry": expiry,
            "pcr": pcr, "max_pain": _calc_max_pain(ce_oi, pe_oi),
            "total_ce_oi": int(total_ce), "total_pe_oi": int(total_pe),
            "top_ce_oi": [{"strike": s, "oi": o} for s, o in top_ce],
            "top_pe_oi": [{"strike": s, "oi": o} for s, o in top_pe],
            "bias": bias, "bias_reason": bias_reason,
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S IST"),
            "success": True, "source": "yfinance",
        }
    except Exception as e:
        return {"symbol": symbol, "success": False, "error": f"yfinance: {e}"}


def get_nifty_oi():
    if _NSP_OK:
        try:
            r = _parse_nse_chain(_nsp("NIFTY"), "NIFTY 50")
            if r.get("success"): return r
        except Exception as e:
            print(f"nsepython NIFTY error: {e}")
    if _YF_OK:
        r = _parse_yfinance("^NSEI", "NIFTY 50")
        if r.get("success"): return r
    return _error_result("NIFTY 50", "Both nsepython and yfinance failed. Check Render logs.")


def get_banknifty_oi():
    if _NSP_OK:
        try:
            r = _parse_nse_chain(_nsp("BANKNIFTY"), "BANK NIFTY")
            if r.get("success"): return r
        except Exception as e:
            print(f"nsepython BANKNIFTY error: {e}")
    if _YF_OK:
        r = _parse_yfinance("^NSEBANK", "BANK NIFTY")
        if r.get("success"): return r
    return _error_result("BANK NIFTY", "Both nsepython and yfinance failed. Check Render logs.")


def get_combined_signal():
    nifty    = get_nifty_oi()
    banknifty = get_banknifty_oi()
    n_bias = nifty.get("bias", "")
    b_bias = banknifty.get("bias", "")
    if "BULLISH" in n_bias and "BULLISH" in b_bias:
        signal = "\U0001f7e2 STRONG BULLISH - Both indices show positive OI structure"
    elif "BEARISH" in n_bias and "BEARISH" in b_bias:
        signal = "\U0001f534 STRONG BEARISH - Both indices under selling pressure"
    elif not nifty.get("success") and not banknifty.get("success"):
        signal = "\u274c DATA UNAVAILABLE - All data sources failed. Check Render logs."
    else:
        signal = "\U0001f7e1 MIXED - Watch price action for confirmation"
    return {
        "nifty": nifty, "banknifty": banknifty,
        "combined_signal": signal,
        "generated_at": datetime.datetime.now().strftime("%d %b %Y %H:%M IST"),
    }


if __name__ == "__main__":
    result = get_combined_signal()
    print(result["combined_signal"])
    print(f"Nifty:     PCR={result['nifty'].get('pcr')}  source={result['nifty'].get('source','error')}")
    print(f"BankNifty: PCR={result['banknifty'].get('pcr')}  source={result['banknifty'].get('source','error')}")
