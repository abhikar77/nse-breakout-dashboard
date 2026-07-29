# NSE Resistance Breakout Watchlist

Scans **all NSE EQ-series stocks (~2060)** daily and flags those closing above a
well-tested resistance ceiling with volume, trend, momentum, relative-strength and
liquidity confirmation.

## Signal model (breakout.py)
**Status ladder (confirmation candle):**
- `CREAKING` — pressing up against the zone, hasn't closed above yet
- `BREAKOUT` — first close above the zone (consider waiting a day)
- `CONFIRMED` — held above the zone on the following day(s), max 5 days (older = stale, dropped)

**Hard filters (excluded if failed):**
- Resistance zone must have ≥2 swing-high touches spread over ≥5 days
- Liquidity: ≥₹2 Cr average daily turnover (20d)
- Volume: ≥1.5x 20-day average on the breakout candle (1.2x for creaking)
- 1-day move ≤20% (news-spike exclusion)
- ≥220 bars of history

**Scored components:** touches, volume ratio + 3-day volume trend, price above rising
20/50-DMA, RSI(14) sweet spot 55–70, MACD cross, relative strength vs Nifty (1M/3M),
strong close (upper ⅓ of range), 52-week-high bonus, confirmation bonus.

**Risk flags (shown, penalized):** `OVERBOUGHT` (RSI>75), `SPIKE_RISK` (>12% day),
`EXTENDED` (>8% above zone), `WEAK_CLOSE`, `HIGH_VOLATILITY` (ATR>6%), `52W_HIGH` (informational).

**Trade plan per stock:** suggested stop (below breakout level / recent swing low,
ATR-sanity-checked), risk %, 5–7% target zone, reward:risk, 2-week time-box note.

**Market context (scan.py):** Nifty regime banner (UPTREND / NEUTRAL / DOWNTREND from
50/200-DMA state) and per-industry 1-month median return (sector rotation) from the local DB.

## Second dashboard: Rally & Recovery Scanner (app_rally.py)
A companion app over the same price DB, scanning for **momentum continuation** and
**bottom reversals** instead of resistance breakouts. Signals (rally.py):
- `GAP_HOLD` — 2–3%+ gap-up that never filled and is holding
- `VOL_SURGE` — 2x+ average volume with a 3%+ up move, price holding
- `HOT_SECTOR` — industry in the top quartile of 1-month sector returns
- `FIB_50` / `FIB_618` — bounce reclaimed 50% / 61.8% of a ≥12% peak-to-valley fall
- `VOL_ON_BOUNCE` — up-day volume on recovery ≥ ~down-day volume on the fall
- `HIGHER_LOW` — higher swing low after the valley (demand zone confirmed)
- `SMA50_RECLAIM` / `GOLDEN_CROSS` — 50-DMA reclaim (≤10 sessions) / 50>200 cross (≤15)
- `RSI_DIVERGENCE` — price lower low + RSI higher low at the valley

Needs ≥2 signals + score ≥0.35 + the same ₹2 Cr liquidity floor. Each candidate gets a
**target range + timeframe**: recovery plays target the next Fibonacci level up to the
prior peak; momentum plays project 1.6–3.2x ATR; timeframe assumes ~0.3–0.5 ATR net
progress per session (1–8 weeks). Not covered (no free bulk data): results-driven
momentum (earnings calendar) and short-covering rallies (F&O open interest).

## Files
- `universe.py` — full NSE EQ list + Nifty 500 (for industry mapping), cached weekly
- `data_fetch.py` — 18 months daily OHLCV per stock via yfinance -> `data/prices.db` (SQLite)
- `breakout.py` — zone detection + scoring (all thresholds are constants at the top)
- `scan.py` — full-universe scan, regime, sector strength -> `data/latest_scan.json`
- `app.py` — Streamlit breakout dashboard
- `rally.py` — rally/recovery signal detection + target estimation
- `rally_scan.py` — full-universe rally scan -> `data/latest_rally_scan.json`
- `app_rally.py` — Streamlit rally & recovery dashboard

## Running it
```
venv\Scripts\activate
python data_fetch.py        # refresh prices (~5 min for full universe)
python scan.py              # re-run the breakout scan
python rally_scan.py        # re-run the rally/recovery scan
streamlit run app.py        # breakout dashboard at http://localhost:8501
streamlit run app_rally.py --server.port 8502   # rally dashboard at http://localhost:8502
```
Or double-click `run.bat` / `run_rally.bat`. Each app's sidebar "Refresh data + rescan"
button does the fetch+scan in one click (they share the same price DB, so refreshing in
one refreshes prices for both).

## Not implemented (yet)
- Earnings-date / corporate-news exclusion — no reliable free bulk source for NSE
- Backtesting of the rule combinations — **do this before trusting the signals with real money**
- Real-time intraday data (yfinance is end-of-day; would need a broker API)

Educational tool — **not investment advice**.
