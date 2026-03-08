"""Weekly performance review page for the Driftwood Streamlit app."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

# Ensure project-root imports work when launching from dashboard/pages/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config
from data.database import get_engine


def _event_label(event_type: str) -> str:
    """Map internal event types to readable labels/icons."""
    mapping = {
        "trade_opened": "✅ Trade Opened",
        "trade_closed": "🔒 Trade Closed",
        "trade_skipped": "⏭ Skipped",
        "error": "❌ Error",
    }
    return mapping.get(event_type, "ℹ️ Info")


def _week_bounds(selection: str) -> tuple[datetime, datetime]:
    """Return UTC week bounds (Monday 00:00 to next Monday 00:00)."""
    now_utc = datetime.now(timezone.utc)
    current_monday = (now_utc - timedelta(days=now_utc.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if selection == "Last week":
        start = current_monday - timedelta(days=7)
    else:
        start = current_monday
    end = start + timedelta(days=7)
    return start, end


@st.cache_data(ttl=300)
def load_week_trades(week_start: datetime, week_end: datetime) -> pd.DataFrame:
    """Load trades entered during the selected week."""
    engine = get_engine()
    query = text(
        """
        SELECT id, symbol, side, entry_price, exit_price, quantity, pnl, fees, status, mode, entry_time, exit_time, reason
        FROM trades
        WHERE entry_time >= :week_start AND entry_time < :week_end
        ORDER BY entry_time ASC
        """
    )
    return pd.read_sql(query, engine, params={"week_start": week_start, "week_end": week_end})


@st.cache_data(ttl=300)
def load_week_events(week_start: datetime, week_end: datetime) -> pd.DataFrame:
    """Load system events recorded during the selected week."""
    engine = get_engine()
    query = text(
        """
        SELECT id, event_type, message, timestamp, details
        FROM system_events
        WHERE timestamp >= :week_start AND timestamp < :week_end
        ORDER BY timestamp ASC
        """
    )
    return pd.read_sql(query, engine, params={"week_start": week_start, "week_end": week_end})


@st.cache_data(ttl=300)
def load_all_closed_trades() -> pd.DataFrame:
    """Load all closed trades for global equity history."""
    engine = get_engine()
    query = text(
        """
        SELECT id, symbol, pnl, exit_time
        FROM trades
        WHERE status = 'closed'
        ORDER BY exit_time ASC
        """
    )
    return pd.read_sql(query, engine)


def _compute_metrics(trades_df: pd.DataFrame, events_df: pd.DataFrame) -> dict:
    """Compute weekly review metrics from trades and events."""
    closed = trades_df[trades_df["status"] == "closed"].copy() if not trades_df.empty else pd.DataFrame()
    if not closed.empty:
        closed["pnl"] = pd.to_numeric(closed["pnl"], errors="coerce").fillna(0.0)

    signal_events = events_df[events_df["event_type"].isin(["trade_skipped", "trade_opened"])] if not events_df.empty else pd.DataFrame()
    error_events = events_df[events_df["event_type"] == "error"] if not events_df.empty else pd.DataFrame()
    halts = events_df[events_df["event_type"] == "trading_halted"] if not events_df.empty else pd.DataFrame()

    wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
    losses = int((closed["pnl"] < 0).sum()) if not closed.empty else 0
    closed_count = int(len(closed))
    total_pnl = float(closed["pnl"].sum()) if not closed.empty else 0.0
    max_single_trade_loss = float(closed["pnl"].min()) if not closed.empty else 0.0

    winning_sum = float(closed.loc[closed["pnl"] > 0, "pnl"].sum()) if not closed.empty else 0.0
    losing_sum = float(closed.loc[closed["pnl"] < 0, "pnl"].sum()) if not closed.empty else 0.0
    profit_factor = (winning_sum / abs(losing_sum)) if losing_sum < 0 else 0.0

    return {
        "total_signals": int(len(signal_events)),
        "trades_taken": int(len(trades_df)),
        "closed_trades": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (wins / closed_count * 100.0) if closed_count > 0 else 0.0,
        "total_pnl": total_pnl,
        "max_single_trade_loss": max_single_trade_loss,
        "profit_factor": profit_factor,
        "error_count": int(len(error_events)),
        "total_cycles": int(len(signal_events)),
        "any_halts": bool(len(halts) > 0),
    }


cfg = get_config()
_ = cfg  # Config loaded for parity with other pages and future expansion.

# Top controls: choose week window.
st.title("📊 Weekly Review")
selected_week = st.selectbox("Week", ["Last week", "Current week"], index=0)
week_start, week_end = _week_bounds(selected_week)
week_number = int(week_start.isocalendar().week)
st.caption(
    f"Week {week_number} | {week_start.strftime('%Y-%m-%d')} to {(week_end - timedelta(days=1)).strftime('%Y-%m-%d')} (UTC)"
)

try:
    trades_df = load_week_trades(week_start, week_end)
    events_df = load_week_events(week_start, week_end)
    all_closed_df = load_all_closed_trades()
    metrics = _compute_metrics(trades_df, events_df)
except Exception as exc:
    trades_df = pd.DataFrame()
    events_df = pd.DataFrame()
    all_closed_df = pd.DataFrame()
    metrics = _compute_metrics(pd.DataFrame(), pd.DataFrame())
    st.error(f"Failed to load weekly data: {exc}")

st.divider()

# Section 1 — Performance Metrics.
try:
    st.subheader("Performance Metrics")
    a1, a2, a3 = st.columns(3)
    with a1:
        st.metric("Total Signals Generated", metrics["total_signals"])
    with a2:
        st.metric("Trades Taken", metrics["trades_taken"])
    with a3:
        st.metric("Win Rate %", f"{metrics['win_rate_pct']:.2f}%")

    b1, b2, b3 = st.columns(3)
    with b1:
        st.metric("Total PnL", f"${metrics['total_pnl']:.2f}", delta=f"{metrics['total_pnl']:+.2f}")
    with b2:
        st.metric("Profit Factor", f"{metrics['profit_factor']:.2f}")
    with b3:
        st.metric("Max Single Trade Loss", f"${metrics['max_single_trade_loss']:.2f}")
except Exception as exc:
    st.error(f"Performance Metrics section failed: {exc}")

st.divider()

# Section 2 — Win/Loss breakdown.
try:
    st.subheader("Win/Loss Breakdown")
    w1, w2 = st.columns(2)
    with w1:
        st.markdown("### :green[Wins]")
        st.metric("Winning Trades", metrics["wins"])
    with w2:
        st.markdown("### :red[Losses]")
        st.metric("Losing Trades", metrics["losses"])
except Exception as exc:
    st.error(f"Win/Loss Breakdown section failed: {exc}")

st.divider()

# Section 3 — Equity Curve for the week.
try:
    st.subheader("Equity Curve For The Week")
    if trades_df.empty:
        st.info("No closed trades in this week — equity curve will appear here once trades are completed.")
    else:
        weekly_closed = trades_df[trades_df["status"] == "closed"].copy()
        if weekly_closed.empty:
            st.info("No closed trades in this week — equity curve will appear here once trades are completed.")
        else:
            weekly_closed["exit_time"] = pd.to_datetime(weekly_closed["exit_time"], utc=True, errors="coerce")
            weekly_closed["pnl"] = pd.to_numeric(weekly_closed["pnl"], errors="coerce").fillna(0.0)
            weekly_closed = weekly_closed.sort_values("exit_time")
            weekly_closed["cumulative_pnl"] = weekly_closed["pnl"].cumsum()

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=weekly_closed["exit_time"],
                    y=weekly_closed["cumulative_pnl"],
                    mode="lines",
                    fill="tozeroy",
                    name="Weekly Cumulative PnL",
                    line=dict(width=2),
                )
            )
            fig.update_layout(
                title="Equity Curve",
                xaxis_title="Time (UTC)",
                yaxis_title="Cumulative PnL (USD)",
                height=420,
            )
            st.plotly_chart(fig, use_container_width=True)
except Exception as exc:
    st.error(f"Equity Curve section failed: {exc}")

st.divider()

# Section 4 — System Health.
try:
    st.subheader("System Health")
    h1, h2, h3 = st.columns(3)
    with h1:
        st.metric("Total Scheduler Cycles", metrics["total_cycles"])
    with h2:
        st.metric("Errors Logged", metrics["error_count"])
    with h3:
        st.metric("Trading Halted This Week", "Yes" if metrics["any_halts"] else "No")
except Exception as exc:
    st.error(f"System Health section failed: {exc}")

st.divider()

# Section 5 — Market Conditions (manual notes).
try:
    st.subheader("Market Conditions")
    st.caption(
        "The fields below reflect manual observations — update them in your weekly notes"
    )
    st.text_input("BTC daily trend this week")
    st.text_input("ETH daily trend this week")
    st.text_input("Overall market regime")
except Exception as exc:
    st.error(f"Market Conditions section failed: {exc}")

st.divider()

# Section 6 — Trade detail table.
try:
    st.subheader("Trade Detail Table")
    if trades_df.empty:
        st.info("No trades for the selected week.")
    else:
        styled = trades_df.style.map(
            lambda v: "color: #16a34a"
            if pd.notna(v) and float(v) > 0
            else "color: #dc2626"
            if pd.notna(v) and float(v) < 0
            else "",
            subset=["pnl"],
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(f"Trade Detail Table section failed: {exc}")

st.divider()

# Section 7 — Event log for the week.
try:
    st.subheader("Event Log For The Week")
    if events_df.empty:
        st.info("No system events for the selected week.")
    else:
        display = events_df.copy()
        display["time"] = pd.to_datetime(display["timestamp"], utc=True, errors="coerce")
        display["event"] = display["event_type"].apply(_event_label)
        display["time"] = display["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(display[["time", "event", "message"]], use_container_width=True, hide_index=True)
except Exception as exc:
    st.error(f"Event Log section failed: {exc}")
