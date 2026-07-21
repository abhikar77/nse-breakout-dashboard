"""Resistance-breakout detection and scoring for a single stock's daily OHLCV history.

Encodes the screening checklist:
- Close above a resistance zone with >=2 prior touches spread over time
- Confirmation-candle status: CREAKING -> BREAKOUT (first close above) -> CONFIRMED (held next day)
- Strong close (upper 1/3 of day range), 52-week-high flag, 1-day spike filter
- Volume >= threshold vs 20d avg, rising 3-day volume trend, absolute turnover floor
- Price above rising 20/50 DMA, RSI band with overbought flag, MACD cross
- Relative strength vs benchmark (Nifty), ATR-based stop suggestion
"""
import numpy as np
import pandas as pd

# --- Resistance zone geometry ---
SWING_WINDOW = 5           # bars each side to confirm a local high
ZONE_TOLERANCE_PCT = 1.5   # swing highs within this % cluster into one zone
LOOKBACK_DAYS = 240        # ~1 trading year to search for resistance zones
RECENT_EXCLUDE_DAYS = 3    # don't let the current move's own highs form its ceiling
MIN_TOUCHES = 2            # a single old high isn't resistance
MIN_ZONE_SPAN_DAYS = 5     # touches must be spread out in time, not one swing

# --- Relevance / freshness ---
APPROACH_BAND_BELOW_PCT = 3.0   # CREAKING: close within this % below the zone
APPROACH_BAND_ABOVE_PCT = 6.0   # still relevant this % above (recent breakouts)
MAX_DAYS_ABOVE = 5              # broke out longer ago than this -> stale, skip

# --- Hard filters ---
MIN_HISTORY_BARS = 220     # need ~1yr for 52w context and SMA200
MIN_AVG_TURNOVER = 2e7     # >=Rs 2 crore avg daily traded value (20d) - liquidity floor
MIN_VOLUME_RATIO = 1.5     # breakout-day volume vs 20d avg, hard floor
MAX_DAY_MOVE_PCT = 20.0    # exclude 1-day moves beyond this (news spike, high reversal risk)
MIN_SCORE = 0.55

# --- Flag thresholds (kept, but flagged as risks) ---
SPIKE_FLAG_PCT = 12.0      # 1-day move above this -> SPIKE_RISK flag + penalty
EXTENDED_PCT = 8.0         # close this far above zone -> EXTENDED flag + penalty
RSI_OVERBOUGHT = 75.0

VOL_LOOKBACK = 20
RSI_PERIOD = 14
ATR_PERIOD = 14


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["avg_vol20"] = df["volume"].rolling(VOL_LOOKBACK).mean()
    df["turnover20"] = (df["close"] * df["volume"]).rolling(VOL_LOOKBACK).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = (100 - (100 / (1 + rs))).fillna(50)

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = tr.rolling(ATR_PERIOD).mean()

    return df


def find_swing_highs(df: pd.DataFrame, window: int = SWING_WINDOW) -> pd.Series:
    """Returns boolean Series: True where `high` is a local max within +/- window bars."""
    highs = df["high"].to_numpy()
    n = len(highs)
    flags = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        seg = highs[i - window : i + window + 1]
        if highs[i] == seg.max():
            flags[i] = True
    return pd.Series(flags, index=df.index)


def cluster_resistance_zones(df: pd.DataFrame, tolerance_pct: float = ZONE_TOLERANCE_PCT) -> list[dict]:
    """Groups nearby swing highs (excluding the most recent bars) into resistance zones.

    Only zones with MIN_TOUCHES+ touches spread over MIN_ZONE_SPAN_DAYS+ qualify.
    """
    usable = df.iloc[: max(len(df) - RECENT_EXCLUDE_DAYS, 0)].tail(LOOKBACK_DAYS)
    if len(usable) < SWING_WINDOW * 2 + 1:
        return []

    is_peak = find_swing_highs(usable)
    peaks = usable.loc[is_peak, ["high"]].copy()
    if peaks.empty:
        return []

    peaks = peaks.sort_values("high")
    zones = []
    current = {"levels": [peaks.iloc[0]["high"]], "dates": [peaks.index[0]]}

    for price, date in zip(peaks["high"].iloc[1:], peaks.index[1:]):
        center = np.mean(current["levels"])
        if abs(price - center) / center * 100 <= tolerance_pct:
            current["levels"].append(price)
            current["dates"].append(date)
        else:
            zones.append(current)
            current = {"levels": [price], "dates": [date]}
    zones.append(current)

    result = []
    for z in zones:
        first_date, last_date = min(z["dates"]), max(z["dates"])
        touches = len(z["levels"])
        if touches < MIN_TOUCHES or (last_date - first_date).days < MIN_ZONE_SPAN_DAYS:
            continue
        result.append(
            {
                "level": float(np.mean(z["levels"])),
                "touches": touches,
                "first_date": first_date,
                "last_date": last_date,
            }
        )
    return sorted(result, key=lambda z: z["level"])


def _relative_strength(close: pd.Series, benchmark_close: pd.Series | None, days: int) -> float | None:
    """Stock return minus benchmark return over `days` trading days, in percentage points."""
    if benchmark_close is None or len(close) <= days or len(benchmark_close) <= days:
        return None
    stock_ret = close.iloc[-1] / close.iloc[-1 - days] - 1
    bench_ret = benchmark_close.iloc[-1] / benchmark_close.iloc[-1 - days] - 1
    return round((stock_ret - bench_ret) * 100, 2)


def score_stock(df: pd.DataFrame, benchmark_close: pd.Series | None = None) -> dict | None:
    """Analyzes the most recent bar against resistance zones. Returns a signal dict or None."""
    if len(df) < MIN_HISTORY_BARS:
        return None

    df = add_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = latest["close"]

    if pd.isna(latest["sma50"]) or pd.isna(latest["avg_vol20"]) or pd.isna(latest["atr14"]):
        return None

    # --- Liquidity floor: avoid illiquid traps ---
    if pd.isna(latest["turnover20"]) or latest["turnover20"] < MIN_AVG_TURNOVER:
        return None

    # --- 1-day spike exclusion: unsustainable news-driven moves ---
    day_move_pct = (close / prev["close"] - 1) * 100
    if day_move_pct > MAX_DAY_MOVE_PCT:
        return None

    zones = cluster_resistance_zones(df)
    if not zones:
        return None

    relevant = []
    for z in zones:
        pct = (close - z["level"]) / z["level"] * 100
        if -APPROACH_BAND_BELOW_PCT <= pct <= APPROACH_BAND_ABOVE_PCT:
            relevant.append(z)
    if not relevant:
        return None

    zone = min(relevant, key=lambda z: abs(close - z["level"]))
    zone_level = zone["level"]
    pct_vs_zone = (close - zone_level) / zone_level * 100

    # --- Confirmation-candle status: count consecutive closes above the zone ---
    closes = df["close"].to_numpy()
    days_above = 0
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] > zone_level:
            days_above += 1
        else:
            break
    if days_above > MAX_DAYS_ABOVE:
        return None  # stale breakout, the move already ran
    if days_above == 0:
        status = "CREAKING"
    elif days_above == 1:
        status = "BREAKOUT"
    else:
        status = "CONFIRMED"

    # --- Volume checks ---
    vol_ratio = latest["volume"] / latest["avg_vol20"] if latest["avg_vol20"] else 0
    # For a breakout candle demand real volume; for creaking allow slightly less
    vol_floor = MIN_VOLUME_RATIO if status != "CREAKING" else 1.2
    if vol_ratio < vol_floor:
        return None
    vol_trend = df["volume"].iloc[-3:].mean() / latest["avg_vol20"]  # accumulation over last 3 days

    # --- Price action quality ---
    day_range = latest["high"] - latest["low"]
    close_position = (close - latest["low"]) / day_range if day_range > 0 else 1.0
    strong_close = close_position >= 0.66
    high_52w = df["high"].iloc[-250:].max()
    is_52w_high = close >= high_52w * 0.999

    # --- Trend & momentum ---
    above_sma20 = close > latest["sma20"]
    above_sma50 = close > latest["sma50"]
    sma20_rising = df["sma20"].iloc[-1] > df["sma20"].iloc[-6]
    sma50_rising = df["sma50"].iloc[-1] > df["sma50"].iloc[-6]
    rsi = latest["rsi14"]
    macd_bull = latest["macd"] > latest["macd_signal"] and latest["macd_hist"] > 0

    rs_1m = _relative_strength(df["close"], benchmark_close, 21)
    rs_3m = _relative_strength(df["close"], benchmark_close, 63)

    # --- Risk / trade plan ---
    atr = latest["atr14"]
    if days_above >= 1:
        stop = zone_level * 0.98  # just below the breakout level
    else:
        stop = df["low"].iloc[-10:].min() * 0.995  # below recent swing low
    stop = min(stop, close - 0.5 * atr)  # never a stop tighter than half an ATR
    risk_pct = (close - stop) / close * 100
    atr_pct = atr / close * 100

    # --- Flags (kept in the list but marked as risks) ---
    flags = []
    if is_52w_high:
        flags.append("52W_HIGH")
    if not strong_close:
        flags.append("WEAK_CLOSE")
    if rsi > RSI_OVERBOUGHT:
        flags.append("OVERBOUGHT")
    if day_move_pct > SPIKE_FLAG_PCT:
        flags.append("SPIKE_RISK")
    if pct_vs_zone > EXTENDED_PCT:
        flags.append("EXTENDED")
    if atr_pct > 6:
        flags.append("HIGH_VOLATILITY")

    # --- Composite score ---
    touches_score = min(zone["touches"] / 4, 1.0)
    volume_score = 0.7 * min(max(vol_ratio - 1, 0) / 2.0, 1.0) + 0.3 * min(max(vol_trend - 1, 0) / 1.0, 1.0)
    trend_score = (
        0.3 * above_sma20 + 0.2 * above_sma50 + 0.25 * sma20_rising + 0.25 * sma50_rising
    )
    momentum_score = (
        0.5 * (1.0 if 55 <= rsi <= 70 else max(0.0, 1 - abs(rsi - 62.5) / 25))
        + 0.5 * (1.0 if macd_bull else 0.0)
    )
    rs_score = 0.0
    if rs_1m is not None:
        rs_score += 0.6 * min(max(rs_1m, 0) / 8.0, 1.0)
    if rs_3m is not None:
        rs_score += 0.4 * min(max(rs_3m, 0) / 15.0, 1.0)
    price_action_score = 0.6 * (1.0 if strong_close else 0.0) + 0.4 * (1.0 if is_52w_high else 0.0)

    composite = (
        0.12 * touches_score
        + 0.26 * volume_score
        + 0.16 * trend_score
        + 0.16 * momentum_score
        + 0.12 * rs_score
        + 0.10 * price_action_score
        + (0.06 if status == "BREAKOUT" else 0.0)
        + (0.10 if status == "CONFIRMED" else 0.0)
    )
    if rsi > RSI_OVERBOUGHT:
        composite -= 0.10
    if day_move_pct > SPIKE_FLAG_PCT:
        composite -= 0.12
    if pct_vs_zone > EXTENDED_PCT:
        composite -= 0.10
    composite = max(0.0, min(composite, 1.0))

    if composite < MIN_SCORE:
        return None

    return {
        "close": round(float(close), 2),
        "zone_level": round(float(zone_level), 2),
        "pct_vs_zone": round(float(pct_vs_zone), 2),
        "status": status,
        "days_above": int(days_above),
        "touches": zone["touches"],
        "volume_ratio": round(float(vol_ratio), 2),
        "vol_trend": round(float(vol_trend), 2),
        "day_move_pct": round(float(day_move_pct), 2),
        "close_position": round(float(close_position), 2),
        "is_52w_high": bool(is_52w_high),
        "rsi14": round(float(rsi), 1),
        "macd_bull": bool(macd_bull),
        "above_sma20": bool(above_sma20),
        "sma20_rising": bool(sma20_rising),
        "sma50_rising": bool(sma50_rising),
        "rs_1m": rs_1m,
        "rs_3m": rs_3m,
        "atr": round(float(atr), 2),
        "atr_pct": round(float(atr_pct), 2),
        "stop": round(float(stop), 2),
        "risk_pct": round(float(risk_pct), 2),
        "turnover_cr": round(float(latest["turnover20"]) / 1e7, 2),
        "flags": flags,
        "score": round(float(composite), 3),
    }
