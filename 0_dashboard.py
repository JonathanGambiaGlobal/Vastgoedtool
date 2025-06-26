import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import requests
import pydeck as pdk
from utils import (
    geocode,
    beoordeel_perceel_modulair,
    get_exchange_rate_eur_to_gmd,
    get_exchange_rate_volatility,
    hoofdsteden_df
)

# --- Percelen laden (vervang indien nodig door load_percelen_from_json) ---
if "percelen" not in st.session_state:
    st.session_state["percelen"] = []

# --- Titel & Koersen ---
st.title("Vastgoeddashboard – Gambia")
wisselkoers = get_exchange_rate_eur_to_gmd()
volatiliteit_pct = get_exchange_rate_volatility()

col1, col2 = st.columns(2)
with col1:
    if wisselkoers:
        st.metric("💶 Live wisselkoers (EUR → GMD)", f"{wisselkoers:.2f}")
    else:
        st.warning("Wisselkoers niet beschikbaar.")
with col2:
    if volatiliteit_pct is not None:
        label = "🟢 Laag" if volatiliteit_pct < 1 else ("🟡 Gemiddeld" if volatiliteit_pct < 2 else "🔴 Hoog")
        st.metric("📉 Wisselkoersvolatiliteit (30 dagen)", f"{volatiliteit_pct}%", label)
    else:
        st.warning("Geen historische wisselkoersdata beschikbaar.")

st.markdown("---")


