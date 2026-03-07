"""Streamlit dashboard for the Driftwood trading system."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

# Ensure project-root imports work when launching from dashboard/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config, is_paper_trading
from data.database import get_engine, load_ohlcv
from data.kraken_client import fetch_ohlcv
from signals.indicators import compute_all_indicators, compute_trend_signal


# Page configuration.
st.set_page_config(page_title="Driftwood Trading System", page_icon="🌊", layout="wide")


@st.cache_data(ttl=60)
def load_open_trades_count() -> int:
    """Return current number of open trades from the database."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) AS count FROM trades WHERE status = 'open'"))
        row = result.fetchone()
    return int(row[0]) if row else 0


@st.cache_data(ttl=60)
def load_recent_events(limit: int = 10) -> pd.DataFrame:
    """Load recent system events ordered newest-first."""
    engine = get_engine()
    query = text(
        """
        SELECT timestamp AS time, event_type, message
        FROM system_events
        ORDER BY timestamp DESC
        LIMIT :limit
        """
    )
    return pd.read_sql(query, engine, params={"limit": limit})


@st.cache_data(ttl=60)
def load_trade_history(limit: int = 20) -> pd.DataFrame:
    """Load recent trades ordered newest-first."""
    engine = get_engine()
    query = text(
        """
        SELECT symbol, side, entry_price, exit_price, quantity, pnl, fees, status, mode, entry_time
        FROM trades
        ORDER BY entry_time DESC
        LIMIT :limit
        """
    )
    return pd.read_sql(query, engine, params={"limit": limit})


@st.cache_data(ttl=60)
def load_today_realized_pnl() -> float:
    """Sum today's realized PnL from closed trades (UTC day)."""
    engine = get_engine()
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = text(
        """
        SELECT COALESCE(SUM(pnl), 0) AS total_pnl
        FROM trades
        WHERE status = 'closed' AND exit_time >= :day_start
        """
    )
    with engine.connect() as conn:
        result = conn.execute(query, {"day_start": day_start})
        row = result.fetchone()
    return float(row[0]) if row else 0.0


@st.cache_data(ttl=60)
def load_pair_signal_snapshot(symbol: str) -> dict:
    """Fetch fresh OHLCV and compute multi-timeframe signal summary for one symbol."""
    daily_raw = fetch_ohlcv(symbol, "1d", limit=500)
    four_hour_raw = fetch_ohlcv(symbol, "4h", limit=500)
    one_hour_raw = fetch_ohlcv(symbol, "1h", limit=500)

    daily = compute_all_indicators(daily_raw, "daily")
    four_hour = compute_all_indicators(four_hour_raw, "four_hour")
    one_hour = compute_all_indicators(one_hour_raw, "one_hour")

    signal = compute_trend_signal(daily, four_hour, one_hour)
    current_price = float(one_hour_raw["close"].iloc[-1])

    return {
        "symbol": symbol,
        "current_price": current_price,
        "signal": signal,
    }


@st.cache_data(ttl=60)
def load_chart_data(symbol: str) -> pd.DataFrame:
    """Load 4h candles from DB for a symbol and enrich with moving averages."""
    df = load_ohlcv(symbol, "4h", limit=500)
    if df.empty:
        return df
    return compute_all_indicators(df, "four_hour")


def _signal_color(signal_value: str) -> str:
    signal = signal_value.lower()
    if signal == "buy":
        return "#16a34a"
    if signal == "sell":
        return "#dc2626"
    return "#6b7280"


# Keep session toggles stable across reruns.
if "pause_trading" not in st.session_state:
    st.session_state.pause_trading = False
if "halted" not in st.session_state:
    st.session_state.halted = False
if "halt_reason" not in st.session_state:
    st.session_state.halt_reason = ""

config = get_config()
mode_is_paper = is_paper_trading()
mode_label = "Paper" if mode_is_paper else "Live"
mode_badge_color = "#16a34a" if mode_is_paper else "#dc2626"

# Sidebar controls and runtime context.
with st.sidebar:
    st.markdown("## 🌊 Driftwood")
    st.markdown(
        f"<span style='background:{mode_badge_color};color:white;padding:0.2rem 0.5rem;border-radius:0.5rem;'>"
        f"Mode: {mode_label}</span>",
        unsafe_allow_html=True,
    )

    if st.button("Refresh Data", use_container_width=True):
        st.rerun()

    st.session_state.pause_trading = st.toggle(
        "Pause Trading", value=st.session_state.pause_trading
    )

    if st.button("Kill Switch", type="primary", use_container_width=True):
        st.session_state.halted = True
        st.session_state.halt_reason = "Kill switch activated by operator"
        st.warning("Kill switch engaged. Trading is now halted.")

    st.caption(f"UTC now: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")


st.title("Driftwood Trading System")

# Section 1: System Status row.
st.subheader("System Status")
col1, col2, col3 = st.columns(3)

status_label = "HALTED" if st.session_state.halted else "Running"
status_delta = (
    "Paused" if st.session_state.pause_trading and not st.session_state.halted else "Active"
)
if st.session_state.halted:
    status_delta = "Kill Switch"

with col1:
    st.metric("System Status", status_label, delta=status_delta)
with col2:
    st.metric("Trading Mode", mode_label)
with col3:
    try:
        open_trades_count = load_open_trades_count()
    except Exception as exc:
        open_trades_count = 0
        st.error(f"Could not load open trades count: {exc}")
    st.metric("Open Trades", open_trades_count)

# Section 2: Signal panel.
st.subheader("Signal Panel")
pair_cols = st.columns(2)
for idx, pair in enumerate(["BTC/USD", "ETH/USD"]):
    with pair_cols[idx]:
        try:
            snapshot = load_pair_signal_snapshot(pair)
            sig = snapshot["signal"]
            signal_value = str(sig.get("signal", "hold")).upper()
            color = _signal_color(signal_value)

            st.markdown(f"### {pair}")
            st.markdown(f"**Current price:** ${snapshot['current_price']:,.2f}")
            st.markdown(
                f"**Overall signal:** <span style='color:{color};font-weight:700;'>{signal_value}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"**Daily trend:** {sig.get('daily_trend', 'n/a')} | "
                f"**4h trend:** {sig.get('four_hour_trend', 'n/a')} | "
                f"**1h trend:** {sig.get('one_hour_trend', 'n/a')}"
            )
            st.markdown(
                f"**Volume confirmed:** {'Yes' if sig.get('volume_confirmed') else 'No'}"
            )
            st.caption(f"Reason: {sig.get('reason', 'No reason available')}")
        except Exception as exc:
            st.error(f"Failed to build signal panel for {pair}: {exc}")

# Section 3: Risk utilization.
st.subheader("Risk Utilization")
r1, r2, r3 = st.columns(3)

risk_cfg = config.get("risk", {})
trading_cfg = config.get("trading", {})
portfolio_cfg = config.get("portfolio", {})
max_daily_loss_pct = float(risk_cfg.get("max_daily_loss_pct", 0.03))
max_open_trades = int(trading_cfg.get("max_open_trades", 1))
initial_capital = float(portfolio_cfg.get("initial_capital_usd", 1000.0))

try:
    today_realized_pnl = load_today_realized_pnl()
except Exception as exc:
    today_realized_pnl = 0.0
    st.error(f"Could not load daily PnL: {exc}")

try:
    open_trades_for_risk = load_open_trades_count()
except Exception:
    open_trades_for_risk = 0

# Loss usage is only counted when today PnL is negative.
daily_loss_pct_used = max(0.0, (-today_realized_pnl / initial_capital) * 100)
daily_limit_pct = max_daily_loss_pct * 100
daily_usage_ratio = min(daily_loss_pct_used / daily_limit_pct, 1.0) if daily_limit_pct > 0 else 0.0
portfolio_value = initial_capital + today_realized_pnl

with r1:
    st.metric("Daily Loss % Used", f"{daily_loss_pct_used:.2f}%", delta=f"Limit {daily_limit_pct:.2f}%")
    st.progress(daily_usage_ratio)
with r2:
    st.metric("Open Trades", f"{open_trades_for_risk}/{max_open_trades}")
with r3:
    st.metric("Portfolio Value", f"${portfolio_value:,.2f}")

# Section 4: Recent decisions.
st.subheader("Recent Decisions")
try:
    events_df = load_recent_events(limit=10)
    if events_df.empty:
        st.info("No system events yet.")
    else:
        st.dataframe(events_df, use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(f"Could not load recent events: {exc}")

# Section 5: Trade history.
st.subheader("Trade History")
try:
    trades_df = load_trade_history(limit=20)
    if trades_df.empty:
        st.info("No trades recorded yet.")
    else:
        styled = trades_df.style.apply(
            lambda row: [
                "color: #16a34a" if pd.notna(row["pnl"]) and row["pnl"] > 0
                else "color: #dc2626" if pd.notna(row["pnl"]) and row["pnl"] < 0
                else ""
                for _ in row.index
            ],
            axis=1,
            subset=["pnl"],
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(f"Could not load trade history: {exc}")

# Section 6: Price chart.
st.subheader("Price Chart")
selected_pair = st.selectbox("Select pair", ["BTC/USD", "ETH/USD"], index=0)
try:
    chart_df = load_chart_data(selected_pair)
    if chart_df.empty:
        st.info(f"No {selected_pair} 4h candles available in the database.")
    else:
        fig = go.Figure()
        fig.add_trace(
            go.Candlestick(
                x=chart_df.index,
                open=chart_df["open"],
                high=chart_df["high"],
                low=chart_df["low"],
                close=chart_df["close"],
                name="OHLC",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=chart_df.index,
                y=chart_df["fast_ma"],
                mode="lines",
                name="Fast MA",
                line=dict(width=1.5),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=chart_df.index,
                y=chart_df["slow_ma"],
                mode="lines",
                name="Slow MA",
                line=dict(width=1.5),
            )
        )
        fig.update_layout(
            title=f"{selected_pair} — 4h with Moving Averages",
            xaxis_title="Time (UTC)",
            yaxis_title="Price",
            xaxis_rangeslider_visible=False,
            height=600,
        )
        st.plotly_chart(fig, use_container_width=True)
except Exception as exc:
    st.error(f"Could not render BTC chart: {exc}")
