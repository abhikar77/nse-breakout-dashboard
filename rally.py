"""Rally / recovery signal detection for a single stock's daily OHLCV history.

Encodes the momentum & bottom-reversal checklist (EOD-data version):
- GAP_HOLD        gap-up that held (gap never filled) with follow-through
- VOL_SURGE       2x+ average volume alongside a 3%+ single-day move
- HOT_SECTOR      stock's industry is in the top quartile of 1-month sector returns
- FIB_50/FIB_618  bounce off a valley reclaimed 50% / 61.8% of the prior fall
- VOL_ON_BOUNCE   up-day volume on the recovery rivals down-day volume on the fall
- HIGHER_LOW      a higher swing low printed after the valley (demand zone confirmed)
- SMA50_RECLAIM   close crossed back above the 50-DMA within the last 10 sessions
- GOLDEN_CROSS    50-DMA crossed above the 200-DMA within the last 15 sessions
- RSI_DIVERGENCE  price made a lower low while RSI made a higher low

Not covered (no free bulk data source): results-driven momentum (earnings
calendar/estimates) and short-covering rallies (F&O open interest).

Each candidate gets an estimated target RANGE plus a timeframe:
- Recovery plays: next unreclaimed Fibonacci level up to the prior peak
- Momentum plays: ATR-multiple projection (~1.6x to ~3.2x ATR above close)
- Timeframe assumes net progress of roughly 0.3-0.5 ATR per session, clamped 1-8 weeks
"""
import math

import numpy as np
import pandas as pd

from breakout import MIN_AVG_TURNOVER, MIN_HISTORY_BARS, add_indicators, _relative_strength

# --- Recovery-leg geometry ---
RECOVERY_LOOKBACK = 180     # sessions to search for the peak -> valley -> bounce structure
MIN_DECLINE_PCT = 12.0      # peak-to-valley fall must be at least this to count as a correction
MIN_BOUNCE_PCT = 2.0        # close must be at least this % off the valley (bounce has begun)
MIN_VALLEY_AGE = 3          # valley must be at least this many bars ago
SWING_LOW_WINDOW = 3        # bars each side to confirm a local low

# --- Momentum-signal thresholds ---
GAP_MIN_PCT = 2.0           # open vs prior close, when it's a true gap (open > prior high)
GAP_MIN_PCT_LOOSE = 3.0     # plain 3%+ open-up also counts even without clearing prior high
GAP_LOOKBACK = 5            # sessions back to search for the gap day
SURGE_VOL_RATIO = 2.0       # volume vs 20d average
SURGE_MOVE_PCT = 3.0        # single-day close-to-close move
SURGE_LOOKBACK = 3

# --- Cross recency ---
SMA50_CROSS_LOOKBACK = 10
GOLDEN_CROSS_LOOKBACK = 15

# --- Gates ---
MIN_SIGNALS = 2
MIN_RALLY_SCORE = 0.35
MAX_DAY_MOVE_PCT = 20.0     # exclude circuit-style one-day spikes, too reversal-prone

RSI_OVERBOUGHT = 75.0
SPIKE_FLAG_PCT = 12.0

MOMENTUM_SIGNALS = {"GAP_HOLD", "VOL_SURGE"}
RECOVERY_SIGNALS = {"FIB_50", "FIB_618", "VOL_ON_BOUNCE", "HIGHER_LOW",
                    "SMA50_RECLAIM", "GOLDEN_CROSS", "RSI_DIVERGENCE"}

SIGNAL_WEIGHTS = {
    "GAP_HOLD": 0.18,
    "VOL_SURGE": 0.16,
    "HOT_SECTOR": 0.08,
    "FIB_50": 0.06,
    "FIB_618": 0.12,
    "VOL_ON_BOUNCE": 0.10,
    "HIGHER_LOW": 0.12,
    "SMA50_RECLAIM": 0.10,
    "GOLDEN_CROSS": 0.06,
    "RSI_DIVERGENCE": 0.10,
}

FIB_LEVELS = [0.382, 0.5, 0.618, 0.786, 1.0]


def find_swing_lows(lows: np.ndarray, window: int = SWING_LOW_WINDOW) -> np.ndarray:
    n = len(lows)
    flags = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        seg = lows[i - window : i + window + 1]
        if lows[i] == seg.min():
            flags[i] = True
    return flags


def find_recovery_leg(df: pd.DataFrame) -> dict | None:
    """Locates the most recent peak -> valley -> bounce structure, if any.

    Returns positional indices so callers can slice with .iloc.
    """
    n = len(df)
    win = df.iloc[-RECOVERY_LOOKBACK:] if n > RECOVERY_LOOKBACK else df
    offset = n - len(win)

    highs = win["high"].to_numpy()
    lows = win["low"].to_numpy()

    peak_pos_local = int(np.argmax(highs))
    peak = float(highs[peak_pos_local])
    if peak_pos_local >= len(win) - MIN_VALLEY_AGE:
        return None  # peak is (almost) the latest bar: no correction to recover from

    after = lows[peak_pos_local:]
    valley_pos_local = peak_pos_local + int(np.argmin(after))
    valley = float(after.min())

    decline_pct = (peak - valley) / peak * 100
    if decline_pct < MIN_DECLINE_PCT:
        return None
    if valley_pos_local >= len(win) - MIN_VALLEY_AGE:
        return None  # valley too fresh, bounce not established

    close = float(win["close"].iloc[-1])
    bounce_pct = (close / valley - 1) * 100
    if bounce_pct < MIN_BOUNCE_PCT:
        return None

    retrace = (close - valley) / (peak - valley)
    return {
        "peak_pos": offset + peak_pos_local,
        "valley_pos": offset + valley_pos_local,
        "peak": peak,
        "valley": valley,
        "peak_date": win.index[peak_pos_local],
        "valley_date": win.index[valley_pos_local],
        "decline_pct": decline_pct,
        "bounce_pct": bounce_pct,
        "retrace": retrace,
    }


def _detect_gap_hold(df: pd.DataFrame) -> dict | None:
    """A 2-3%+ gap-up in the last GAP_LOOKBACK sessions that never filled and is holding."""
    n = len(df)
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()

    for g in range(n - 1, max(n - 1 - GAP_LOOKBACK, 0), -1):
        prev_close, prev_high = closes[g - 1], highs[g - 1]
        gap_pct = (opens[g] / prev_close - 1) * 100
        true_gap = opens[g] > prev_high and gap_pct >= GAP_MIN_PCT
        if not (true_gap or gap_pct >= GAP_MIN_PCT_LOOSE):
            continue
        if closes[g] < opens[g] * 0.99:
            continue  # faded on gap day itself
        if lows[g:].min() <= prev_close:
            continue  # gap was filled back down
        if closes[-1] < opens[g] * 0.99:
            continue  # not holding the gap open anymore
        return {"gap_pct": round(gap_pct, 2), "days_ago": n - 1 - g}
    return None


def _detect_vol_surge(df: pd.DataFrame) -> dict | None:
    """2x+ average volume with a 3%+ up move in the last few sessions, and price holding."""
    n = len(df)
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    avg20 = df["avg_vol20"].to_numpy()

    for d in range(n - 1, max(n - 1 - SURGE_LOOKBACK, 0), -1):
        if np.isnan(avg20[d]) or avg20[d] <= 0:
            continue
        move = (closes[d] / closes[d - 1] - 1) * 100
        ratio = volumes[d] / avg20[d]
        if move >= SURGE_MOVE_PCT and ratio >= SURGE_VOL_RATIO and closes[-1] >= closes[d] * 0.98:
            return {"surge_vol_ratio": round(float(ratio), 2), "surge_move_pct": round(float(move), 2),
                    "days_ago": n - 1 - d}
    return None


def _detect_bounce_volume(df: pd.DataFrame, leg: dict) -> bool:
    """Volume on recovery up-days rivals volume on decline down-days -> real buying."""
    decline = df.iloc[leg["peak_pos"] : leg["valley_pos"] + 1]
    bounce = df.iloc[leg["valley_pos"] :]

    down_days = decline[decline["close"] < decline["close"].shift(1)]
    up_days = bounce[bounce["close"] > bounce["close"].shift(1)]
    if len(up_days) < 2 or len(down_days) < 2:
        return False
    return float(up_days["volume"].mean()) >= 0.9 * float(down_days["volume"].mean())


def _detect_higher_low(df: pd.DataFrame, leg: dict) -> dict | None:
    """A confirmed swing low after the valley that sits above the valley low."""
    lows = df["low"].to_numpy()
    flags = find_swing_lows(lows)
    for pos in range(len(lows) - 1 - SWING_LOW_WINDOW, leg["valley_pos"], -1):
        if flags[pos]:
            hl = float(lows[pos])
            if hl > leg["valley"] * 1.01 and df["close"].iloc[-1] > hl:
                return {"higher_low": hl, "higher_low_pos": pos}
            return None  # most recent swing low undercut the valley zone
    return None


def _detect_rsi_divergence(df: pd.DataFrame) -> bool:
    """Price lower low + RSI higher low on the last two swing lows, with bounce underway."""
    tail = df.iloc[-90:]
    lows = tail["low"].to_numpy()
    rsi = tail["rsi14"].to_numpy()
    closes = tail["close"].to_numpy()
    flags = find_swing_lows(lows)
    swing_pos = [i for i in range(len(lows)) if flags[i]]
    if len(swing_pos) < 2:
        return False
    p1, p2 = swing_pos[-2], swing_pos[-1]
    if len(lows) - 1 - p2 > 20:
        return False  # divergence too old to act on
    price_ll = lows[p2] < lows[p1] * 0.995
    rsi_hl = rsi[p2] > rsi[p1] + 2
    return bool(price_ll and rsi_hl and closes[-1] > closes[p2])


def _estimate_targets(close: float, atr: float, leg: dict | None, category: str) -> dict:
    """Target range + timeframe. Fib-ladder targets for recovery, ATR projection otherwise."""
    target_low = target_high = None

    if leg is not None and category in ("RECOVERY", "BOTH") and leg["retrace"] < 1.0:
        span = leg["peak"] - leg["valley"]
        above = [leg["valley"] + f * span for f in FIB_LEVELS
                 if leg["valley"] + f * span > close * 1.015]
        if above:
            target_low = above[0]
            target_high = min(leg["peak"], close * 1.25)
            if target_high <= target_low:
                target_high = target_low * 1.03

    if target_low is None:  # momentum play, or recovery already back at its peak
        target_low = close + 1.6 * atr
        target_high = close + 3.2 * atr

    dist_low, dist_high = target_low - close, target_high - close
    sessions_low = max(3, dist_low / (0.5 * atr)) if atr > 0 else 5
    sessions_high = min(45, dist_high / (0.3 * atr)) if atr > 0 else 15
    weeks_low = max(1, round(sessions_low / 5))
    weeks_high = max(weeks_low, math.ceil(sessions_high / 5))
    weeks_high = min(weeks_high, 8)

    return {
        "target_low": round(target_low, 2),
        "target_high": round(target_high, 2),
        "upside_low_pct": round(dist_low / close * 100, 2),
        "upside_high_pct": round(dist_high / close * 100, 2),
        "timeframe": f"{weeks_low}–{weeks_high} wk" if weeks_high > weeks_low else f"~{weeks_low} wk",
    }


def analyze_stock(
    df: pd.DataFrame,
    benchmark_close: pd.Series | None = None,
    sector_rank: float | None = None,
) -> dict | None:
    """Runs every rally/recovery detector on the latest bar. Returns a signal dict or None.

    sector_rank: percentile (0-1) of the stock's industry in 1-month sector returns.
    """
    if len(df) < MIN_HISTORY_BARS:
        return None

    df = add_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(latest["close"])

    if pd.isna(latest["sma50"]) or pd.isna(latest["avg_vol20"]) or pd.isna(latest["atr14"]):
        return None
    if pd.isna(latest["turnover20"]) or latest["turnover20"] < MIN_AVG_TURNOVER:
        return None

    day_move_pct = (close / prev["close"] - 1) * 100
    if day_move_pct > MAX_DAY_MOVE_PCT:
        return None

    leg = find_recovery_leg(df)
    signals: list[str] = []
    detail: dict = {}

    gap = _detect_gap_hold(df)
    if gap:
        signals.append("GAP_HOLD")
        detail.update(gap_pct=gap["gap_pct"], gap_days_ago=gap["days_ago"])

    surge = _detect_vol_surge(df)
    if surge:
        signals.append("VOL_SURGE")
        detail.update(surge_vol_ratio=surge["surge_vol_ratio"], surge_move_pct=surge["surge_move_pct"])

    ret_5d = (close / df["close"].iloc[-6] - 1) * 100 if len(df) > 6 else 0.0
    if sector_rank is not None and sector_rank >= 0.75 and ret_5d > 0:
        signals.append("HOT_SECTOR")

    higher_low = None
    if leg is not None:
        if leg["retrace"] < 1.0:
            if leg["retrace"] >= 0.618:
                signals.append("FIB_618")
            elif leg["retrace"] >= 0.5:
                signals.append("FIB_50")
        if _detect_bounce_volume(df, leg):
            signals.append("VOL_ON_BOUNCE")
        higher_low = _detect_higher_low(df, leg)
        if higher_low:
            signals.append("HIGHER_LOW")

    closes = df["close"].to_numpy()
    sma50 = df["sma50"].to_numpy()
    sma200 = df["sma200"].to_numpy()
    if close > sma50[-1] and np.any(closes[-1 - SMA50_CROSS_LOOKBACK : -1] < sma50[-1 - SMA50_CROSS_LOOKBACK : -1]):
        signals.append("SMA50_RECLAIM")
    lb = GOLDEN_CROSS_LOOKBACK
    if (not np.isnan(sma200[-1]) and not np.isnan(sma200[-1 - lb])
            and sma50[-1] > sma200[-1] and sma50[-1 - lb] <= sma200[-1 - lb]):
        signals.append("GOLDEN_CROSS")

    if _detect_rsi_divergence(df):
        signals.append("RSI_DIVERGENCE")

    if len(signals) < MIN_SIGNALS:
        return None

    has_momo = bool(MOMENTUM_SIGNALS & set(signals))
    has_recovery = bool(RECOVERY_SIGNALS & set(signals))
    category = "BOTH" if has_momo and has_recovery else ("MOMENTUM" if has_momo else "RECOVERY")

    # --- Context ---
    rsi = float(latest["rsi14"])
    macd_bull = latest["macd"] > latest["macd_signal"] and latest["macd_hist"] > 0
    rs_1m = _relative_strength(df["close"], benchmark_close, 21)
    rs_3m = _relative_strength(df["close"], benchmark_close, 63)
    atr = float(latest["atr14"])
    atr_pct = atr / close * 100
    vol_ratio = float(latest["volume"] / latest["avg_vol20"]) if latest["avg_vol20"] else 0.0

    # --- Targets & timeframe ---
    targets = _estimate_targets(close, atr, leg, category)

    # --- Stop suggestion ---
    if category in ("RECOVERY", "BOTH") and leg is not None:
        support = higher_low["higher_low"] if higher_low else leg["valley"]
        stop = support * 0.99
    else:
        stop = float(df["low"].iloc[-5:].min()) * 0.995
    stop = min(stop, close - 0.75 * atr)  # never tighter than 3/4 of an ATR
    risk_pct = (close - stop) / close * 100
    rr = (targets["target_low"] - close) / (close - stop) if close > stop else None

    # --- Flags ---
    flags = []
    if rsi > RSI_OVERBOUGHT:
        flags.append("OVERBOUGHT")
    if day_move_pct > SPIKE_FLAG_PCT:
        flags.append("SPIKE_RISK")
    if atr_pct > 6:
        flags.append("HIGH_VOLATILITY")
    if risk_pct > 12:
        flags.append("DEEP_STOP")
    if leg is not None and leg["retrace"] >= 1.0:
        flags.append("FULL_RECOVERY")

    # --- Score ---
    score = sum(SIGNAL_WEIGHTS.get(s, 0.0) for s in signals)
    if macd_bull:
        score += 0.05
    if rs_1m is not None and rs_1m > 0:
        score += 0.05
    if not pd.isna(latest["sma20"]) and close > latest["sma20"]:
        score += 0.03
    if rsi > RSI_OVERBOUGHT:
        score -= 0.08
    if day_move_pct > SPIKE_FLAG_PCT:
        score -= 0.10
    if risk_pct > 10:
        score -= 0.05
    score = max(0.0, min(score, 1.0))
    if score < MIN_RALLY_SCORE:
        return None

    result = {
        "close": round(close, 2),
        "category": category,
        "signals": signals,
        "n_signals": len(signals),
        "score": round(score, 3),
        "day_move_pct": round(day_move_pct, 2),
        "ret_5d": round(float(ret_5d), 2),
        "rsi14": round(rsi, 1),
        "macd_bull": bool(macd_bull),
        "vol_ratio": round(vol_ratio, 2),
        "rs_1m": rs_1m,
        "rs_3m": rs_3m,
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        "stop": round(stop, 2),
        "risk_pct": round(risk_pct, 2),
        "rr": round(rr, 2) if rr is not None else None,
        "turnover_cr": round(float(latest["turnover20"]) / 1e7, 2),
        "flags": flags,
        **targets,
        **detail,
    }
    if leg is not None:
        result.update(
            peak=round(leg["peak"], 2),
            valley=round(leg["valley"], 2),
            peak_date=leg["peak_date"].strftime("%Y-%m-%d"),
            valley_date=leg["valley_date"].strftime("%Y-%m-%d"),
            decline_pct=round(leg["decline_pct"], 2),
            bounce_pct=round(leg["bounce_pct"], 2),
            retrace_pct=round(leg["retrace"] * 100, 1),
        )
    return result
