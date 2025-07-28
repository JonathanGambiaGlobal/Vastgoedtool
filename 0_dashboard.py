import streamlit as st

st.image("QG.png", width=180)

import pandas as pd
import numpy as np
from datetime import date, timedelta
import requests
import pydeck as pdk
from datetime import datetime
from dateutil.relativedelta import relativedelta
from utils import (
    geocode,
    beoordeel_perceel_modulair,
    get_exchange_rate_eur_to_gmd,
    get_exchange_rate_volatility,
    hoofdsteden_df,
    format_currency
)
from auth import login_check
login_check()

from utils import load_percelen_from_json

if "percelen" not in st.session_state or not st.session_state["percelen"]:
    st.session_state["percelen"] = load_percelen_from_json()


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

# 📊 DASHBOARD KERNCIJFERS
st.markdown("## 📊 Kerngegevens vastgoedportfolio")

percelen = st.session_state["percelen"]
aantal_percelen = len(percelen)
totaal_m2 = sum(
    p.get("lengte", 0) * p.get("breedte", 0)
    for p in percelen if isinstance(p, dict)
)

col1, col2 = st.columns(2)

with col1:
    st.metric("📍 Aantal percelen", aantal_percelen)

with col2:
    st.metric("📐 Totale oppervlakte", f"{totaal_m2:,.0f} m²")



st.markdown("## 📅 Komende betalingen & opgebouwde rente")

betalingen = []
vandaag = date.today()

for perceel in percelen:
    locatie = perceel.get("locatie", "Onbekend")
    aankoopdatum_str = perceel.get("aankoopdatum", "")
    try:
        aankoopdatum = datetime.strptime(aankoopdatum_str, "%Y-%m-%d").date()
    except:
        aankoopdatum = vandaag

    for inv in perceel.get("investeerders", []):
        naam = inv.get("naam", "Investeerder")
        bedrag = inv.get("bedrag_eur", 0.0)
        rente = inv.get("rente", 0.0)
        rentetype = inv.get("rentetype", "bij verkoop")

        if rente > 0 and rentetype in ["maandelijks", "jaarlijks"]:
            if rentetype == "maandelijks":
                maanden = (vandaag.year - aankoopdatum.year) * 12 + (vandaag.month - aankoopdatum.month)
                opgebouwde_rente = bedrag * (rente / 12) * maanden
                volgende_betaling = aankoopdatum + relativedelta(months=+maanden + 1)
                volgende_bedrag = bedrag * rente / 12
            elif rentetype == "jaarlijks":
                jaren = max(vandaag.year - aankoopdatum.year, 0)
                opgebouwde_rente = bedrag * rente * jaren
                volgende_betaling = aankoopdatum + relativedelta(years=+jaren + 1)
                volgende_bedrag = bedrag * rente
            else:
                opgebouwde_rente = 0
                volgende_betaling = "n.v.t."
                volgende_bedrag = 0

            betalingen.append({
                "Perceel": locatie,
                "Investeerder": naam,
                "Rentetype": rentetype,
                "Startdatum": aankoopdatum.strftime("%d-%m-%Y"),
                "Volgende betaling": volgende_betaling.strftime("%d-%m-%Y") if isinstance(volgende_betaling, date) else "n.v.t.",
                "Bedrag volgende betaling (€)": round(volgende_bedrag, 2),
                "Opgebouwde rente tot nu (€)": round(opgebouwde_rente, 2)
            })

if betalingen:
    df_betalingen = pd.DataFrame(betalingen)
    df_betalingen.reset_index(drop=True, inplace=True)
    st.dataframe(df_betalingen, use_container_width=True)
else:
    st.info("Geen rentebetalingen gepland.")

def analyse_portfolio_perceel(perceel: dict, groei_pct: float, horizon_jaren: int, exchange_rate: float) -> dict:
    def safe_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    aankoopprijs = safe_float(perceel.get("aankoopprijs"))
    aankoopdatum = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce")
    investeerders = perceel.get("investeerders", [])
    if not isinstance(investeerders, list):
        investeerders = []

    totaal_extern = sum(safe_float(i.get("bedrag")) for i in investeerders if isinstance(i, dict))
    eigen_inleg = aankoopprijs - totaal_extern

    if eigen_inleg > 0:
        investeerders.append({
            "naam": "Eigen beheer",
            "bedrag": eigen_inleg,
            "rente": 0,
            "rentetype": "bij verkoop",
            "winstdeling": 1.0
        })

    # Geen geldige analyse mogelijk zonder aankoopprijs of datum
    if not pd.notnull(aankoopdatum) or aankoopprijs <= 0:
        return None

    # Verkoopprijs meenemen (handmatig of prognose)
    verkoopprijs_gmd = safe_float(perceel.get("verkoopprijs"))
    verkoopprijs_eur = safe_float(perceel.get("verkoopprijs_eur"))

    if verkoopprijs_gmd > 0:
        verkoopwaarde = verkoopprijs_gmd
    else:
        verkoopwaarde = aankoopprijs * ((1 + groei_pct / 100) ** horizon_jaren)

    if verkoopprijs_eur <= 0 and exchange_rate:
        verkoopprijs_eur = round(verkoopwaarde / exchange_rate, 2)

    vandaag = pd.Timestamp.today()
    maanden = max((vandaag.year - aankoopdatum.year) * 12 + (vandaag.month - aankoopdatum.month), 1)
    jaren = maanden / 12

    totaal_inleg = 0
    totaal_rente = 0
    investeerder_resultaten = []

    for inv in investeerders:
        if isinstance(inv, dict):
            bedrag = safe_float(inv.get("bedrag"))
            rente = safe_float(inv.get("rente"))
            winstdeling_pct = safe_float(inv.get("winstdeling"))
            rentetype = inv.get("rentetype", "maandelijks").lower()
            naam = inv.get("naam", "Investeerder")
        else:
            bedrag = 0.0
            rente = 0.0
            winstdeling_pct = 0.0
            rentetype = "bij verkoop"
            naam = str(inv)

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
            "naam": naam,
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
        "verkoopprijs": round(verkoopwaarde, 2),
        "verkoopprijs_eur": round(verkoopprijs_eur, 2) if verkoopprijs_eur else None,
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
    verkoopprijs_gmd = safe_float(perceel.get("verkoopprijs"))
    verkoopprijs_eur = safe_float(perceel.get("verkoopprijs_eur"))
    aankoopdatum = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce")
    verkoopdatum = pd.to_datetime(perceel.get("verkoopdatum"), errors="coerce")
    investeerders = perceel.get("investeerders", [])
    if not isinstance(investeerders, list):
        investeerders = []

    # Wisselkoers fallback
    if verkoopprijs_eur <= 0 and exchange_rate:
        verkoopprijs_eur = round(verkoopprijs_gmd / exchange_rate, 2)

    # Bepaal looptijd voor rente
    maanden = 0
    jaren = 0
    if pd.notnull(aankoopdatum) and pd.notnull(verkoopdatum):
        maanden = max((verkoopdatum.year - aankoopdatum.year) * 12 + (verkoopdatum.month - aankoopdatum.month), 1)
        jaren = maanden / 12

    # Start met aankoopprijs als inleg (eigen beheer)
    totaal_inleg = aankoopprijs
    totaal_rente = 0
    investeerder_resultaten = []

    # Rente + winstdeling voor externe investeerders
    for inv in investeerders:
        if isinstance(inv, dict):
            bedrag = safe_float(inv.get("bedrag"))
            rente = safe_float(inv.get("rente"))
            rentetype = inv.get("rentetype", "bij verkoop").lower()
            winstdeling_pct = safe_float(inv.get("winstdeling"))
            naam = inv.get("naam", "Investeerder")

            # Rente-opbouw berekenen
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
                "naam": naam,
                "inleg": round(bedrag, 2),
                "rente": round(rente_opbouw, 2),
                "kapitaalkosten": round(bedrag + rente_opbouw, 2),
                "kapitaalkosten_eur": round((bedrag + rente_opbouw) / exchange_rate, 2) if exchange_rate else None,
                "rentetype": rentetype,
                "winstdeling_pct": winstdeling_pct
            })

    # Netto winst berekenen
    netto_winst = verkoopprijs_gmd - totaal_inleg - totaal_rente
    netto_winst_eur = round(netto_winst / exchange_rate, 2) if exchange_rate else None

    # Winstdeling verdelen
    waardestijging = max(0, verkoopprijs_gmd - aankoopprijs)
    for result in investeerder_resultaten:
        winstdeling_pct = result.get("winstdeling_pct", 0)
        winst_aandeel = waardestijging * winstdeling_pct
        result["winstdeling"] = round(winst_aandeel, 2)
        result["winst_eur"] = round(winst_aandeel / exchange_rate, 2) if exchange_rate else None

    return {
        "locatie": perceel.get("locatie"),
        "verkoopprijs": round(verkoopprijs_gmd, 2),
        "verkoopprijs_eur": round(verkoopprijs_eur, 2) if verkoopprijs_eur else None,
        "verkoopwaarde": round(verkoopprijs_gmd, 2),
        "verkoopwaarde_eur": round(verkoopprijs_eur, 2) if verkoopprijs_eur else None,
        "totaal_inleg": round(totaal_inleg, 2),
        "totaal_rente": round(totaal_rente, 2),
        "netto_winst": round(netto_winst, 2),
        "netto_winst_eur": netto_winst_eur,
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

if "verkoopprijs" not in df.columns:
    df["verkoopprijs"] = 0
if "verkoopprijs_eur" not in df.columns:
    df["verkoopprijs_eur"] = 0.0

# 🎯 Strategieanalyse: verrijk de DataFrame met planning en opbrengstvelden
if "doorlooptijd" not in df.columns:
    df["doorlooptijd"] = pd.NaT  # kolom aanmaken indien ontbreekt

# Converteer naar datetime
df["doorlooptijd"] = pd.to_datetime(df["doorlooptijd"], errors="coerce")

# 🔎 Debug: toon unieke waarden om te zien waarom sommige geen datetime zijn
st.write("DEBUG - unieke waarden in doorlooptijd:", df["doorlooptijd"].unique())

# ✅ Zorg dat de kolommen voor opbrengst, kosten en winst altijd bestaan
for col in ["verwachte_opbrengst_eur", "verwachte_kosten_eur", "verwachte_winst_eur"]:
    if col not in df.columns:
        df[col] = [0] * len(df)   # lijst van nullen met dezelfde lengte als df

# Converteer naar numeriek en vul missende waarden aan met 0
df["verwachte_opbrengst_eur"] = pd.to_numeric(df["verwachte_opbrengst_eur"], errors="coerce").fillna(0)
df["verwachte_kosten_eur"]   = pd.to_numeric(df["verwachte_kosten_eur"], errors="coerce").fillna(0)

# Bereken verwachte winst
df["verwachte_winst_eur"] = df["verwachte_opbrengst_eur"] - df["verwachte_kosten_eur"]

# ✅ Zorg dat de strategie-kolom altijd bestaat
if "strategie" not in df.columns:
    df["strategie"] = None


# 📅 Totale verwachte winst per jaar (o.b.v. doorlooptijd)
st.subheader("📆 Totale verwachte winst per jaar")

# Filter alleen de rijen die echte datums bevatten
df_planning = df[pd.to_datetime(df["doorlooptijd"], errors="coerce").notnull()].copy()

# Converteer de kolom opnieuw om zeker te zijn dat alles datetime is
df_planning["doorlooptijd"] = pd.to_datetime(df_planning["doorlooptijd"], errors="coerce")

# Maak standaard een lege DataFrame om fouten te voorkomen
jaartotalen = pd.DataFrame(columns=["jaar", "verwachte_winst_eur"])

if not df_planning.empty:
    df_planning["jaar"] = df_planning["doorlooptijd"].dt.year
    jaartotalen = df_planning.groupby("jaar")["verwachte_winst_eur"].sum().reset_index()

# Toon resultaten of info
if not jaartotalen.empty:
    st.dataframe(
        jaartotalen.rename(
            columns={"jaar": "Jaar", "verwachte_winst_eur": "Totale verwachte winst (EUR)"}
        )
    )
else:
    st.info("Nog geen geldige doorlooptijden ingevoerd.")

# 📌 Overzicht per strategie
st.subheader("🧭 Strategieoverzicht")

# ✅ Zorg dat de kolom altijd bestaat
if "strategie" not in df.columns:
    df["strategie"] = None

df_strategie = df[df["strategie"].notna()].copy()

if not df_strategie.empty:
    strategie_stats = df_strategie.groupby("strategie").agg({
        "locatie": "count",
        "verwachte_opbrengst_eur": "sum",
        "verwachte_kosten_eur": "sum",
        "verwachte_winst_eur": "sum"
    }).reset_index()

    strategie_stats = strategie_stats.rename(columns={
        "locatie": "Aantal percelen",
        "verwachte_opbrengst_eur": "Totale opbrengst (EUR)",
        "verwachte_kosten_eur": "Totale kosten (EUR)",
        "verwachte_winst_eur": "Totale winst (EUR)"
    })

    st.dataframe(strategie_stats, use_container_width=True)
else:
    st.info("Er zijn nog geen strategieën ingevoerd.")


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

    # Zorg dat verkoopprijs-kolommen altijd aanwezig zijn
    if "verkoopprijs" not in df_result.columns:
        df_result["verkoopprijs"] = 0
    if "verkoopprijs_eur" not in df_result.columns:
        df_result["verkoopprijs_eur"] = 0.0

    # ✅ Kolommenlijst uitbreiden
    kolommen = [
        "locatie",
        "verkoopprijs", "verkoopprijs_eur",   # toegevoegd
        "verkoopwaarde", "verkoopwaarde_eur",
        "totaal_inleg", "totaal_rente",
        "netto_winst", "netto_winst_eur"
    ]

    # Gebruik reindex om ontbrekende kolommen automatisch aan te vullen
    df_view = df_result.reindex(columns=kolommen, fill_value=0).copy()

    # ✅ Valutakolommen formatteren
    df_view["verkoopprijs"] = df_view["verkoopprijs"].apply(lambda x: format_currency(x, "GMD"))
    df_view["verkoopprijs_eur"] = df_view["verkoopprijs_eur"].apply(lambda x: format_currency(x, "EUR"))
    df_view["verkoopwaarde"] = df_view["verkoopwaarde"].apply(lambda x: format_currency(x, "GMD"))
    df_view["verkoopwaarde_eur"] = df_view["verkoopwaarde_eur"].apply(lambda x: format_currency(x, "EUR"))
    df_view["totaal_inleg"] = df_view["totaal_inleg"].apply(lambda x: format_currency(x, "GMD"))
    df_view["totaal_rente"] = df_view["totaal_rente"].apply(lambda x: format_currency(x, "GMD"))
    df_view["netto_winst"] = df_view["netto_winst"].apply(lambda x: format_currency(x, "GMD"))
    df_view["netto_winst_eur"] = df_view["netto_winst_eur"].apply(lambda x: format_currency(x, "EUR"))

    # ✅ Tabel tonen
    st.dataframe(df_view.sort_values(by="netto_winst", ascending=False), use_container_width=True)


    # ✅ Sorteer en toon
    st.dataframe(df_view.sort_values(by="netto_winst", ascending=False), use_container_width=True)


    # 👥 Per investeerder inzicht
    if not verkochte_df.empty:
        st.markdown("### 👥 Investeerders per verkocht perceel")

        for _, perceel in verkochte_df.iterrows():
            locatie = perceel.get("locatie", "Onbekend")
            st.markdown(f"#### 📍 {locatie}")
            analyse = analyse_verkocht_perceel(perceel, exchange_rate)

            if analyse:
                for inv in analyse["investeerders"]:
                    naam = inv.get("naam", "Investeerder")
                    kapitaalkosten = inv.get("kapitaalkosten", 0)
                    winstdeling = inv.get("winstdeling", 0)
                    totaal = kapitaalkosten + winstdeling

                    kapitaalkosten_eur = inv.get("kapitaalkosten_eur", 0)
                    winst_eur = inv.get("winst_eur", 0)
                    totaal_eur = kapitaalkosten_eur + winst_eur

                    inleg_eur = inv.get("inleg_eur", 0)
                    rendement_pct = (winst_eur / inleg_eur * 100) if inleg_eur else 0
                    winstdeling_pct = inv.get("winstdeling_pct", 0) * 100

                    st.write(
                        f"- {naam}: kapitaalkosten {format_currency(kapitaalkosten, 'GMD')}, "
                        f"winstdeling {format_currency(winstdeling, 'GMD')} "
                        f"({winstdeling_pct:.0f}%), totaal: {format_currency(totaal, 'GMD')} "
                        f"({format_currency(totaal_eur, 'EUR')}), netto winst: {format_currency(winst_eur, 'EUR')} "
                        f"({rendement_pct:.1f}%)"
                    )
