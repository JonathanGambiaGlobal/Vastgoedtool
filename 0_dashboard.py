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

import streamlit as st
import pandas as pd
from utils import get_exchange_rate_eur_to_gmd
from datetime import datetime


def analyse_portfolio_perceel(perceel: dict, groei_pct: float, horizon_jaren: int, exchange_rate: float) -> dict:
    def safe_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    aankoopprijs = safe_float(perceel.get("aankoopprijs"))
    aankoopdatum = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce")
    investeerders = perceel.get("investeerders", [])
    totaal_extern = sum(safe_float(i.get("bedrag")) for i in investeerders)
    eigen_inleg = aankoopprijs - totaal_extern

    if eigen_inleg > 0:
        investeerders.append({
            "naam": "Eigen beheer",
            "bedrag": eigen_inleg,
            "rente": 0,
            "rentetype": "bij verkoop",
            "winstdeling": 1.0
        })

    if not pd.notnull(aankoopdatum) or aankoopprijs <= 0:
        return None

    verkoopprijs = safe_float(perceel.get("verkoopprijs"))
    if verkoopprijs > 0:
        verkoopwaarde = verkoopprijs
    else:
        verkoopwaarde = aankoopprijs * ((1 + groei_pct / 100) ** horizon_jaren)

    vandaag = datetime.today()
    maanden = max((vandaag.year - aankoopdatum.year) * 12 + (vandaag.month - aankoopdatum.month), 1)
    jaren = maanden / 12

    totaal_inleg = 0
    totaal_rente = 0
    investeerder_resultaten = []

    for inv in investeerders:
        bedrag = safe_float(inv.get("bedrag"))
        rente = safe_float(inv.get("rente"))
        winstdeling_pct = safe_float(inv.get("winstdeling"))
        rentetype = inv.get("rentetype", "maandelijks").lower()

        if rentetype == "maandelijks":
            rente_opbouw = bedrag * ((1 + rente / 12) ** maanden - 1)
        elif rentetype == "jaarlijks":
            rente_opbouw = bedrag * ((1 + rente) ** jaren - 1)
        elif rentetype == "bij verkoop":
            rente_opbouw = bedrag * rente
        else:
            rente_opbouw = 0

        totaal_inleg += bedrag
        totaal_rente += rente_opbouw

        investeerder_resultaten.append({
            "naam": inv.get("naam"),
            "inleg": round(bedrag, 2),
            "rente": round(rente_opbouw, 2),
            "kapitaalkosten": round(bedrag + rente_opbouw, 2),
            "kapitaalkosten_eur": round((bedrag + rente_opbouw) / exchange_rate, 2) if exchange_rate else None,
            "rentetype": rentetype,
            "winstdeling_pct": winstdeling_pct
        })

    netto_winst = verkoopwaarde - totaal_inleg - totaal_rente
    waardestijging = max(0, verkoopwaarde - aankoopprijs)

    for result in investeerder_resultaten:
        winstdeling_pct = result.get("winstdeling_pct", 0)
        winst_aandeel = waardestijging * winstdeling_pct
        result["winstdeling"] = round(winst_aandeel, 2)
        result["winst_eur"] = round(winst_aandeel / exchange_rate, 2) if exchange_rate else None

    return {
        "locatie": perceel.get("locatie"),
        "verkoopwaarde": round(verkoopwaarde, 2),
        "verkoopwaarde_eur": round(verkoopwaarde / exchange_rate, 2) if exchange_rate else None,
        "totaal_inleg": round(totaal_inleg, 2),
        "totaal_rente": round(totaal_rente, 2),
        "netto_winst": round(netto_winst, 2),
        "netto_winst_eur": round(netto_winst / exchange_rate, 2) if exchange_rate else None,
        "investeerders": investeerder_resultaten
    }

def analyse_verkocht_perceel(perceel: dict, exchange_rate: float) -> dict:
    def safe_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    aankoopprijs = safe_float(perceel.get("aankoopprijs"))
    verkoopwaarde = safe_float(perceel.get("verkoopprijs"))
    aankoopdatum = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce")
    verkoopdatum = pd.to_datetime(perceel.get("verkoopdatum"), errors="coerce")

    if not pd.notnull(aankoopdatum) or aankoopprijs <= 0 or verkoopwaarde <= 0:
        return None

    maanden = max((verkoopdatum.year - aankoopdatum.year) * 12 + (verkoopdatum.month - aankoopdatum.month), 1)
    jaren = maanden / 12

    investeerders = perceel.get("investeerders", [])
    totaal_inleg = 0
    totaal_rente = 0
    investeerder_resultaten = []

    for inv in investeerders:
        bedrag = safe_float(inv.get("bedrag"))
        rente = safe_float(inv.get("rente"))
        winstdeling_pct = safe_float(inv.get("winstdeling"))
        rentetype = inv.get("rentetype", "maandelijks").lower()

        if rentetype == "maandelijks":
            rente_opbouw = bedrag * ((1 + rente / 12) ** maanden - 1)
        elif rentetype == "jaarlijks":
            rente_opbouw = bedrag * ((1 + rente) ** jaren - 1)
        elif rentetype == "bij verkoop":
            rente_opbouw = bedrag * rente
        else:
            rente_opbouw = 0

        totaal_inleg += bedrag
        totaal_rente += rente_opbouw

        investeerder_resultaten.append({
            "naam": inv.get("naam"),
            "inleg": round(bedrag, 2),
            "rente": round(rente_opbouw, 2),
            "kapitaalkosten": round(bedrag + rente_opbouw, 2),
            "kapitaalkosten_eur": round((bedrag + rente_opbouw) / exchange_rate, 2) if exchange_rate else None,
            "rentetype": rentetype,
            "winstdeling_pct": winstdeling_pct
        })

    netto_winst = verkoopwaarde - totaal_inleg - totaal_rente
    waardestijging = max(0, verkoopwaarde - aankoopprijs)

    for result in investeerder_resultaten:
        winstdeling_pct = result.get("winstdeling_pct", 0)
        winst_aandeel = waardestijging * winstdeling_pct
        result["winstdeling"] = round(winst_aandeel, 2)
        result["winst_eur"] = round(winst_aandeel / exchange_rate, 2) if exchange_rate else None

    return {
        "locatie": perceel.get("locatie"),
        "verkoopwaarde": round(verkoopwaarde, 2),
        "verkoopwaarde_eur": round(verkoopwaarde / exchange_rate, 2) if exchange_rate else None,
        "totaal_inleg": round(totaal_inleg, 2),
        "totaal_rente": round(totaal_rente, 2),
        "netto_winst": round(netto_winst, 2),
        "netto_winst_eur": round(netto_winst / exchange_rate, 2) if exchange_rate else None,
        "investeerders": investeerder_resultaten
    }

# Interface
st.subheader("📈 Analyse verkochte percelen")
exchange_rate = get_exchange_rate_eur_to_gmd()
if not exchange_rate:
    st.warning("Wisselkoers niet beschikbaar. Euro-waardes worden niet getoond.")

if "percelen" not in st.session_state or not st.session_state["percelen"]:
    st.warning("Er zijn nog geen percelen beschikbaar in het geheugen.")
    st.stop()

percelen = st.session_state["percelen"]
df = pd.DataFrame(percelen)
df.columns = df.columns.str.lower()
verkochte_df = df[df["dealstage"].str.lower() == "verkocht"]

resultaten = []

if verkochte_df.empty:
    st.info("Er zijn nog geen verkochte percelen. Hieronder volgt een prognose van actieve percelen.")
    portfolio_df = df[df["dealstage"].str.lower().isin(["in portfolio", "in planning"])]
    if not portfolio_df.empty:
        groei_pct = st.number_input("Verwachte jaarlijkse waardestijging (%)", min_value=0.0, max_value=100.0, value=5.0)
        horizon = st.slider("Prognoseperiode (jaren)", 1, 15, 5)
        for _, perceel in portfolio_df.iterrows():
            analyse = analyse_portfolio_perceel(perceel, groei_pct, horizon, exchange_rate)
            if analyse:
                resultaten.append(analyse)
else:
    for _, perceel in verkochte_df.iterrows():
        analyse = analyse_verkocht_perceel(perceel, exchange_rate)
        if analyse:
            resultaten.append(analyse)

if resultaten:
    df_result = pd.DataFrame(resultaten)
    if not verkochte_df.empty:
        st.subheader("📊 Resultaten verkochte percelen")
    else:
        st.subheader("📊 Prognose actieve percelen")

    st.dataframe(df_result[[
        "locatie", "verkoopwaarde", "totaal_inleg", "totaal_rente", "netto_winst",
        "verkoopwaarde_eur", "netto_winst_eur"
    ]].sort_values(by="netto_winst", ascending=False))

    if not verkochte_df.empty:
        for _, perceel in verkochte_df.iterrows():
            locatie = perceel.get("locatie", "Onbekend")
            st.markdown(f"#### 👥 Investeerders: {locatie}")
            analyse = analyse_verkocht_perceel(perceel, exchange_rate)
            if analyse:
                for inv in analyse["investeerders"]:
                    totaal = inv.get("kapitaalkosten", 0) + inv.get("winstdeling", 0)
                    totaal_eur = inv.get("kapitaalkosten_eur", 0) + inv.get("winst_eur", 0)
                    inleg_eur = inv.get("inleg_eur", 0)
                    winst_eur = totaal_eur - inleg_eur
                    rendement_pct = (winst_eur / inleg_eur * 100) if inleg_eur else 0
                    st.write(f"- {inv['naam']}: kapitaalkosten GMD {inv['kapitaalkosten']:,}, "
                             f"winstdeling GMD {inv.get('winstdeling', 0):,.2f} "
                             f"({inv.get('winstdeling_pct', 0)*100:.0f}%), totaal: GMD {totaal:,.2f} "
                             f"(EUR {totaal_eur:,.2f}), netto winst: EUR {winst_eur:,.2f} "
                             f"({rendement_pct:.1f}%)")


