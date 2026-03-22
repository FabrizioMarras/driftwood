"""Backtest results page for the Driftwood Streamlit app."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Ensure project-root imports work when launching from dashboard/pages/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@st.cache_data(ttl=300)
def load_backtest_results() -> dict | None:
    """Load backtest JSON output from disk, with short caching."""
    try:
        path = PROJECT_ROOT / "data" / "backtest_results.json"
        if not path.exists():
            return None
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


# Load data once for this page render.
results_payload = load_backtest_results()

# Header and early-stop guard.
st.title("🔬 Backtest Results")
if results_payload is None:
    st.warning("No backtest results found. Run python3 backtest.py to generate results.")
    st.stop()

try:
    cfg = results_payload.get("config", {})
    pair_results = results_payload.get("results", [])
    generated_at = str(results_payload.get("generated_at", "Unknown"))
    generated_display = generated_at
    try:
        generated_display = (
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%d %H:%M:%S UTC")
        )
    except Exception:
        pass

    st.caption(
        f"Generated: {generated_display} | "
        f"Period: {cfg.get('start_date')} to {cfg.get('end_date')} | "
        f"Initial capital: ${float(cfg.get('initial_capital', 0.0)):,.2f} | "
        f"Pairs: {len(pair_results)}"
    )
except Exception as exc:
    st.warning(f"Failed to render header metadata: {exc}")

st.divider()

# Section 1: Combined summary metrics.
try:
    st.subheader("Combined Summary")
    pair_results = results_payload.get("results", [])

    total_trades = int(sum(float(item.get("total_trades", 0)) for item in pair_results))
    combined_pnl = float(sum(float(item.get("total_pnl_usd", 0.0)) for item in pair_results))

    initial_capital = float(cfg.get("initial_capital", 0.0))
    combined_initial = initial_capital * len(pair_results)
    combined_pnl_pct = (combined_pnl / combined_initial * 100.0) if combined_initial else 0.0

    best_pair = "N/A"
    if pair_results:
        best = max(pair_results, key=lambda x: float(x.get("total_pnl_usd", 0.0)))
        best_pair = str(best.get("symbol", "N/A"))

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Trades", f"{total_trades}")
    c2.metric(
        "Combined PnL (USD)",
        f"${combined_pnl:,.2f}",
        delta=f"{combined_pnl_pct:+.2f}%",
        delta_color="normal",
    )
    c3.metric("Best Performing Pair", best_pair)

    st.divider()

    pair_cfg_map = results_payload.get("pair_configs", {})
    if pair_cfg_map:
        cfg_cols = st.columns(len(pair_cfg_map))
        for col, (pair_name, pair_cfg) in zip(cfg_cols, pair_cfg_map.items()):
            col.markdown(
                f"**{pair_name}** - "
                f"SL: {float(pair_cfg.get('stop_loss_pct', 0.0)) * 100:.0f}% | "
                f"TP: {float(pair_cfg.get('take_profit_pct', 0.0)) * 100:.0f}% | "
                f"Pos: {float(pair_cfg.get('position_size_pct', 0.0)) * 100:.0f}%"
            )
except Exception as exc:
    st.warning(f"Failed to render combined summary: {exc}")

st.divider()

# Section 2: Per-pair tabs and charts.
try:
    st.subheader("Per Pair Results")
    pair_results = results_payload.get("results", [])

    if not pair_results:
        st.info("No pair results available in backtest_results.json.")
    else:
        tab_labels = [str(item.get("symbol", "Unknown")) for item in pair_results]
        tabs = st.tabs(tab_labels)

        for tab, item in zip(tabs, pair_results):
            with tab:
                symbol = str(item.get("symbol", "Unknown"))
                total_pnl_usd = float(item.get("total_pnl_usd", 0.0))
                final_value = float(item.get("final_portfolio_value", 0.0))
                initial_value = float(cfg.get("initial_capital", 0.0))
                pair_cfg = item.get("pair_config", {})

                st.markdown(
                    f"**Strategy:** SL {float(pair_cfg.get('stop_loss_pct', 0)) * 100:.1f}% | "
                    f"TP {float(pair_cfg.get('take_profit_pct', 0)) * 100:.1f}% | "
                    f"Position {float(pair_cfg.get('position_size_pct', 0)) * 100:.1f}%"
                )

                # Row 1 metrics.
                r1c1, r1c2, r1c3, r1c4 = st.columns(4)
                r1c1.metric("Total Trades", f"{int(item.get('total_trades', 0))}")
                r1c2.metric("Win Rate %", f"{float(item.get('win_rate_pct', 0.0)):.2f}%")
                profit_factor = float(item.get("profit_factor", 0.0))
                pf_color = "#dc2626"
                if profit_factor >= 1.2:
                    pf_color = "#16a34a"
                elif profit_factor >= 1.0:
                    pf_color = "#f59e0b"
                r1c3.markdown(
                    "Profit Factor  \n"
                    f"<span style='color:{pf_color};font-weight:700;font-size:1.1rem;'>"
                    f"{profit_factor:.2f}</span>",
                    unsafe_allow_html=True,
                )
                r1c4.metric(
                    "Max Drawdown %", f"{float(item.get('max_drawdown_pct', 0.0)):.2f}%"
                )

                # Row 2 metrics.
                r2c1, r2c2, r2c3, r2c4 = st.columns(4)
                r2c1.metric(
                    "Total PnL (USD)",
                    f"${total_pnl_usd:,.2f}",
                    delta=f"{float(item.get('total_pnl_pct', 0.0)):+.2f}%",
                    delta_color="normal",
                )
                r2c2.metric("Final Portfolio", f"${final_value:,.2f}")
                r2c3.metric("Avg Win", f"${float(item.get('avg_win_usd', 0.0)):,.2f}")
                r2c4.metric("Avg Loss", f"${float(item.get('avg_loss_usd', 0.0)):,.2f}")

                # Row 3 metrics.
                r3c1, r3c2 = st.columns(2)
                r3c1.metric(
                    "Max Single Gain",
                    f"${float(item.get('max_single_gain_usd', 0.0)):,.2f}",
                )
                r3c2.metric(
                    "Max Single Loss",
                    f"${float(item.get('max_single_loss_usd', 0.0)):,.2f}",
                )

                # Build trade-level equity curve from backtest result trades.
                trades = item.get("trades", [])
                line_color = "#16a34a" if final_value >= initial_value else "#dc2626"
                equity_points = [initial_value] + [
                    float(t.get("portfolio_value_after_trade", initial_value)) for t in trades
                ]
                trade_numbers = list(range(len(equity_points)))
                curve_df = pd.DataFrame(
                    {"trade_number": trade_numbers, "portfolio_value": equity_points}
                )

                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=curve_df["trade_number"],
                        y=curve_df["portfolio_value"],
                        mode="lines+markers",
                        fill="tozeroy",
                        line={"color": line_color, "width": 3},
                        marker={"size": 8},
                        name=symbol,
                        hovertemplate=(
                            "Trade #: %{x}<br>"
                            "Portfolio: $%{y:,.2f}<br>"
                            f"Total PnL: ${total_pnl_usd:,.2f}<extra></extra>"
                        ),
                    )
                )
                fig.update_layout(
                    title=f"{symbol} Equity Curve",
                    xaxis_title="Trade Number",
                    yaxis_title="Portfolio Value (USD)",
                    margin={"l": 20, "r": 20, "t": 50, "b": 20},
                    height=360,
                )
                st.plotly_chart(fig, use_container_width=True)

                # Exit reason breakdown from trade-level data.
                stop_losses = sum(1 for t in trades if t.get("exit_reason") == "stop_loss")
                take_profits = sum(1 for t in trades if t.get("exit_reason") == "take_profit")
                reversals = sum(1 for t in trades if t.get("exit_reason") == "trend_reversal")

                e1, e2, e3 = st.columns(3)
                e1.metric("🛑 Stop Loss", f"{stop_losses}")
                e2.metric("🎯 Take Profit", f"{take_profits}")
                e3.metric("🔄 Trend Reversal", f"{reversals}")

                # Trade detail table for the selected pair.
                if trades:
                    trade_df = pd.DataFrame(trades)
                    display_cols = [
                        "entry_time",
                        "exit_time",
                        "entry_price",
                        "exit_price",
                        "pnl",
                        "fees",
                        "exit_reason",
                    ]
                    trade_df = trade_df.reindex(columns=display_cols)

                    if "entry_price" in trade_df.columns:
                        trade_df["entry_price"] = pd.to_numeric(
                            trade_df["entry_price"], errors="coerce"
                        ).round(2)
                    if "exit_price" in trade_df.columns:
                        trade_df["exit_price"] = pd.to_numeric(
                            trade_df["exit_price"], errors="coerce"
                        ).round(2)

                    def _pnl_color(v: object) -> str:
                        val = pd.to_numeric(v, errors="coerce")
                        if pd.isna(val):
                            return ""
                        return "color: #16a34a" if float(val) > 0 else "color: #dc2626"

                    st.dataframe(
                        trade_df.style.map(_pnl_color, subset=["pnl"]),
                        use_container_width=True,
                    )
                else:
                    st.info("No trades for this pair.")
except Exception as exc:
    st.warning(f"Failed to render per-pair section: {exc}")

st.divider()

# Section 3: Backtest configuration details.
try:
    st.subheader("Backtest Config Used")
    with st.expander("Backtest Configuration"):
        cfg = results_payload.get("config", {})
        st.markdown("**Shared config**")
        st.write(f"start_date: {cfg.get('start_date', 'N/A')}")
        st.write(f"end_date: {cfg.get('end_date', 'N/A')}")
        st.write(f"initial_capital: {cfg.get('initial_capital', 'N/A')}")
        st.write(f"fee_rate: {cfg.get('fee_rate', 'N/A')}")
        st.write(f"slippage: {cfg.get('slippage', 'N/A')}")
        st.write(f"max_open_trades: {cfg.get('max_open_trades', 'N/A')}")

        st.markdown("**Per-pair config**")
        pair_cfg_map = results_payload.get("pair_configs", {})
        if pair_cfg_map:
            pair_cfg_df = pd.DataFrame.from_dict(pair_cfg_map, orient="index")[
                ["stop_loss_pct", "take_profit_pct", "position_size_pct"]
            ]
            st.dataframe(
                pair_cfg_df.style.format(
                    {
                        "stop_loss_pct": "{:.2%}",
                        "take_profit_pct": "{:.2%}",
                        "position_size_pct": "{:.2%}",
                    }
                ),
                use_container_width=True,
            )
        else:
            st.info("No per-pair config found in results JSON.")
except Exception as exc:
    st.warning(f"Failed to render backtest config: {exc}")
