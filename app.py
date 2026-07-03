"""NSE OI Scanner - yfinance only (NSE API blocked on cloud)"""
import datetime
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
      h+='<div class="card"><h2>'+n+'</h2>'
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

def get_bias(pcr):
    if pcr > 1.3: return "BULLISH"
    if pcr < 0.7: return "BEARISH"
    return "NEUTRAL"

def max_pain(calls, puts):
    try:
        strikes = sorted(set(calls.index) | set(puts.index))
        best, bs = float('inf'), strikes[0]
        for s in strikes:
            p = sum(calls.loc[k,'openInterest']*(s-k) for k in strikes if k<s and k in calls.index)
            p += sum(puts.loc[k,'openInterest']*(k-s) for k in strikes if k>s and k in puts.index)
            if p < best: best, bs = p, s
        return int(bs)
    except: return 0

def fetch_index(symbol):
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        hist = t.history(period="1d")
        if hist.empty: return None
        spot = round(float(hist['Close'].iloc[-1]), 2)
        exps = t.options
        if not exps: return None
        ch = t.option_chain(exps[0])
        calls = ch.calls.set_index('strike')
        puts  = ch.puts.set_index('strike')
        ce_oi = calls['openInterest'].sum()
        pe_oi = puts['openInterest'].sum()
        pcr   = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 1.0
        top_ce = calls['openInterest'].idxmax() if not calls.empty else 0
        top_pe = puts['openInterest'].idxmax() if not puts.empty else 0
        return {"spot":spot,"pcr":pcr,"max_pain":max_pain(calls,puts),
                "bias":get_bias(pcr),"top_ce_strike":int(top_ce),"top_pe_strike":int(top_pe)}
    except Exception as e:
        app.logger.error(f"fetch_index {symbol}: {e}")
        return None

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/data')
def api_data():
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    nifty  = fetch_index("^NSEI")
    bnifty = fetch_index("^NSEBANK")
    if not nifty and not bnifty:
        return jsonify({"success":False,"error":"No data from yfinance (market closed or rate-limited)","timestamp":ts})
    idx = {}
    if nifty:  idx["NIFTY"]     = nifty
    if bnifty: idx["BANKNIFTY"] = bnifty
    bs = [v["bias"] for v in idx.values()]
    if   all(b=="BULLISH"  for b in bs): sig="BULLISH"
    elif all(b=="BEARISH"  for b in bs): sig="BEARISH"
    elif "BULLISH" in bs:                sig="BULLISH_PARTIAL"
    elif "BEARISH" in bs:                sig="BEARISH_PARTIAL"
    else:                                sig="NEUTRAL"
    return jsonify({"success":True,"indices":idx,"combined_signal":sig,"timestamp":ts})

@app.route('/api/signal')
def api_signal():
    d = api_data().get_json()
    if not d.get("success"):
        return jsonify({"signal":"SKIP","reason":d.get("error","unavailable")})
    nifty = d["indices"].get("NIFTY",{})
    return jsonify({"signal":d["combined_signal"],"pcr":nifty.get("pcr"),
                    "max_pain":nifty.get("max_pain"),"spot":nifty.get("spot"),
                    "timestamp":d["timestamp"]})

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=10000)
