import pandas as pd
import pydeck as pdk
import numpy as np
import requests
from datetime import date, timedelta
import streamlit as st
from oauth2client.service_account import ServiceAccountCredentials
import gspread
from geopy.distance import geodesic
import json


# 🟡 1. Wisselkoers ophalen via FX Rates API
ttldays=3600
@st.cache_data(ttl=ttldays)
def get_exchange_rate_eur_to_gmd():
    url = "https://api.fxratesapi.com/latest"
    headers = {"Authorization": f"Bearer {st.secrets['fxrates_token']}"}
    params = {"base": "EUR", "symbols": "GMD"}
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        data = resp.json()
        return data.get("rates", {}).get("GMD")
    return None

# 🔵 2. Wisselkoersvolatiliteit berekenen
ttldays_vol=86400
@st.cache_data(ttl=ttldays_vol)
def get_exchange_rate_volatility(dagen=30):
    end_date = date.today()
    start_date = end_date - timedelta(days=dagen)
    url = "https://api.fxratesapi.com/timeseries"
    headers = {"Authorization": f"Bearer {st.secrets['fxrates_token']}"}
    params = {
        "base": "EUR", "symbols": "GMD",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        data = resp.json()
        rates = data.get("rates", {})
        koersen = [v.get("GMD") for v in rates.values() if "GMD" in v]
        if len(koersen) >= 2:
            std = np.std(koersen)
            mean = np.mean(koersen)
            return round((std / mean) * 100, 2)
    return None

# 🧭 3. Geocoding via Google Maps API
def geocode(locatie: str) -> tuple:
    api_key = st.secrets["google_api_key"]
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": f"{locatie}, Gambia", "key": api_key}
    r = requests.get(url, params=params)
    if r.status_code == 200:
        data = r.json()
        results = data.get("results")
        if results:
            loc = results[0]["geometry"]["location"]
            return loc.get("lat"), loc.get("lng")
    return None, None

# 📍 4. Bepaal dichtstbijzijnde regio op basis van afstand
def match_op_basis_van_afstand(lat: float, lon: float, referentieregio_df: pd.DataFrame) -> str:
    min_afstand = float("inf")
    beste_regio = None
    for _, row in referentieregio_df.iterrows():
        coord = (row["Latitude"], row["Longitude"])
        afstand = geodesic((lat, lon), coord).km
        if afstand < min_afstand:
            min_afstand = afstand
            beste_regio = row["regio"]
    return beste_regio

# 📊 5. Risico- en adviesmodel per perceel
def beoordeel_perceel_modulair(row: pd.Series, marktprijzen_df: pd.DataFrame, hoofdsteden_df: pd.DataFrame) -> tuple:
    score = 0
    toelichting = []
    aankoop = row.get("Aankoopprijs_GMD", 0)
    grootte = row.get("Grootte_m2", 0)
    # Valutarisico
    if aankoop > 800000:
        score -= 1
        toelichting.append("Hoge investering verhoogt valutarisico.")
    else:
        score += 1
        toelichting.append("Beheersbare investering verlaagt valutarisico.")
    # Rendement
    rendement = ((grootte * 400) - aankoop) / aankoop if aankoop > 0 else 0
    if rendement > 0.4:
        score += 1
        toelichting.append("Hoog verwacht rendement.")
    elif rendement < 0:
        score -= 1
        toelichting.append("Negatief verwacht rendement.")
    else:
        toelichting.append("Gemiddeld rendement.")
    # Marktprijsvergelijking
    lat, lon = row.get("Latitude"), row.get("Longitude")
    regio = None
    if lat is not None and lon is not None:
        regio = match_op_basis_van_afstand(lat, lon, hoofdsteden_df)
    marktprijs = None
    if regio:
        try:
            marktprijs = marktprijzen_df.loc[marktprijzen_df["regio"] == regio, "Prijs_per_m2"].iat[0]
        except (IndexError, KeyError):
            marktprijs = None
    aankoop_per_m2 = aankoop / grootte if grootte > 0 else 0
    if marktprijs is not None:
        if aankoop_per_m2 > marktprijs * 1.1:
            score -= 1
            toelichting.append("Aankoopprijs ligt boven marktwaarde.")
        elif aankoop_per_m2 < marktprijs * 0.9:
            score += 1
            toelichting.append("Aankoopprijs ligt onder marktwaarde.")
        else:
            toelichting.append("Aankoopprijs ligt binnen marktwaarde.")
    else:
        toelichting.append("Marktprijs per m² niet beschikbaar.")
    advies = "Kopen" if score >= 2 else "Mijden" if score <= -1 else "Twijfel"
    return score, ", ".join(toelichting), advies

# 🔗 6. Google Sheets verbinding via service account in secrets

def get_worksheet(sheet_name: str = None, tabblad: str = "Blad1") -> gspread.Worksheet:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    g = st.secrets["gspread"]
    creds_dict = {
        "type":                        g["type"],
        "project_id":                  g["project_id"],
        "private_key_id":              g["private_key_id"],
        "private_key":                 g["private_key"].replace("\\n", "\n"),
        "client_email":                g["client_email"],
        "client_id":                   g["client_id"],
        "auth_uri":                    g["auth_uri"],
        "token_uri":                   g["token_uri"],
        "auth_provider_x509_cert_url": g["auth_provider_x509_cert_url"],
        "client_x509_cert_url":        g["client_x509_cert_url"],
    }
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    # Use sheet_id from secrets if provided
    sheet_id = g.get("sheet_id")
    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
    elif sheet_name:
        spreadsheet = client.open(sheet_name)
    else:
        raise ValueError("No sheet_id in secrets and no sheet_name provided.")
    return spreadsheet.worksheet(tabblad)

# 📥 7. Percelen opslaan en laden via JSON

def save_percelen_as_json(percelen: list[dict]) -> None:
    ws = get_worksheet()
    ws.clear()
    ws.append_row(["json_data"])
    for perceel in percelen:
        json_str = json.dumps(perceel, ensure_ascii=False, default=str)
        ws.append_row([json_str])

def load_percelen_from_json() -> list[dict]:
    ws = get_worksheet()
    rows = ws.get_all_values()
    if not rows or len(rows[0]) == 0 or rows[0][0] != "json_data":
        return []
    return [json.loads(row[0]) for row in rows[1:] if row and row[0].strip()]

    

# 🌍 10. DataFrame met hoofdregio’s in Gambia
hoofdsteden_df = pd.DataFrame([
    {"regio": "Banjul",      "Latitude": 13.4549, "Longitude": -16.5790},
    {"regio": "Kanifing",    "Latitude": 13.4799, "Longitude": -16.6825},
    {"regio": "Serekunda",   "Latitude": 13.4304, "Longitude": -16.6781},
    {"regio": "Brikama",     "Latitude": 13.2700, "Longitude": -16.6450},
    {"regio": "Bakau",       "Latitude": 13.4781, "Longitude": -16.6819},
    {"regio": "Bijilo",      "Latitude": 13.4218, "Longitude": -16.6814},
    {"regio": "Lamin",       "Latitude": 13.3731, "Longitude": -16.6528},
    {"regio": "Farato",      "Latitude": 13.3420, "Longitude": -16.7155},
    {"regio": "Tanji",       "Latitude": 13.3522, "Longitude": -16.7915},
    {"regio": "Gunjur",      "Latitude": 13.2010, "Longitude": -16.7507},
    {"regio": "Mansa Konko", "Latitude": 13.3500, "Longitude": -15.9500},
    {"regio": "Soma",        "Latitude": 13.4000, "Longitude": -15.5333},
    {"regio": "Janjanbureh", "Latitude": 13.5333, "Longitude": -14.7667},
    {"regio": "Kuntaur",     "Latitude": 13.6833, "Longitude": -14.9333},
    {"regio": "Kerewan",     "Latitude": 13.4892, "Longitude": -16.0883},
    {"regio": "Farafenni",   "Latitude": 13.5667, "Longitude": -15.6000},
    {"regio": "Essau",       "Latitude": 13.4833, "Longitude": -16.5333},
    {"regio": "Basse",       "Latitude": 13.3167, "Longitude": -14.2167},
    {"regio": "Fatoto",      "Latitude": 13.3667, "Longitude": -13.9833},
    {"regio": "Koina",       "Latitude": 13.4000, "Longitude": -13.8667}
])

# 📥 Lees marktprijzen uit Blad2 van PercelenData
def read_marktprijzen(sheet_name: str = "PercelenData", tabblad: str = "Blad2") -> pd.DataFrame:
    try:
        ws = get_worksheet(sheet_name=sheet_name, tabblad=tabblad)
        records = ws.get_all_records()
        df = pd.DataFrame(records)

        # ✅ Standaardiseer kolomnamen: lowercase + geen spaties
        df.columns = df.columns.str.strip().str.lower()

        # ✅ Check of verplichte kolommen aanwezig zijn
        if "regio" not in df.columns or "prijs_per_m2" not in df.columns:
            st.warning("De kolommen 'regio' en/of 'prijs_per_m2' ontbreken in het tabblad.")
            return pd.DataFrame(columns=["regio", "prijs_per_m2"])

        return df

    except Exception as e:
        st.warning(f"Marktprijzen niet kunnen laden: {e}")
        return pd.DataFrame(columns=["regio", "prijs_per_m2"])

# 💾 Schrijf marktprijzen terug naar Blad2 van PercelenData
def write_marktprijzen(df: pd.DataFrame, sheet_name: str = "PercelenData", tabblad: str = "Blad2"):
    try:
        # Zorg dat kolomnamen correct en gestandaardiseerd zijn
        df = df.rename(columns=str.lower)
        df = df[["regio", "prijs_per_m2"]]

        ws = get_worksheet(sheet_name=sheet_name, tabblad=tabblad)
        ws.clear()

        # Schrijf de header
        ws.append_row(["regio", "prijs_per_m2"])

        # Schrijf de data
        for _, row in df.iterrows():
            ws.append_row([row["regio"], row["prijs_per_m2"]])

    except Exception as e:
        st.error(f"Fout bij opslaan van marktprijzen: {e}")

# ➕ Vul ontbrekende regio's aan met prijs 0
def aanvul_regios(df: pd.DataFrame, regio_lijst: list) -> pd.DataFrame:
    bestaande = df["regio"].tolist() if "regio" in df.columns else []
    aanvullingen = [{"regio": r, "Prijs_per_m2": 0} for r in regio_lijst if r not in bestaande]
    if aanvullingen:
        df = pd.concat([df, pd.DataFrame(aanvullingen)], ignore_index=True)
    return df

