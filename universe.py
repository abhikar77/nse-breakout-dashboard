"""Fetches and caches the Nifty 500 symbol list from NSE."""
import csv
import io
import time
from pathlib import Path

import requests

NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
CACHE_PATH = Path(__file__).parent / "data" / "nifty500.csv"
EQUITY_CACHE_PATH = Path(__file__).parent / "data" / "all_equities.csv"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600  # weekly refresh is plenty; index reconstitution is infrequent

BENCHMARK_TICKER = "^NSEI"  # Nifty 50 index, used for market regime + relative strength

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _fetch_csv(url: str) -> list[dict]:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    # EQUITY_L.csv has leading spaces in header names; normalize everywhere
    return [{k.strip(): v.strip() for k, v in row.items()} for row in reader]


def _fetch_live() -> list[dict]:
    return _fetch_csv(NIFTY500_URL)


def get_nifty500(force_refresh: bool = False) -> list[dict]:
    """Returns list of dicts with keys: Company Name, Industry, Symbol, Series, ISIN Code."""
    CACHE_PATH.parent.mkdir(exist_ok=True)

    if not force_refresh and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_MAX_AGE_SECONDS:
            with open(CACHE_PATH, newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))

    try:
        rows = _fetch_live()
        with open(CACHE_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return rows
    except Exception:
        if CACHE_PATH.exists():
            with open(CACHE_PATH, newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        raise


def get_all_equities(force_refresh: bool = False) -> list[dict]:
    """Returns all NSE EQ-series stocks (excludes BE/BZ trade-to-trade & surveillance series)."""
    EQUITY_CACHE_PATH.parent.mkdir(exist_ok=True)

    rows = None
    if not force_refresh and EQUITY_CACHE_PATH.exists():
        age = time.time() - EQUITY_CACHE_PATH.stat().st_mtime
        if age < CACHE_MAX_AGE_SECONDS:
            with open(EQUITY_CACHE_PATH, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

    if rows is None:
        try:
            rows = _fetch_csv(EQUITY_LIST_URL)
            with open(EQUITY_CACHE_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        except Exception:
            if EQUITY_CACHE_PATH.exists():
                with open(EQUITY_CACHE_PATH, newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
            else:
                raise

    return [r for r in rows if r["SERIES"] == "EQ"]


def get_yf_tickers(force_refresh: bool = False, scope: str = "all") -> list[str]:
    """Returns NSE symbols suffixed for yfinance, e.g. 'RELIANCE.NS'.

    scope='all' -> full EQ-series list (~2000); scope='nifty500' -> Nifty 500 only.
    """
    if scope == "nifty500":
        return [f"{row['Symbol']}.NS" for row in get_nifty500(force_refresh)]
    return [f"{row['SYMBOL']}.NS" for row in get_all_equities(force_refresh)]


def get_industry_map() -> dict[str, str]:
    """Symbol (no suffix) -> industry, for stocks where NSE publishes it (Nifty 500)."""
    return {row["Symbol"]: row["Industry"] for row in get_nifty500()}


if __name__ == "__main__":
    tickers = get_yf_tickers(force_refresh=True)
    print(f"Fetched {len(tickers)} tickers (full EQ list)")
    print(tickers[:10])
