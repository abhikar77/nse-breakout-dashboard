"""Streamlit dashboard: NSE rally & recovery scanner (full NSE EQ universe).

Companion to the breakout watchlist (app.py) — same price DB, different lens:
momentum continuation (gaps, volume surges, hot sectors) and bottom reversals
(Fib reclaims, higher lows, DMA reclaims, RSI divergence), each with an
estimated target range and timeframe.
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from breakout import add_indicators
from data_fetch import load_prices
from rally import FIB_LEVELS, find_recovery_leg
from rally_scan import load_latest_rally_scan, run_rally_scan
from universe import BENCHMARK_TICKER, get_yf_tickers

st.set_page_config(page_title="NSE Rally & Recovery Scanner", layout="wide")

SIGNAL_HELP = {
    "GAP_HOLD": "Gapped up 2-3%+ and the gap never filled — historically tends to see continuation.",
    "VOL_SURGE": "Traded 2x+ its average volume alongside a 3%+ up move — crowd is noticing.",
    "HOT_SECTOR": "Its industry is in the top quartile of 1-month sector returns — rotation tailwind.",
    "FIB_50": "Bounce reclaimed 50% of the prior fall — recovery gaining credibility.",
    "FIB_618": "Bounce reclaimed 61.8% of the prior fall — technically a strong sign the uptrend is resuming.",
    "VOL_ON_BOUNCE": "Up-day volume on the recovery rivals the down-day volume of the fall — real buying, not just absent sellers.",
    "HIGHER_LOW": "Printed a higher swing low after the valley — the valley is acting as a demand zone.",
    "SMA50_RECLAIM": "Closed back above the 50-DMA within the last 10 sessions — corrective phase likely over.",
    "GOLDEN_CROSS": "50-DMA crossed above the 200-DMA recently — long-term trend turning up.",
    "RSI_DIVERGENCE": "Price made a lower low while RSI made a higher low — selling momentum faded before the turn.",
}
ALL_SIGNALS = list(SIGNAL_HELP.keys())


def _check_password() -> bool:
    """Gate the app behind a password when APP_PASSWORD is configured (e.g. on Streamlit Cloud)."""
    try:
        app_password = st.secrets["APP_PASSWORD"]
    except (KeyError, FileNotFoundError):
        return True
    if st.session_state.get("authenticated"):
        return True

    def _on_submit():
        if st.session_state.get("password_input") == app_password:
            st.session_state["authenticated"] = True
        else:
            st.session_state["authenticated"] = False

    st.title("NSE Rally & Recovery Scanner")
    st.text_input("Password", type="password", key="password_input", on_change=_on_submit)
    if st.session_state.get("authenticated") is False:
        st.error("Incorrect password")
    return False


if not _check_password():
    st.stop()

st.title("NSE Rally & Recovery Scanner")
st.caption(
    "Scans all NSE EQ-series stocks for momentum-continuation signals (held gap-ups, volume surges, "
    "sector rotation) and bottom-reversal signals (Fibonacci reclaims, bounce volume, higher lows, "
    "DMA reclaims, golden crosses, RSI divergence), with an estimated target range + timeframe per stock. "
    "Results-driven momentum and short-covering rallies are **not** covered (they need earnings-calendar / "
    "F&O open-interest data with no free bulk source). Educational tool — not investment advice."
)

scan_data = load_latest_rally_scan()
regime = scan_data.get("regime", {})
signals = scan_data.get("signals", [])
as_of = scan_data.get("as_of")
universe_size = scan_data.get("universe_size", "?")

regime_name = regime.get("regime", "UNKNOWN")
if regime_name == "UPTREND":
    st.success(
        f"**Market regime: UPTREND** — Nifty {regime.get('nifty_close', '?')} "
        f"({regime.get('nifty_ret_1m', '?'):+.1f}% 1M). Rallies have the wind at their back."
    )
elif regime_name == "DOWNTREND":
    st.error(
        f"**Market regime: DOWNTREND** — Nifty {regime.get('nifty_close', '?')} "
        f"({regime.get('nifty_ret_1m', '?'):+.1f}% 1M). Bounces fail more often in falling markets — extra caution."
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
        prog.progress(1.0, text="Scanning for rally signals...")
        run_rally_scan()
        st.success("Done")
        st.rerun()

    st.divider()
    category_filter = st.multiselect(
        "Category",
        ["MOMENTUM", "RECOVERY", "BOTH"],
        default=["MOMENTUM", "RECOVERY", "BOTH"],
        help="MOMENTUM = gap/volume continuation; RECOVERY = bounce off a 12%+ correction; BOTH = overlap",
    )
    required_signals = st.multiselect(
        "Must include signals", ALL_SIGNALS,
        help="Stock must show every selected signal. Leave empty for no constraint.",
    )
    min_signals = st.slider("Minimum signal count", 2, 7, 2)
    min_score = st.slider("Minimum score", 0.0, 1.0, 0.35, 0.05)
    min_upside = st.slider("Minimum upside to lower target (%)", 0.0, 10.0, 0.0, 0.5)
    max_risk = st.slider("Maximum risk to stop (%)", 3.0, 20.0, 20.0, 0.5)
    hide_overbought = st.checkbox("Hide overbought (RSI > 75)", value=False)

with st.expander("What each signal means"):
    for name, desc in SIGNAL_HELP.items():
        st.markdown(f"- **{name}** — {desc}")

if not signals:
    st.warning("No scan results yet. Click **Refresh data + rescan** in the sidebar to run the first scan.")
    st.stop()

st.caption(
    f"Data as of: **{as_of}** &nbsp;|&nbsp; universe: **{universe_size}** NSE stocks "
    f"&nbsp;|&nbsp; {len(signals)} flagged &nbsp;|&nbsp; liquidity floor: ₹2 Cr avg daily turnover"
)

df = pd.DataFrame(signals)
df["signals_str"] = df["signals"].apply(lambda s: ", ".join(s))
df["flags_str"] = df["flags"].apply(lambda f: ", ".join(f) if f else "")
df["target_range"] = df.apply(lambda r: f"{r['target_low']:.0f}–{r['target_high']:.0f}", axis=1)
df["upside_range"] = df.apply(lambda r: f"{r['upside_low_pct']:+.1f} to {r['upside_high_pct']:+.1f}%", axis=1)

mask = (
    df["category"].isin(category_filter)
    & (df["n_signals"] >= min_signals)
    & (df["score"] >= min_score)
    & (df["upside_low_pct"] >= min_upside)
    & (df["risk_pct"] <= max_risk)
)
for sig in required_signals:
    mask &= df["signals"].apply(lambda s: sig in s)
if hide_overbought:
    mask &= df["rsi14"] <= 75

filtered = df[mask].reset_index(drop=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Matching stocks", len(filtered))
c2.metric("Momentum", int((filtered["category"] == "MOMENTUM").sum()))
c3.metric("Recovery", int((filtered["category"] == "RECOVERY").sum()))
c4.metric("Both", int((filtered["category"] == "BOTH").sum()))

st.subheader(f"Rally watchlist ({len(filtered)} stocks)")

display_cols = [
    "symbol", "category", "score", "n_signals", "signals_str", "close",
    "target_range", "upside_range", "timeframe", "stop", "risk_pct", "rr",
    "retrace_pct", "rsi14", "rs_1m", "sector_1m", "turnover_cr", "industry", "flags_str",
]
display_cols = [c for c in display_cols if c in filtered.columns]
st.dataframe(
    filtered[display_cols].sort_values("score", ascending=False),
    width="stretch",
    height=min(35 * (len(filtered) + 1), 560),
    column_config={
        "score": st.column_config.ProgressColumn("score", min_value=0.0, max_value=1.0, format="%.2f"),
        "n_signals": st.column_config.NumberColumn("# sig"),
        "signals_str": st.column_config.TextColumn("signals", width="large"),
        "target_range": st.column_config.TextColumn("target ₹"),
        "upside_range": st.column_config.TextColumn("upside"),
        "timeframe": st.column_config.TextColumn("time"),
        "risk_pct": st.column_config.NumberColumn("risk %", format="%.1f%%", help="Distance from close to suggested stop"),
        "rr": st.column_config.NumberColumn("R:R", format="%.1f", help="Reward to lower target vs risk to stop"),
        "retrace_pct": st.column_config.NumberColumn("retrace %", format="%.0f%%", help="How much of the prior fall has been reclaimed"),
        "rs_1m": st.column_config.NumberColumn("RS 1M", format="%+.1f", help="Return vs Nifty, 1 month, pct points"),
        "sector_1m": st.column_config.NumberColumn("sector 1M %", format="%+.1f%%"),
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
leg = find_recovery_leg(price_df)
plot_df = price_df.tail(220)

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

if leg is not None:
    span = leg["peak"] - leg["valley"]
    for f in FIB_LEVELS:
        level = leg["valley"] + f * span
        fig.add_hline(
            y=level, line=dict(color="teal", width=1, dash="dot"),
            annotation_text=f"{f * 100:.1f}%  {level:.1f}",
            annotation_position="right", row=1, col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=[leg["peak_date"], leg["valley_date"]], y=[leg["peak"], leg["valley"]],
            mode="markers+text", text=["peak", "valley"], textposition="top center",
            marker=dict(color=["crimson", "seagreen"], size=10, symbol="diamond"),
            name="peak/valley",
        ),
        row=1, col=1,
    )

fig.add_hrect(
    y0=row["target_low"], y1=row["target_high"],
    fillcolor="seagreen", opacity=0.12, line_width=0, row=1, col=1,
)
fig.add_hline(
    y=row["target_low"], line=dict(color="seagreen", width=1, dash="dash"),
    annotation_text=f"target {row['target_low']:.1f}–{row['target_high']:.1f}",
    annotation_position="left", row=1, col=1,
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
    title=f"{selected} — last 220 sessions", legend=dict(orientation="h", y=1.06),
)
st.plotly_chart(fig, width="stretch")

# --- Signal summary + trade plan ---
c1, c2 = st.columns(2)

with c1:
    st.markdown("**Why it's flagged**")
    for sig in row["signals"]:
        st.markdown(f"- **{sig}** — {SIGNAL_HELP.get(sig, '')}")
    if row["flags_str"]:
        st.warning(f"Flags: {row['flags_str']}")
    cols = st.columns(4)
    cols[0].metric("Category", row["category"])
    cols[1].metric("Score", f"{row['score']:.2f}")
    cols[2].metric("RSI(14)", f"{row['rsi14']:.1f}")
    cols[3].metric(
        "Retrace",
        f"{row['retrace_pct']:.0f}%" if "retrace_pct" in row and pd.notna(row.get("retrace_pct")) else "n/a",
        help="Portion of the prior fall reclaimed",
    )

with c2:
    st.markdown(f"**Trade plan (rule-based, est. {row['timeframe']})**")
    entry = row["close"]
    cols = st.columns(4)
    cols[0].metric("Entry (last close)", f"{entry:.2f}")
    cols[1].metric("Stop", f"{row['stop']:.2f}", f"-{row['risk_pct']:.1f}%", delta_color="inverse")
    cols[2].metric(
        "Target range", row["target_range"],
        f"{row['upside_low_pct']:+.1f} to {row['upside_high_pct']:+.1f}%",
    )
    cols[3].metric("Reward:Risk", f"{row['rr']:.1f}" if pd.notna(row["rr"]) else "n/a")
    st.caption(
        "Targets: recovery plays aim for the next Fibonacci level up to the prior peak; momentum plays "
        "project 1.6–3.2x ATR. Timeframe assumes ~0.3–0.5 ATR net progress per session. Position size = "
        "(capital you'll risk) ÷ (entry − stop). Exit if neither target nor stop is hit within the timeframe."
    )
