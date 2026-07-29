"""Runs rally/recovery detection across the full stock universe and persists ranked results.

Reuses the breakout project's price DB, market-regime computation and per-industry
1-month strength. Industry strength is converted to percentile ranks so HOT_SECTOR
means "top quartile of sectors this month".
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from data_fetch import DB_PATH, load_all_symbols, load_prices
from rally import analyze_stock
from scan import compute_regime, compute_sector_strength
from universe import BENCHMARK_TICKER, get_industry_map

RESULTS_PATH = Path(__file__).parent / "data" / "latest_rally_scan.json"


def _sector_ranks(sector_strength: dict[str, float]) -> dict[str, float]:
    """Industry -> percentile rank (0-1) of its 1-month median return."""
    if not sector_strength:
        return {}
    s = pd.Series(sector_strength)
    return s.rank(pct=True).to_dict()


def run_rally_scan() -> list[dict]:
    symbols = [s for s in load_all_symbols() if s != BENCHMARK_TICKER]

    nifty_df = load_prices(BENCHMARK_TICKER)
    nifty_close = nifty_df["close"] if not nifty_df.empty else None
    regime = compute_regime(nifty_close) if nifty_close is not None else {"regime": "UNKNOWN"}

    industry_map = get_industry_map()
    sector_strength = compute_sector_strength(industry_map)
    ranks = _sector_ranks(sector_strength)

    results = []
    for sym in symbols:
        df = load_prices(sym)
        plain = sym.replace(".NS", "")
        industry = industry_map.get(plain)
        signal = analyze_stock(
            df,
            benchmark_close=nifty_close,
            sector_rank=ranks.get(industry) if industry else None,
        )
        if signal:
            signal["symbol"] = plain
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
                "universe_size": n_scanned - 1,
                "regime": regime,
                "sector_strength": sector_strength,
                "signals": results,
            },
            f,
            indent=2,
        )
    return results


def load_latest_rally_scan() -> dict:
    if not RESULTS_PATH.exists():
        return {"as_of": None, "signals": [], "regime": {"regime": "UNKNOWN"}}
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    results = run_rally_scan()
    scan = load_latest_rally_scan()
    print(f"Market regime: {scan['regime']['regime']}")
    print(f"Scanned {scan['universe_size']} stocks, {len(results)} flagged")
    for r in results[:25]:
        print(
            f"{r['symbol']:15s} {r['category']:9s} score={r['score']:.3f} "
            f"close={r['close']:>9.2f} tgt={r['target_low']:.0f}-{r['target_high']:.0f} "
            f"({r['upside_low_pct']:+.1f}/{r['upside_high_pct']:+.1f}%) {r['timeframe']:8s} "
            f"sig={','.join(r['signals'])}"
        )
