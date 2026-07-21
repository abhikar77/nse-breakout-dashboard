"""Streamlit dashboard: NSE resistance-breakout watchlist (full NSE EQ universe)."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from breakout import add_indicators, cluster_resistance_zones
from data_fetch import load_prices
from scan import load_latest_scan, run_scan
from universe import BENCHMARK_TICKER, get_yf_tickers

st.set_page_config(page_title="NSE Breakout Watchlist", layout="wide")

st.title("NSE Resistance Breakout Watchlist")
st.caption(
    "Scans all NSE EQ-series stocks for closes above well-tested resistance zones, with "
    "volume, trend, momentum, relative-strength and liquidity filters. Educational tool — not investment advice."
)

scan_data = load_latest_scan()
regime = scan_data.get("regime", {})
signals = scan_data.get("signals", [])
as_of = scan_data.get("as_of")
universe_size = scan_data.get("universe_size", "?")

# --- Market regime banner ---
regime_name = regime.get("regime", "UNKNOWN")
if regime_name == "UPTREND":
    st.success(
        f"**Market regime: UPTREND** — Nifty {regime.get('nifty_close', '?')} "
        f"({regime.get('nifty_ret_1m', '?'):+.1f}% 1M). Breakout setups have the wind at their back."
    )
elif regime_name == "DOWNTREND":
    st.error(
        f"**Market regime: DOWNTREND** — Nifty {regime.get('nifty_close', '?')} "
        f"({regime.get('nifty_ret_1m', '?'):+.1f}% 1M). Breakouts fail more often in falling markets — extra caution."
    )
elif regime_name == "NEUTRAL":
    st.warning(
        f"**Market regime: NEUTRAL** — Nifty {regime.get('nifty_close', '?')} "
        f"({regime.get('nifty_ret_1m', '?'):+.1f}% 1M). Mixed trend signals; be selective."
    )

with st.sidebar:
    st.header("Controls")
    if st.button("Refresh data + rescan", type="primary", use_container_width=True):
        from data_fetch import fetch_universe

        tickers = get_yf_tickers(scope="all") + [BENCHMARK_TICKER]
        prog = st.progress(0.0, text="Downloading latest prices...")
        fetch_universe(
            tickers,
            progress_cb=lambda done, total: prog.progress(
                min(done / total, 1.0), text=f"Downloading prices {done}/{total}"
            ),
        )
        prog.progress(1.0, text="Scanning for breakouts...")
        run_scan()
        st.success("Done")
        st.rerun()

    st.divider()
    status_filter = st.multiselect(
        "Status",
        ["CONFIRMED", "BREAKOUT", "CREAKING"],
        default=["CONFIRMED", "BREAKOUT", "CREAKING"],
        help="CREAKING = pressing the ceiling; BREAKOUT = first close above; CONFIRMED = held above next day too",
    )
    min_score = st.slider("Minimum score", 0.0, 1.0, 0.55, 0.05)
    min_touches = st.slider("Minimum resistance touches", 2, 8, 2)
    min_vol_ratio = st.slider("Minimum volume ratio (x avg)", 1.0, 5.0, 1.5, 0.1)
    hide_overbought = st.checkbox("Hide overbought (RSI > 75)", value=False)
    hide_weak_close = st.checkbox("Require strong close (upper 1/3 of range)", value=False)
    only_52w = st.checkbox("Only 52-week highs", value=False)

if not signals:
    st.warning("No scan results yet. Click **Refresh data + rescan** in the sidebar to run the first scan.")
    st.stop()

st.caption(
    f"Data as of: **{as_of}** &nbsp;|&nbsp; universe: **{universe_size}** NSE stocks "
    f"&nbsp;|&nbsp; {len(signals)} flagged &nbsp;|&nbsp; liquidity floor: ₹2 Cr avg daily turnover"
)

df = pd.DataFrame(signals)
df["flags_str"] = df["flags"].apply(lambda f: ", ".join(f) if f else "")

mask = (
    df["status"].isin(status_filter)
    & (df["score"] >= min_score)
    & (df["touches"] >= min_touches)
    & (df["volume_ratio"] >= min_vol_ratio)
)
if hide_overbought:
    mask &= df["rsi14"] <= 75
if hide_weak_close:
    mask &= df["close_position"] >= 0.66
if only_52w:
    mask &= df["is_52w_high"]

filtered = df[mask].reset_index(drop=True)

st.subheader(f"Watchlist ({len(filtered)} stocks)")

display_cols = [
    "symbol", "status", "score", "close", "zone_level", "pct_vs_zone", "touches",
    "volume_ratio", "vol_trend", "rsi14", "rs_1m", "rs_3m", "sector_1m",
    "stop", "risk_pct", "turnover_cr", "industry", "flags_str",
]
st.dataframe(
    filtered[display_cols].sort_values("score", ascending=False),
    width="stretch",
    height=min(35 * (len(filtered) + 1), 560),
    column_config={
        "score": st.column_config.ProgressColumn("score", min_value=0.0, max_value=1.0, format="%.2f"),
        "pct_vs_zone": st.column_config.NumberColumn("vs zone %", format="%+.1f%%"),
        "volume_ratio": st.column_config.NumberColumn("vol x", format="%.1fx"),
        "vol_trend": st.column_config.NumberColumn("vol 3d x", format="%.1fx"),
        "rs_1m": st.column_config.NumberColumn("RS 1M", format="%+.1f", help="Return vs Nifty, 1 month, pct points"),
        "rs_3m": st.column_config.NumberColumn("RS 3M", format="%+.1f"),
        "sector_1m": st.column_config.NumberColumn("sector 1M %", format="%+.1f%%"),
        "risk_pct": st.column_config.NumberColumn("risk %", format="%.1f%%", help="Distance from close to suggested stop"),
        "turnover_cr": st.column_config.NumberColumn("turnover ₹Cr", format="%.1f"),
        "flags_str": st.column_config.TextColumn("flags"),
    },
)

st.divider()
st.subheader("Chart inspector")

if filtered.empty:
    st.info("No stocks match the current filters.")
    st.stop()

selected = st.selectbox("Select a stock to inspect", filtered["symbol"].tolist())
row = filtered[filtered["symbol"] == selected].iloc[0]

symbol_yf = f"{selected}.NS"
price_df = load_prices(symbol_yf)
price_df = add_indicators(price_df)
zones = cluster_resistance_zones(price_df)
plot_df = price_df.tail(180)

fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03
)
fig.add_trace(
    go.Candlestick(
        x=plot_df.index, open=plot_df["open"], high=plot_df["high"],
        low=plot_df["low"], close=plot_df["close"], name=selected,
    ),
    row=1, col=1,
)
for col_name, color in [("sma20", "royalblue"), ("sma50", "orange"), ("sma200", "purple")]:
    fig.add_trace(
        go.Scatter(x=plot_df.index, y=plot_df[col_name], line=dict(color=color, width=1),
                   name=col_name.upper()),
        row=1, col=1,
    )
for z in zones:
    if z["level"] < plot_df["low"].min() * 0.95 or z["level"] > plot_df["high"].max() * 1.05:
        continue
    fig.add_hline(
        y=z["level"], line=dict(color="red", width=1, dash="dot"),
        annotation_text=f"resistance {z['level']:.1f} ({z['touches']}x)",
        annotation_position="right", row=1, col=1,
    )
fig.add_hline(
    y=row["stop"], line=dict(color="black", width=1, dash="dash"),
    annotation_text=f"stop {row['stop']:.1f}", annotation_position="left", row=1, col=1,
)

vol_colors = [
    "seagreen" if c >= o else "indianred"
    for c, o in zip(plot_df["close"], plot_df["open"])
]
fig.add_trace(
    go.Bar(x=plot_df.index, y=plot_df["volume"], marker_color=vol_colors, name="Volume"),
    row=2, col=1,
)
fig.add_trace(
    go.Scatter(x=plot_df.index, y=plot_df["avg_vol20"], line=dict(color="gray", width=1),
               name="20d avg vol"),
    row=2, col=1,
)

fig.update_layout(
    height=650, xaxis_rangeslider_visible=False,
    title=f"{selected} — last 180 sessions", legend=dict(orientation="h", y=1.06),
)
st.plotly_chart(fig, width="stretch")

# --- Signal summary + trade plan ---
c1, c2 = st.columns(2)

with c1:
    st.markdown("**Signal summary**")
    cols = st.columns(4)
    cols[0].metric("Status", row["status"])
    cols[1].metric("Score", f"{row['score']:.2f}")
    cols[2].metric("vs Zone", f"{row['pct_vs_zone']:+.2f}%")
    cols[3].metric("Touches", int(row["touches"]))
    cols = st.columns(4)
    cols[0].metric("Vol Ratio", f"{row['volume_ratio']:.1f}x")
    cols[1].metric("RSI(14)", f"{row['rsi14']:.1f}")
    cols[2].metric("RS 1M", f"{row['rs_1m']:+.1f}" if pd.notna(row["rs_1m"]) else "n/a")
    cols[3].metric("ATR", f"{row['atr_pct']:.1f}%")
    if row["flags_str"]:
        st.warning(f"Flags: {row['flags_str']}")

with c2:
    st.markdown("**Trade plan (rule-based, 1-2 week time-box)**")
    entry = row["close"]
    stop = row["stop"]
    target_low, target_high = entry * 1.05, entry * 1.07
    rr = (target_low - entry) / (entry - stop) if entry > stop else float("nan")
    cols = st.columns(4)
    cols[0].metric("Entry (last close)", f"{entry:.2f}")
    cols[1].metric("Stop", f"{stop:.2f}", f"-{row['risk_pct']:.1f}%", delta_color="inverse")
    cols[2].metric("Target zone", f"{target_low:.0f}–{target_high:.0f}", "+5–7%")
    cols[3].metric("Reward:Risk", f"{rr:.1f}" if pd.notna(rr) else "n/a")
    st.caption(
        "Position size = (capital you'll risk) ÷ (entry − stop). Exit if neither target nor stop "
        "is hit within 2 weeks. If status is BREAKOUT, consider waiting for next-day confirmation."
    )
