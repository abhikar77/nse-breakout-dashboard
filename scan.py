"""Runs breakout detection across the full stock universe and persists ranked results.

Adds market-context layers:
- Market regime from the Nifty index (breakouts in a falling market fail more often)
- Per-industry 1-month strength computed from our own price DB (sector rotation)
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from breakout import score_stock
from data_fetch import DB_PATH, load_all_symbols, load_prices
from universe import BENCHMARK_TICKER, get_industry_map

RESULTS_PATH = Path(__file__).parent / "data" / "latest_scan.json"


def compute_regime(nifty_close: pd.Series) -> dict:
    sma50 = nifty_close.rolling(50).mean()
    sma200 = nifty_close.rolling(200).mean()
    above_50 = bool(nifty_close.iloc[-1] > sma50.iloc[-1])
    sma50_rising = bool(sma50.iloc[-1] > sma50.iloc[-6])
    above_200 = bool(nifty_close.iloc[-1] > sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else True
    ret_1m = round((nifty_close.iloc[-1] / nifty_close.iloc[-22] - 1) * 100, 2)

    if above_50 and sma50_rising and above_200:
        regime = "UPTREND"
    elif not above_50 and not sma50_rising:
        regime = "DOWNTREND"
    else:
        regime = "NEUTRAL"

    return {
        "regime": regime,
        "nifty_close": round(float(nifty_close.iloc[-1]), 2),
        "nifty_ret_1m": ret_1m,
        "above_sma50": above_50,
        "sma50_rising": sma50_rising,
        "above_sma200": above_200,
    }


def compute_sector_strength(industry_map: dict[str, str]) -> dict[str, float]:
    """Median 1-month return per industry, computed from our own price DB (Nifty 500 members)."""
    conn = sqlite3.connect(DB_PATH)
    returns_by_industry: dict[str, list[float]] = {}
    for symbol, industry in industry_map.items():
        rows = conn.execute(
            "SELECT close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 23",
            (f"{symbol}.NS",),
        ).fetchall()
        if len(rows) < 22:
            continue
        ret = (rows[0][0] / rows[21][0] - 1) * 100
        returns_by_industry.setdefault(industry, []).append(ret)
    conn.close()
    return {
        ind: round(float(pd.Series(rets).median()), 2)
        for ind, rets in returns_by_industry.items()
        if len(rets) >= 3
    }


def run_scan() -> list[dict]:
    symbols = [s for s in load_all_symbols() if s != BENCHMARK_TICKER]

    nifty_df = load_prices(BENCHMARK_TICKER)
    nifty_close = nifty_df["close"] if not nifty_df.empty else None
    regime = compute_regime(nifty_close) if nifty_close is not None else {"regime": "UNKNOWN"}

    industry_map = get_industry_map()
    sector_strength = compute_sector_strength(industry_map)

    results = []
    for sym in symbols:
        df = load_prices(sym)
        signal = score_stock(df, benchmark_close=nifty_close)
        if signal:
            plain = sym.replace(".NS", "")
            signal["symbol"] = plain
            industry = industry_map.get(plain)
            signal["industry"] = industry or "—"
            signal["sector_1m"] = sector_strength.get(industry) if industry else None
            results.append(signal)

    results.sort(key=lambda r: r["score"], reverse=True)

    conn = sqlite3.connect(DB_PATH)
    last_date = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    n_scanned = conn.execute("SELECT COUNT(DISTINCT symbol) FROM prices").fetchone()[0]
    conn.close()

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "as_of": last_date,
                "universe_size": n_scanned - 1,  # minus the benchmark
                "regime": regime,
                "sector_strength": sector_strength,
                "signals": results,
            },
            f,
            indent=2,
        )

    return results


def load_latest_scan() -> dict:
    if not RESULTS_PATH.exists():
        return {"as_of": None, "signals": [], "regime": {"regime": "UNKNOWN"}}
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    results = run_scan()
    scan = load_latest_scan()
    print(f"Market regime: {scan['regime']}")
    print(f"Scanned {scan['universe_size']} stocks, {len(results)} flagged")
    for r in results[:20]:
        print(
            f"{r['symbol']:15s} {r['status']:9s} score={r['score']:.3f} "
            f"close={r['close']:>9.2f} zone={r['zone_level']:>9.2f} "
            f"vol={r['volume_ratio']:.1f}x rs1m={r['rs_1m']} flags={','.join(r['flags']) or '-'}"
        )
