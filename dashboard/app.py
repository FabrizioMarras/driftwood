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
        result = conn.execute(text("SELECT COUNT(*) FROM trades WHERE status = 'open'"))
        row = result.fetchone()
    return int(row[0]) if row else 0


@st.cache_data(ttl=60)
def load_recent_events(limit: int = 15) -> pd.DataFrame:
    """Load recent system events ordered newest-first."""
    engine = get_engine()
    query = text(
        """
        SELECT timestamp, event_type, message
        FROM system_events
        ORDER BY timestamp DESC
        LIMIT :limit
        """
    )
    return pd.read_sql(query, engine, params={"limit": limit})


@st.cache_data(ttl=60)
def load_trade_history(limit: int = 100) -> pd.DataFrame:
    """Load recent trades ordered newest-first."""
    engine = get_engine()
    query = text(
        """
        SELECT symbol, side, entry_price, exit_price, quantity, pnl, fees, status, mode, entry_time, exit_time
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
        SELECT COALESCE(SUM(pnl), 0)
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

    return {"symbol": symbol, "current_price": current_price, "signal": signal}


@st.cache_data(ttl=60)
def load_chart_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load chart candles from DB and compute matching timeframe indicators."""
    timeframe_key_map = {"1h": "one_hour", "4h": "four_hour", "1d": "daily"}
    tf_key = timeframe_key_map[timeframe]
    candles = load_ohlcv(symbol, timeframe, limit=500)
    if candles.empty:
        return candles
    return compute_all_indicators(candles, tf_key)


@st.cache_data(ttl=60)
def load_last_cycle_event() -> pd.DataFrame:
    """Load the most recent scheduler cycle event (trade_opened or trade_skipped)."""
    engine = get_engine()
    query = text(
        """
        SELECT timestamp, event_type, message
        FROM system_events
        WHERE event_type IN ('trade_opened', 'trade_skipped')
        ORDER BY timestamp DESC
        LIMIT 1
        """
    )
    return pd.read_sql(query, engine)


@st.cache_data(ttl=60)
def load_closed_trades_for_equity() -> pd.DataFrame:
    """Load all closed trades in chronological close order for equity curve."""
    engine = get_engine()
    query = text(
        """
        SELECT exit_time, pnl
        FROM trades
        WHERE status = 'closed'
        ORDER BY exit_time ASC
        """
    )
    return pd.read_sql(query, engine)


def _signal_color(signal_value: str) -> str:
    val = signal_value.lower()
    if val == "buy":
        return "#16a34a"
    if val == "sell":
        return "#dc2626"
    return "#6b7280"


def _trend_color(value: str) -> str:
    v = value.lower()
    if v in {"up", "bullish"}:
        return "#16a34a"
    if v in {"down", "bearish"}:
        return "#dc2626"
    return "#6b7280"


def _format_time_ago(ts: pd.Timestamp | datetime | None) -> str:
    """Convert timestamp into a simple human-readable relative string."""
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "No cycles yet"

    value = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(value):
        return "No cycles yet"

    delta = datetime.now(timezone.utc) - value.to_pydatetime()
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        mins = seconds // 60
        return f"{mins} min{'s' if mins != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _event_label(event_type: str) -> str:
    mapping = {
        "trade_opened": "✅ Trade Opened",
        "trade_closed": "🔒 Trade Closed",
        "trade_skipped": "⏭ Skipped",
        "error": "❌ Error",
    }
    return mapping.get(event_type, "ℹ️ Info")


def _render_signal_box(signal_text: str) -> None:
    color = _signal_color(signal_text)
    st.markdown(
        (
            "<div style='padding:0.9rem 1rem;border-radius:0.65rem;"
            f"background:{color};text-align:center;'>"
            f"<span style='font-size:1.5rem;font-weight:700;color:white;'>{signal_text.upper()}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_custom_progress(ratio: float) -> None:
    """Render a simple progress bar that turns red above 50% usage."""
    ratio = min(max(ratio, 0.0), 1.0)
    fill_color = "#dc2626" if ratio > 0.5 else "#16a34a"
    width_pct = ratio * 100
    st.markdown(
        (
            "<div style='width:100%;height:12px;background:#e5e7eb;border-radius:999px;'>"
            f"<div style='height:12px;width:{width_pct:.2f}%;background:{fill_color};"
            "border-radius:999px;'></div></div>"
        ),
        unsafe_allow_html=True,
    )


# Keep session toggles stable across reruns.
if "pause_trading" not in st.session_state:
    st.session_state.pause_trading = False
if "halted" not in st.session_state:
    st.session_state.halted = False
if "halt_reason" not in st.session_state:
    st.session_state.halt_reason = ""

config = get_config()
risk_cfg = config.get("risk", {})
trading_cfg = config.get("trading", {})
portfolio_cfg = config.get("portfolio", {})
initial_capital = float(portfolio_cfg.get("initial_capital_usd", 1000.0))
max_open_trades = int(trading_cfg.get("max_open_trades", 1))
max_daily_loss_pct = float(risk_cfg.get("max_daily_loss_pct", 0.03))

mode_is_paper = is_paper_trading()
mode_label = "Paper" if mode_is_paper else "Live"
mode_badge_color = "#16a34a" if mode_is_paper else "#dc2626"

# Shared last-cycle text used in sidebar and status row.
try:
    last_cycle_df = load_last_cycle_event()
    if last_cycle_df.empty:
        last_cycle_text = "No cycles yet"
    else:
        last_cycle_text = _format_time_ago(last_cycle_df.iloc[0]["timestamp"])
except Exception:
    last_cycle_text = "No cycles yet"

# Sidebar.
try:
    with st.sidebar:
        st.markdown("## 🌊 Driftwood")
        st.markdown(
            (
                f"<span style='background:{mode_badge_color};color:white;padding:0.2rem 0.55rem;"
                "border-radius:0.5rem;'>"
                f"Mode: {mode_label}</span>"
            ),
            unsafe_allow_html=True,
        )

        st.caption(f"Last cycle: {last_cycle_text}")

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
except Exception as exc:
    st.error(f"Sidebar failed to render: {exc}")


st.title("Driftwood Trading System")

# Section 1 — System Status.
try:
    st.subheader("System Status")
    c1, c2, c3, c4 = st.columns(4)

    status_label = "HALTED" if st.session_state.halted else "Running"

    with c1:
        st.metric("System Status", status_label)
    with c2:
        st.metric("Trading Mode", mode_label)
    with c3:
        open_trades_count = load_open_trades_count()
        st.metric("Open Trades", open_trades_count)
    with c4:
        st.metric("Last Cycle", last_cycle_text)
except Exception as exc:
    st.error(f"System Status section failed: {exc}")

st.divider()

# Section 2 — Signal Panel.
try:
    st.subheader("Signal Panel")
    cols = st.columns(2)
    for idx, pair in enumerate(["BTC/USD", "ETH/USD"]):
        with cols[idx]:
            snapshot = load_pair_signal_snapshot(pair)
            sig = snapshot["signal"]
            signal_value = str(sig.get("signal", "hold"))

            _render_signal_box(signal_value)
            st.markdown(f"### {pair}")
            st.markdown(f"**Current price:** ${snapshot['current_price']:,.2f}")

            daily_trend = str(sig.get("daily_trend", "neutral"))
            four_trend = str(sig.get("four_hour_trend", "neutral"))
            one_trend = str(sig.get("one_hour_trend", "neutral"))

            st.markdown(
                "**Trend:** "
                f"<span style='color:{_trend_color(daily_trend)};'>Daily {daily_trend}</span> | "
                f"<span style='color:{_trend_color(four_trend)};'>4h {four_trend}</span> | "
                f"<span style='color:{_trend_color(one_trend)};'>1h {one_trend}</span>",
                unsafe_allow_html=True,
            )

            daily_mom = str(sig.get("daily_momentum", "neutral"))
            four_mom = str(sig.get("four_hour_momentum", "neutral"))
            one_mom = str(sig.get("one_hour_momentum", "neutral"))

            st.markdown(
                "**Momentum:** "
                f"<span style='color:{_trend_color(daily_mom)};'>Daily {daily_mom}</span> | "
                f"<span style='color:{_trend_color(four_mom)};'>4h {four_mom}</span> | "
                f"<span style='color:{_trend_color(one_mom)};'>1h {one_mom}</span>",
                unsafe_allow_html=True,
            )

            if bool(sig.get("volume_confirmed", False)):
                st.markdown("✅ Volume confirmed")
            else:
                st.markdown("❌ Volume not confirmed")

            st.caption(f"Reason: {sig.get('reason', 'No reason available')}")
except Exception as exc:
    st.error(f"Signal Panel section failed: {exc}")

st.divider()

# Section 3 — Risk Utilization.
try:
    st.subheader("Risk Utilization")
    r1, r2, r3 = st.columns(3)

    today_realized_pnl = load_today_realized_pnl()
    open_trades_for_risk = load_open_trades_count()

    daily_loss_pct_used = max(0.0, (-today_realized_pnl / initial_capital) * 100)
    daily_limit_pct = max_daily_loss_pct * 100
    usage_ratio = (daily_loss_pct_used / daily_limit_pct) if daily_limit_pct > 0 else 0.0
    portfolio_value = initial_capital + today_realized_pnl

    with r1:
        st.metric(
            "Daily Loss % Used",
            f"{daily_loss_pct_used:.2f}%",
            delta=f"Limit {daily_limit_pct:.2f}%",
        )
        _render_custom_progress(usage_ratio)

    with r2:
        st.metric("Open Trades", f"{open_trades_for_risk}/{max_open_trades}")

    with r3:
        st.metric(
            "Portfolio Value",
            f"${portfolio_value:,.2f}",
            delta=f"{today_realized_pnl:+.2f}",
            delta_color="normal",
        )
except Exception as exc:
    st.error(f"Risk Utilization section failed: {exc}")

st.divider()

# Section 4 — Equity Curve.
try:
    st.subheader("Equity Curve")
    closed_df = load_closed_trades_for_equity()

    if closed_df.empty:
        st.info("No closed trades yet — equity curve will appear here once trades are completed.")
    else:
        closed_df["exit_time"] = pd.to_datetime(closed_df["exit_time"], utc=True, errors="coerce")
        closed_df["pnl"] = pd.to_numeric(closed_df["pnl"], errors="coerce").fillna(0.0)
        closed_df["equity"] = initial_capital + closed_df["pnl"].cumsum()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=closed_df["exit_time"],
                y=closed_df["equity"],
                mode="lines",
                fill="tozeroy",
                name="Equity",
                line=dict(width=2),
            )
        )
        fig.update_layout(
            title="Equity Curve",
            xaxis_title="Time",
            yaxis_title="Portfolio Value (USD)",
            height=420,
        )
        st.plotly_chart(fig, use_container_width=True)
except Exception as exc:
    st.error(f"Equity Curve section failed: {exc}")

st.divider()

# Section 5 — Price Chart.
try:
    st.subheader("Price Chart")
    p1, p2 = st.columns(2)
    with p1:
        selected_pair = st.selectbox("Pair", ["BTC/USD", "ETH/USD"], index=0)
    with p2:
        selected_timeframe = st.selectbox("Timeframe", ["1h", "4h", "1d"], index=1)

    chart_df = load_chart_data(selected_pair, selected_timeframe)
    if chart_df.empty:
        st.info(f"No {selected_pair} {selected_timeframe} candles available in the database.")
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
            go.Scatter(x=chart_df.index, y=chart_df["fast_ma"], mode="lines", name="Fast MA")
        )
        fig.add_trace(
            go.Scatter(x=chart_df.index, y=chart_df["slow_ma"], mode="lines", name="Slow MA")
        )
        fig.update_layout(
            title=f"{selected_pair} — {selected_timeframe} with Moving Averages",
            xaxis_title="Time",
            yaxis_title="Price",
            xaxis_rangeslider_visible=False,
            height=600,
        )
        st.plotly_chart(fig, use_container_width=True)
except Exception as exc:
    st.error(f"Price Chart section failed: {exc}")

st.divider()

# Section 6 — Trade History.
try:
    st.subheader("Trade History")
    trades_df = load_trade_history(limit=200)

    if trades_df.empty:
        st.info("No trades yet.")
    else:
        closed_only = trades_df[trades_df["status"] == "closed"].copy()
        total_realized = float(closed_only["pnl"].fillna(0.0).sum()) if not closed_only.empty else 0.0
        total_closed = int(len(closed_only))
        wins = int((closed_only["pnl"].fillna(0.0) > 0).sum()) if total_closed > 0 else 0
        win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        total_trades = int(len(trades_df))

        s1, s2, s3 = st.columns(3)
        with s1:
            st.metric(
                "Total Realized PnL",
                f"${total_realized:,.2f}",
                delta=f"{total_realized:+.2f}",
                delta_color="normal",
            )
        with s2:
            st.metric("Win Rate", f"{win_rate:.2f}%")
        with s3:
            st.metric("Total Trades", total_trades)

        styled = trades_df.style.map(
            lambda v: "color: #16a34a" if pd.notna(v) and v > 0 else "color: #dc2626" if pd.notna(v) and v < 0 else "",
            subset=["pnl"],
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(f"Trade History section failed: {exc}")

st.divider()

# Section 7 — Recent Decisions.
try:
    st.subheader("Recent Decisions")
    events_df = load_recent_events(limit=15)

    if events_df.empty:
        st.info("No system events yet.")
    else:
        events_df = events_df.copy()
        events_df["time"] = pd.to_datetime(events_df["timestamp"], utc=True, errors="coerce")
        # Convert to local timezone for readability.
        events_df["time"] = events_df["time"].dt.tz_convert(datetime.now().astimezone().tzinfo)
        events_df["time"] = events_df["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        events_df["event"] = events_df["event_type"].apply(_event_label)

        display_df = events_df[["time", "event", "message"]]
        st.dataframe(display_df, use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(f"Recent Decisions section failed: {exc}")
