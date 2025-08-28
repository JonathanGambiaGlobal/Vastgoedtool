import pandas as pd
import pydeck as pdk
import numpy as np
import requests
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import streamlit as st
from oauth2client.service_account import ServiceAccountCredentials
import gspread
from geopy.distance import geodesic
import json
import os, tomllib

def get_ai_config():
    """Zoekt de [ai] sectie in config.toml of .streamlit/config.toml."""
    base = os.path.dirname(__file__)
    cwd  = os.getcwd()
    candidates = [
        os.path.join(base, ".streamlit", "config.toml"),
        os.path.join(cwd,  ".streamlit", "config.toml"),
        os.path.join(base, "config.toml"),
        os.path.join(cwd,  "config.toml"),
    ]
    for p in candidates:
        if os.path.exists(p):
            with open(p, "rb") as f:
                cfg = tomllib.load(f)
            return cfg.get("ai", {})
    return {}

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


@st.cache_data(ttl=60)
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

@st.cache_data(ttl=300)

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

# 🔁 Pipeline-rendering per perceel (3 fasen versie)
def render_pipeline(huidige_fase: str, fase_status: dict = None) -> str:
    # Zelfde fases als in 1_Percelenbeheer.py
    PIPELINE_FASEN = ["Aankoop", "Omzetting / bewerking", "Verkoop", "Verkocht"]

    symbols = []
    actief_bereikt = False
    for fase in PIPELINE_FASEN:
        if fase_status and fase_status.get(fase):
            symbool = "✅"   # afgerond
        elif not actief_bereikt and fase == huidige_fase:
            symbool = "🔵"   # huidige fase
            actief_bereikt = True
        else:
            symbool = "⚪"   # nog niet bereikt
        symbols.append(f"{symbool} {fase}")

    return " → ".join(symbols)


def format_currency(amount, currency="EUR") -> str:
    if currency == "EUR":
        return f"€ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    elif currency == "GMD":
        return f"{amount:,.0f} GMD".replace(",", ".")
    return str(amount)

@st.cache_data(ttl=60)
def build_rentebetalingen(percelen: list[dict], today: date | None = None) -> pd.DataFrame:
    """Maak een overzicht van (volgende) rentebetalingen en opgebouwde rente per perceel/investeerder."""
    if today is None:
        today = date.today()

    rows = []
    for perceel in percelen or []:
        locatie = perceel.get("locatie", "Onbekend")
        aankoopdatum_str = perceel.get("aankoopdatum", "")
        aankoopdatum = pd.to_datetime(aankoopdatum_str, errors="coerce")
        if pd.isna(aankoopdatum):
            aankoopdatum = pd.Timestamp(today)
        aankoopdatum_date = aankoopdatum.date()

        for inv in (perceel.get("investeerders") or []):
            naam = (inv or {}).get("naam", "Investeerder")
            bedrag_eur = float((inv or {}).get("bedrag_eur", 0.0) or 0.0)
            rente = float((inv or {}).get("rente", 0.0) or 0.0)
            rentetype = str((inv or {}).get("rentetype", "bij verkoop")).lower()

            if rente <= 0 or rentetype not in ("maandelijks", "jaarlijks"):
                continue

            if rentetype == "maandelijks":
                maanden = (today.year - aankoopdatum_date.year) * 12 + (today.month - aankoopdatum_date.month)
                maanden = max(maanden, 0)
                opgebouwde = bedrag_eur * (rente / 12) * maanden
                volgende = aankoopdatum_date + relativedelta(months=maanden + 1)
                volgende_bedrag = bedrag_eur * rente / 12
            else:  # jaarlijks
                jaren = max(today.year - aankoopdatum_date.year, 0)
                opgebouwde = bedrag_eur * rente * jaren
                volgende = aankoopdatum_date + relativedelta(years=jaren + 1)
                volgende_bedrag = bedrag_eur * rente

            rows.append({
                "Perceel": locatie,
                "Investeerder": naam,
                "Rentetype": rentetype,
                "Startdatum": aankoopdatum_date.strftime("%d-%m-%Y"),
                "Volgende betaling": volgende.strftime("%d-%m-%Y"),
                "Bedrag volgende betaling (€)": round(volgende_bedrag, 2),
                "Opgebouwde rente tot nu (€)": round(opgebouwde, 2),
                "_volgende_sort": volgende,  # interne sort-key
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("_volgende_sort").drop(columns=["_volgende_sort"]).reset_index(drop=True)
    return df


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

@st.cache_data(ttl=60)
def analyse_portfolio_perceel(perceel: dict, groei_pct: float, horizon_jaren: int, exchange_rate: float) -> dict | None:
    aankoopprijs = _safe_float(perceel.get("aankoopprijs"))
    aankoopdatum = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce")
    investeerders = perceel.get("investeerders", [])
    if not isinstance(investeerders, list):
        investeerders = []

    # eigen inleg = aankoopprijs - externe bedragen
    totaal_extern = sum(_safe_float(i.get("bedrag")) for i in investeerders if isinstance(i, dict))
    eigen_inleg = aankoopprijs - totaal_extern
    if eigen_inleg > 0:
        investeerders.append({
            "naam": "Eigen beheer",
            "bedrag": eigen_inleg,
            "rente": 0,
            "rentetype": "bij verkoop",
            "winstdeling": 1.0,
        })

    if not pd.notnull(aankoopdatum) or aankoopprijs <= 0:
        return None

    # verkoopwaarde: geplande verkoop of prognose
    verkoopprijs_gmd = _safe_float(perceel.get("verkoopprijs"))
    verkoopprijs_eur = _safe_float(perceel.get("verkoopprijs_eur"))
    if verkoopprijs_gmd > 0:
        verkoopwaarde = verkoopprijs_gmd
    else:
        verkoopwaarde = aankoopprijs * ((1 + groei_pct / 100) ** horizon_jaren)
    if verkoopprijs_eur <= 0 and exchange_rate:
        verkoopprijs_eur = round(verkoopwaarde / exchange_rate, 2)

    vandaag = pd.Timestamp.today()
    maanden = max((vandaag.year - aankoopdatum.year) * 12 + (vandaag.month - aankoopdatum.month), 1)
    jaren = maanden / 12

    totaal_inleg = 0.0
    totaal_rente = 0.0
    inv_rows = []

    for inv in investeerders:
        if isinstance(inv, dict):
            bedrag = _safe_float(inv.get("bedrag"))
            rente = _safe_float(inv.get("rente"))
            winstdeling_pct = _safe_float(inv.get("winstdeling"))
            rentetype = (inv.get("rentetype") or "maandelijks").lower()
            naam = inv.get("naam", "Investeerder")
        else:
            bedrag = rente = winstdeling_pct = 0.0
            rentetype = "bij verkoop"
            naam = str(inv)

        if rentetype == "maandelijks":
            rente_opbouw = bedrag * ((1 + rente / 12) ** maanden - 1)
        elif rentetype == "jaarlijks":
            rente_opbouw = bedrag * ((1 + rente) ** jaren - 1)
        elif rentetype == "bij verkoop":
            rente_opbouw = bedrag * rente
        else:
            rente_opbouw = 0.0

        totaal_inleg += bedrag
        totaal_rente += rente_opbouw

        inv_rows.append({
            "naam": naam,
            "inleg": round(bedrag, 2),
            "rente": round(rente_opbouw, 2),
            "kapitaalkosten": round(bedrag + rente_opbouw, 2),
            "kapitaalkosten_eur": round((bedrag + rente_opbouw) / exchange_rate, 2) if exchange_rate else None,
            "rentetype": rentetype,
            "winstdeling_pct": winstdeling_pct,
        })

    netto_winst = verkoopwaarde - totaal_inleg - totaal_rente
    waardestijging = max(0, verkoopwaarde - aankoopprijs)

    for r in inv_rows:
        winst_aandeel = waardestijging * r.get("winstdeling_pct", 0)
        r["winstdeling"] = round(winst_aandeel, 2)
        r["winst_eur"] = round(winst_aandeel / exchange_rate, 2) if exchange_rate else None

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
        "investeerders": inv_rows,
    }

@st.cache_data(ttl=60)
def analyse_verkocht_perceel(perceel: dict, exchange_rate: float) -> dict:
    aankoopprijs = _safe_float(perceel.get("aankoopprijs"))
    verkoopprijs_gmd = _safe_float(perceel.get("verkoopprijs"))
    verkoopprijs_eur = _safe_float(perceel.get("verkoopprijs_eur"))
    aankoopdatum = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce")
    verkoopdatum = pd.to_datetime(perceel.get("verkoopdatum"), errors="coerce")
    investeerders = perceel.get("investeerders", [])
    if not isinstance(investeerders, list):
        investeerders = []

    if verkoopprijs_eur <= 0 and exchange_rate:
        verkoopprijs_eur = round(verkoopprijs_gmd / exchange_rate, 2)

    maanden = jaren = 0
    if pd.notnull(aankoopdatum) and pd.notnull(verkoopdatum):
        maanden = max((verkoopdatum.year - aankoopdatum.year) * 12 + (verkoopdatum.month - aankoopdatum.month), 1)
        jaren = maanden / 12

    totaal_inleg = aankoopprijs
    totaal_rente = 0.0
    inv_rows = []

    for inv in investeerders:
        bedrag = _safe_float(inv.get("bedrag"))
        rente = _safe_float(inv.get("rente"))
        rentetype = (inv.get("rentetype") or "bij verkoop").lower()
        winstdeling_pct = _safe_float(inv.get("winstdeling"))
        naam = inv.get("naam", "Investeerder")

        if rentetype == "maandelijks":
            rente_opbouw = bedrag * ((1 + rente / 12) ** maanden - 1)
        elif rentetype == "jaarlijks":
            rente_opbouw = bedrag * ((1 + rente) ** jaren - 1)
        elif rentetype == "bij verkoop":
            rente_opbouw = bedrag * rente
        else:
            rente_opbouw = 0.0

        totaal_inleg += bedrag
        totaal_rente += rente_opbouw

        inv_rows.append({
            "naam": naam,
            "inleg": round(bedrag, 2),
            "rente": round(rente_opbouw, 2),
            "kapitaalkosten": round(bedrag + rente_opbouw, 2),
            "kapitaalkosten_eur": round((bedrag + rente_opbouw) / exchange_rate, 2) if exchange_rate else None,
            "rentetype": rentetype,
            "winstdeling_pct": winstdeling_pct,
        })

    netto_winst = verkoopprijs_gmd - totaal_inleg - totaal_rente
    netto_winst_eur = round(netto_winst / exchange_rate, 2) if exchange_rate else None

    waardestijging = max(0, verkoopprijs_gmd - aankoopprijs)
    for r in inv_rows:
        winst_aandeel = waardestijging * r.get("winstdeling_pct", 0)
        r["winstdeling"] = round(winst_aandeel, 2)
        r["winst_eur"] = round(winst_aandeel / exchange_rate, 2) if exchange_rate else None

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
        "investeerders": inv_rows,
    }

@st.cache_data(ttl=60)
def verdeel_winst(perceel_row: dict | pd.Series) -> pd.DataFrame:
    """Maak maandregels met winstverdeling tussen start en eind (doorlooptijd of verkoopdatum)."""
    def num(x, d=0.0):
        try:
            if x is None or (isinstance(x, float) and pd.isna(x)):
                return d
            return float(x)
        except Exception:
            return d

    start_raw = perceel_row.get("start_verkooptraject") or perceel_row.get("aankoopdatum") or date.today()
    einde_raw = perceel_row.get("doorlooptijd") or perceel_row.get("verkoopdatum")

    start = pd.to_datetime(start_raw, errors="coerce")
    einde = pd.to_datetime(einde_raw, errors="coerce")
    if pd.isna(start) and not pd.isna(einde):
        start = einde - relativedelta(months=1)
    if pd.isna(start):
        start = pd.Timestamp.today().normalize()
    if pd.isna(einde) or einde < start:
        einde = start + relativedelta(months=1)

    opbrengst = num(perceel_row.get("totaal_opbrengst_eur")) or num(perceel_row.get("verwachte_opbrengst_eur"))
    kosten    = num(perceel_row.get("verwachte_kosten_eur"))
    aankoop   = num(perceel_row.get("aankoopprijs_eur"))
    investering = aankoop + kosten
    totaal_winst = opbrengst - kosten - aankoop

    looptijd_jaren = max((einde.year - start.year) + (einde.month - start.month) / 12, 0.01)
    winst_per_jaar = totaal_winst if looptijd_jaren < 0.5 else totaal_winst / looptijd_jaren
    rendement_per_jaar_pct = (winst_per_jaar / investering * 100) if investering != 0 else 0.0

    maanden = int(max((einde.year - start.year) * 12 + (einde.month - start.month) + 1, 1))
    maand_winst = totaal_winst / maanden

    rows = []
    datum = start
    for _ in range(maanden):
        rows.append({
            "jaar": datum.year,
            "maand": datum.month,
            "winst_eur": maand_winst,
            "looptijd_jaren": looptijd_jaren,
            "opbrengst": opbrengst,
            "kosten": kosten,
            "aankoop": aankoop,
            "investering": investering,
            "winst_per_jaar": winst_per_jaar,
            "rendement_per_jaar_pct": rendement_per_jaar_pct,
        })
        datum += relativedelta(months=1)
    return pd.DataFrame(rows)




