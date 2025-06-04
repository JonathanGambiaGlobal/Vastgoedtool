import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import requests
import pydeck as pdk

# --- API token en functies voor live wisselkoers en volatiliteit (optioneel) ---
TOKEN = "fxr_live_4b81dd1f5e01d6a85d9054a4bdb2ce0cf9f8"

@st.cache_data(ttl=3600)
def get_exchange_rate_eur_to_gmd():
    url = "https://api.fxratesapi.com/latest"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    params = {"base": "EUR", "symbols": "GMD"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
        if "rates" in data and "GMD" in data["rates"]:
            return data["rates"]["GMD"]
    return None

@st.cache_data(ttl=86400)
def get_exchange_rate_volatility(dagen=30):
    end_date = date.today()
    start_date = end_date - timedelta(days=dagen)
    url = "https://api.fxratesapi.com/timeseries"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    params = {"base": "EUR", "symbols": "GMD", "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
        if "rates" in data:
            koersen = [v["GMD"] for v in data["rates"].values() if "GMD" in v]
            if len(koersen) >= 2:
                std = np.std(koersen)
                mean = np.mean(koersen)
                return round((std / mean) * 100, 2)
    return None

# --- Locatie geocoding ---
def geocode(locatie):
    try:
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={locatie}, Gambia"
        headers = {"User-Agent": "Estate4Mission/1.0"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except:
        pass
    return None, None

# --- Session State Initialisatie ---
if "percelen" not in st.session_state:
    st.session_state["percelen"] = []

if "marktprijzen" not in st.session_state:
    # Default marktprijzen per regio
    st.session_state["marktprijzen"] = pd.DataFrame({
        "Regio": ["Banjul", "Serekunda", "Brikama", "Kanifing", "Bakau"],
        "Prijs_per_m2": [3000, 2250, 1850, 2100, 2300]
    })

# --- Dashboard Titel en Economische gegevens ---
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

# --- Marktprijzen Upload en Bewerking ---
st.subheader("Marktprijzen per m² per regio")

uploaded_file = st.file_uploader("Upload grondprijsbestand (Excel of CSV)", type=["xlsx", "csv"])
if uploaded_file:
    if uploaded_file.name.endswith(".csv"):
        df_uploaded = pd.read_csv(uploaded_file)
    else:
        df_uploaded = pd.read_excel(uploaded_file)
    st.session_state["marktprijzen"] = df_uploaded.copy()
    st.success("Marktprijzen succesvol geladen via upload.")

# Interactieve tabel om prijzen te bewerken
st.write("Pas hier de marktprijzen aan of upload een nieuw bestand.")
marktprijzen_edited = st.data_editor(st.session_state["marktprijzen"], num_rows="dynamic")
st.session_state["marktprijzen"] = marktprijzen_edited.copy()

st.markdown("---")

# --- Percelen Upload ---
st.subheader("Upload perceelbestand (Excel)")

uploaded_percelen = st.file_uploader("Kies een Excelbestand met kolommen: Locatie, Aankoopprijs_GMD, Grootte_m2", type=["xlsx"])
if uploaded_percelen:
    try:
        upload_df = pd.read_excel(uploaded_percelen)
        for _, row in upload_df.iterrows():
            lat, lon = geocode(row["Locatie"])
            valutarisico, juridisch_risico, rendement, advies, toelichting = None, None, None, None, None
            # Gebruik aangepaste beoordeel functie later
            nieuw = {
                "Locatie": row["Locatie"],
                "Aankoopprijs_GMD": row["Aankoopprijs_GMD"],
                "Grootte_m2": row["Grootte_m2"],
                "Latitude": lat,
                "Longitude": lon
            }
            st.session_state["percelen"].append(nieuw)
        st.success("Excelbestand met percelen verwerkt!")
    except Exception as e:
        st.error(f"Fout bij inlezen bestand: {e}")

st.markdown("---")

# --- Handmatige perceel toevoegen ---
st.subheader("Voeg een perceel handmatig toe")
with st.form("perceel_form"):
    locatie = st.text_input("Locatie")
    prijs = st.number_input("Aankoopprijs (GMD)", min_value=0)
    grootte = st.number_input("Grootte (m²)", min_value=0)
    toevoegen = st.form_submit_button("Toevoegen")

if toevoegen:
    lat, lon = geocode(locatie)
    nieuw = {
        "Locatie": locatie,
        "Aankoopprijs_GMD": prijs,
        "Grootte_m2": grootte,
        "Latitude": lat,
        "Longitude": lon
    }
    st.session_state["percelen"].append(nieuw)
    st.success(f"Perceel toegevoegd: {locatie}")

# --- Risico en adviesmodel inclusief marktprijsvergelijking ---
def beoordeel_perceel_modulair(row, marktprijzen_df):
    score = 0
    toelichting = []

    # Valutarisico (voorbeeld)
    if row["Aankoopprijs_GMD"] > 800000:
        score -= 1
        toelichting.append("Hoge investering verhoogt valutarisico.")
    else:
        score += 1
        toelichting.append("Beheersbare investering verlaagt valutarisico.")

    # Juridisch risico (voorbeeld)
    if not row["Locatie"].lower().startswith(("brikama", "serekunda", "banjul")):
        score -= 1
        toelichting.append("Locatie buiten kerngebieden verhoogt juridisch risico.")
    else:
        score += 1
        toelichting.append("Locatie binnen erkende regio verlaagt risico.")

    # Rendement (voorbeeld)
    rendement = round((row["Grootte_m2"] * 400 - row["Aankoopprijs_GMD"]) / row["Aankoopprijs_GMD"], 2) if row["Aankoopprijs_GMD"] > 0 else 0
    if rendement > 0.4:
        score += 1
        toelichting.append("Hoog verwacht rendement.")
    elif rendement < 0:
        score -= 1
        toelichting.append("Negatief verwacht rendement.")
    else:
        toelichting.append("Gemiddeld rendement.")

    # Marktprijs per m2 ophalen
    marktprijs = None
    locatie_key = row["Locatie"]
    for idx, mp_row in marktprijzen_df.iterrows():
        if mp_row["Regio"].lower() == locatie_key.lower():
            marktprijs = mp_row["Prijs_per_m2"]
            break

    aankoopprijs_per_m2 = row["Aankoopprijs_GMD"] / row["Grootte_m2"] if row["Grootte_m2"] > 0 else 0
    if marktprijs is not None:
        if aankoopprijs_per_m2 > marktprijs * 1.1:
            score -= 1
            toelichting.append("Aankoopprijs ligt boven marktwaarde.")
        elif aankoopprijs_per_m2 < marktprijs * 0.9:
            score += 1
            toelichting.append("Aankoopprijs ligt onder marktwaarde.")
        else:
            toelichting.append("Aankoopprijs ligt binnen marktwaarde.")
    else:
        toelichting.append("Marktprijs per m² niet beschikbaar.")

    # Advies
    if score >= 2:
        advies = "Kopen"
    elif score <= -1:
        advies = "Mijden"
    else:
        advies = "Twijfel"

    return score, ", ".join(toelichting), advies

# --- Overzicht met advies ---
if st.session_state["percelen"]:
    st.subheader("Ingevoerde percelen met risicobeoordeling")
    df_percelen = pd.DataFrame(st.session_state["percelen"])

    # Voeg risicoscores toe
    scores = df_percelen.apply(lambda r: beoordeel_perceel_modulair(r, st.session_state["marktprijzen"]), axis=1)
    df_percelen[["Score", "Toelichting", "Advies"]] = pd.DataFrame(scores.tolist(), index=df_percelen.index)

    for i, row in df_percelen.iterrows():
        cols = st.columns([6, 1])
        with cols[0]:
            st.write(f"📍 {row['Locatie']} | GMD {row['Aankoopprijs_GMD']} | {row['Grootte_m2']} m²")
            st.caption(f"💡 {row['Advies']} – {row['Toelichting']}")
        with cols[1]:
            if st.button("❌", key=f"delete_{i}"):
                st.session_state["percelen"].pop(i)
                st.experimental_rerun()

    st.download_button("Download als CSV", data=df_percelen.to_csv(index=False).encode("utf-8"),
                       file_name="percelen_advies_score.csv", mime="text/csv")

    # Kaartweergave
    if "Latitude" in df_percelen.columns and df_percelen["Latitude"].notnull().any():
        st.subheader("Interactieve kaartweergave")
        kaart_df = df_percelen.dropna(subset=["Latitude", "Longitude"])
        kaart_df["tooltip"] = kaart_df.apply(
            lambda row: f"{row['Locatie']}\\nAdvies: {row['Advies']}\\nRendement: {row['Grootte_m2']} m²", axis=1)

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=kaart_df,
            get_position='[Longitude, Latitude]',
            get_radius=250,
            get_color='[200, 30, 0, 160]',
            pickable=True
        )

        view_state = pdk.ViewState(
            latitude=kaart_df["Latitude"].mean(),
            longitude=kaart_df["Longitude"].mean(),
            zoom=10,
            pitch=0
        )

        st.pydeck_chart(pdk.Deck(
            map_style="mapbox://styles/mapbox/light-v9",
            initial_view_state=view_state,
            layers=[layer],
            tooltip={"text": "{tooltip}"}
        ))
else:
    st.info("Nog geen percelen toegevoegd.")

