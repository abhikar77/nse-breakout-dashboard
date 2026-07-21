"""Downloads daily OHLCV history for the stock universe and stores it in SQLite."""
import sqlite3
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

DB_PATH = Path(__file__).parent / "data" / "prices.db"
CHUNK_SIZE = 60
PERIOD = "18mo"  # enough history for 52-week resistance + lookback context


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    return conn


def _chunk(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def fetch_universe(tickers: list[str], progress_cb=None) -> dict:
    """Downloads OHLCV for all tickers and upserts into SQLite.

    Returns {'ok': [...], 'failed': [...]}
    """
    conn = _connect()
    ok, failed = [], []
    chunks = list(_chunk(tickers, CHUNK_SIZE))

    for ci, chunk in enumerate(chunks):
        try:
            raw = yf.download(
                chunk,
                period=PERIOD,
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception:
            failed.extend(chunk)
            continue

        for sym in chunk:
            try:
                if len(chunk) == 1:
                    df = raw
                else:
                    df = raw[sym]
                df = df.dropna(how="all")
                if df.empty:
                    failed.append(sym)
                    continue
                df = df.reset_index()
                df["symbol"] = sym
                df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
                rows = df[["symbol", "date", "Open", "High", "Low", "Close", "Volume"]].values.tolist()
                conn.executemany(
                    """INSERT OR REPLACE INTO prices
                       (symbol, date, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                ok.append(sym)
            except Exception:
                failed.append(sym)

        conn.commit()
        if progress_cb:
            progress_cb(min((ci + 1) * CHUNK_SIZE, len(tickers)), len(tickers))
        time.sleep(0.5)  # be polite between chunks

    conn.close()
    return {"ok": ok, "failed": failed}


def load_prices(symbol: str) -> pd.DataFrame:
    conn = _connect()
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM prices WHERE symbol = ? ORDER BY date",
        conn,
        params=(symbol,),
        parse_dates=["date"],
    )
    conn.close()
    df.set_index("date", inplace=True)
    return df


def load_all_symbols() -> list[str]:
    conn = _connect()
    rows = conn.execute("SELECT DISTINCT symbol FROM prices").fetchall()
    conn.close()
    return [r[0] for r in rows]


def last_updated() -> str | None:
    conn = _connect()
    row = conn.execute("SELECT MAX(date) FROM prices").fetchone()
    conn.close()
    return row[0] if row else None


if __name__ == "__main__":
    from universe import BENCHMARK_TICKER, get_yf_tickers

    tickers = get_yf_tickers(scope="all") + [BENCHMARK_TICKER]
    print(f"Fetching {len(tickers)} tickers...")
    result = fetch_universe(tickers, progress_cb=lambda done, total: print(f"{done}/{total}"))
    print(f"OK: {len(result['ok'])}, Failed: {len(result['failed'])}")
    if result["failed"]:
        print("Failed symbols:", result["failed"])
