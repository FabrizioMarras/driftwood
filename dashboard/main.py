"""Main entry page for the Driftwood multi-page Streamlit app."""

from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

# Ensure project-root imports work when launching from dashboard/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import is_paper_trading


# Configure the Streamlit page.
st.set_page_config(page_title="Driftwood Trading System", page_icon="🌊", layout="wide")

# Resolve mode and badge color.
paper_mode = is_paper_trading()
mode_text = "Paper Mode" if paper_mode else "Live Mode"
mode_color = "#16a34a" if paper_mode else "#dc2626"

# Home screen header.
st.title("🌊 Driftwood Trading System")
st.subheader(
    "Automated trend-following trading system for BTC/USD and ETH/USD on Kraken"
)

# Visual mode indicator.
st.markdown(
    f"<span style='background:{mode_color};color:white;padding:0.35rem 0.7rem;border-radius:0.55rem;font-weight:600;'>{mode_text}</span>",
    unsafe_allow_html=True,
)

st.write("Use the sidebar to navigate between sections.")

# Quick overview of app pages.
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(
        "**🏠 Dashboard** — Live market signals, risk utilization, trade history and price charts"
    )

with col2:
    st.markdown(
        "**📊 Weekly Review** — Auto-generated weekly performance report with key metrics"
    )

with col3:
    st.markdown("**⚙️ Settings** — coming soon")
