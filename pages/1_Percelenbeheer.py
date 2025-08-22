import streamlit as st


st.set_page_config(page_title="Percelenbeheer", layout="wide")

st.image("QG.png", width=180)


import pandas as pd
import json
from streamlit_folium import st_folium
import folium 
import copy
from folium.plugins import Draw
from datetime import date
from utils import get_exchange_rate_eur_to_gmd, save_percelen_as_json, load_percelen_from_json
from pyproj import Transformer
from utils import render_pipeline
from utils import format_currency
from auth import login_check
login_check()


def format_date_eu(date_str):
    try:
        dt = pd.to_datetime(date_str, errors="coerce")
        if pd.notnull(dt):
            return dt.strftime("%d-%m-%Y")
    except Exception:
        pass
    return date_str or "n.v.t."

def migrate_percelen():
    transformer = Transformer.from_crs("epsg:32629", "epsg:4326", always_xy=True)
    changed = False

    # Oude (8/4) fasen → nieuwe 3 fasen
    old_to_new = {
        "Oriëntatie": "Aankoop",
        "In onderhandeling": "Aankoop",
        "Te kopen": "Aankoop",
        "Aangekocht": "Aankoop",
        "Geregistreerd": "Omzetting / bewerking",
        "In beheer": "Omzetting / bewerking",
        "In verkoop": "Verkoop",
        # Als je eerder 4 fasen had met "Verkocht" en je wilt die behouden:
        # "Verkocht": "Verkoop",  # of laat weg als je 'Verkocht' los wilt houden
    }

    for perceel in st.session_state.get("percelen", []):
        # ✅ Zorg dat dealstage altijd bestaat en map oude waarden
        ds = perceel.get("dealstage")
        if not ds:
            perceel["dealstage"] = "Aankoop"
            changed = True
        elif ds in old_to_new:
            perceel["dealstage"] = old_to_new[ds]
            changed = True

        # ✅ Zorg dat uploads-dicts bestaan
        if "uploads" not in perceel:
            perceel["uploads"] = {}
            changed = True
        if "uploads_urls" not in perceel:
            perceel["uploads_urls"] = {}
            changed = True

        # 🧹 Fix investeerders (oude, kapotte structuur → 1 entry 'Eigen beheer')
        investeerders = perceel.get("investeerders")
        if isinstance(investeerders, str) or (
            isinstance(investeerders, list)
            and len(investeerders) > 1
            and all(isinstance(i.get("naam"), str) and len(i.get("naam")) == 1 for i in investeerders)
        ):
            perceel["investeerders"] = [{
                "naam": "Eigen beheer",
                "bedrag": 0,
                "bedrag_eur": 0,
                "rente": 0.0,
                "winstdeling": 1.0,
                "rentetype": "bij verkoop"
            }]
            changed = True

        # 🧹 Fix polygon (coördinaat-orde omdraaien indien nodig)
        polygon = perceel.get("polygon")
        if polygon and isinstance(polygon, list):
            # Als waarden buiten lat/lon bereik vallen, is het waarschijnlijk UTM/XY
            if any(
                isinstance(p, list) and len(p) == 2 and (abs(p[0]) > 90 or abs(p[1]) > 180)
                for p in polygon
            ):
                polygon_converted = []
                for pt in polygon:
                    if isinstance(pt, list) and len(pt) == 2:
                        lon_conv, lat_conv = transformer.transform(pt[0], pt[1])
                        polygon_converted.append([lat_conv, lon_conv])
                perceel["polygon"] = polygon_converted
                changed = True

        # 🧹 Fix eigendomstype
        if perceel.get("eigendomstype") in ["Customary land", "Freehold land"]:
            perceel["eigendomstype"] = "Geregistreerd land"
            changed = True

        # 🧮 (Her)bereken verwachte winst
        opbrengst = perceel.get("verwachte_opbrengst_eur", 0) or 0
        kosten = perceel.get("verwachte_kosten_eur", 0) or 0
        aankoop = perceel.get("aankoopprijs_eur", 0) or 0

        berekende_winst = opbrengst - kosten - aankoop
        huidige_winst = perceel.get("verwachte_winst_eur")

        if huidige_winst is None or huidige_winst == 0 or huidige_winst != berekende_winst:
            perceel["verwachte_winst_eur"] = berekende_winst
            changed = True

    if changed:
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
        st.cache_data.clear()
        st.success("✅ Migratie uitgevoerd: fasen gemapt, records opgeschoond en winst bijgewerkt.")
        st.session_state["skip_load"] = True
        st.rerun()

is_admin = st.session_state.get("rol") == "admin"

if "history" not in st.session_state:
    st.session_state["history"] = []

def save_state():
    if "history" not in st.session_state:
        st.session_state["history"] = []
    # Maak een diepe kopie van de percelen en sla op in history
    st.session_state["history"].append(copy.deepcopy(st.session_state["percelen"]))
    print(f"[DEBUG] save_state called. History length: {len(st.session_state['history'])}")


def undo():
    if st.session_state.get("history"):
        print(f"[DEBUG] undo called. History length before pop: {len(st.session_state['history'])}")

        # Zet percelen terug naar vorige versie
        st.session_state["percelen"] = st.session_state["history"].pop()
        st.write("🔄 Percelen na undo:", st.session_state.get("percelen"))
        print(f"[DEBUG] History length after pop: {len(st.session_state['history'])}")

        # Debug: toon alle huidige session_state keys vóór reset
        st.write("🧪 Keys vóór reset:", list(st.session_state.keys()))

        # Algemene key-prefixes
        prefixes = [
            "edit_locatie_", "edit_lengte_", "edit_breedte_",
            "dealstage_edit_", "eigendom_", "aankoopdatum_",
            "aankoopprijs_eur_", "verkoopdatum_", "verkoopprijs_eur_",
            "fase_", "upload_", "opslaan_bewerken_", "x_", "y_", "verwijder_"
        ]

        # Investeerders
        inv_prefixes = [
            "inv_naam_edit_", "inv_bedrag_edit_", "inv_rente_edit_",
            "inv_winst_edit_", "inv_type_edit_"
        ]

        # ▶️ Bepaal maximaal indexgetal uit session_state keys
        all_keys = list(st.session_state.keys())
        all_indices = []

        for key in all_keys:
            for prefix in prefixes + inv_prefixes:
                if key.startswith(prefix):
                    suffix = key[len(prefix):]
                    if suffix.isdigit():
                        all_indices.append(int(suffix))
                    elif "_" in suffix and suffix.split("_")[0].isdigit():
                        all_indices.append(int(suffix.split("_")[0]))

        max_index = max(all_indices, default=0) + 5

        # 🧽 Wis alle widget-keys per perceel
        for i in range(max_index):
            for prefix in prefixes + inv_prefixes:
                st.session_state.pop(f"{prefix}{i}", None)
            for j in range(15):  # tot 15 investeerders per perceel
                for prefix in inv_prefixes:
                    st.session_state.pop(f"{prefix}{i}_{j}", None)

        # Specifieke reset
        st.session_state.pop("investeerders_input", None)
        st.session_state["skip_load"] = True

        # Debug na reset
        st.write("🧼 Keys ná reset:", list(st.session_state.keys()))

        st.rerun()



# Pipelinefases en documentvereisten per fase
PIPELINE_FASEN = [
    "Aankoop",
    "Omzetting / bewerking",
    "Verkoop",
    "Verkocht"  
]

documentvereisten_per_fase = {
    "Aankoop": [
        "Sales agreement",
        "Transfer of ownership",
        "Sketch plan",
        "Rates ontvangstbewijs",
        "Land Use Report",
        "Goedkeuring Alkalo"
    ],
    "Omzetting / bewerking": [
        "Resale agreement"
    ],
    "Verkoop": [
        "Financieringsoverzicht",
        "ID’s investeerders",
        "Uitbetaling investeerders"
    ]
}

def prepare_percelen_for_saving(percelen: list[dict]) -> list[dict]:
    """ Serialize dates to string for JSON storage """
    def serialize(obj):
        if isinstance(obj, date):
            return obj.isoformat()
        return obj
    return [json.loads(json.dumps(p, default=serialize)) for p in percelen]



# Rerun trigger om na opslaan automatisch te herladen
if st.session_state.get("rerun_trigger") is True:
    st.session_state["rerun_trigger"] = False
    st.rerun()

# Scroll naar boven bij laden
st.markdown("""
    <script>
        window.scrollTo(0, 0);
    </script>
""", unsafe_allow_html=True)

st.title("Percelenbeheer")

# Laden percelen en validatie
if "percelen" not in st.session_state or st.session_state.get("skip_load") != True:
    loaded = load_percelen_from_json()
    percelen_valid = []
    for i, p in enumerate(loaded):
        if isinstance(p, dict):
            percelen_valid.append(p)
        else:
            st.warning(f"Percel index {i} is geen dict maar {type(p)}, wordt genegeerd.")
    st.session_state.percelen = percelen_valid

    # ⭐ Vul ontbrekende velden aan voor backwards compatibility
    for perceel in st.session_state["percelen"]:
        perceel.setdefault("wordt_gesplitst", False)
        perceel.setdefault("dealstage", "Aankoop")

# Sidebar invoer velden voor nieuw perceel
st.sidebar.header("📝 Perceelinvoer")

# 📦 Verzamel bestaande locatie-prefixes
bestaande_labels = [p.get("locatie", "") for p in st.session_state.get("percelen", [])]
prefixes = sorted(set(l.rsplit(" ", 1)[0] for l in bestaande_labels if l.strip() and l.rsplit(" ", 1)[-1].isdigit()))

# 🔽 Gebruiker kiest bestaande combinatie of maakt nieuwe
keuze = st.sidebar.selectbox("📍 Gebied & Subzone", prefixes + ["➕ Nieuw gebied..."])

if keuze == "➕ Nieuw gebied...":
    hoofd = st.sidebar.text_input("🌍 Hoofdgebied (bv. Serekunda)")
    sub = st.sidebar.text_input("🏘 Subzone (bv. Sanyang)")
    prefix = f"{hoofd.strip()}, {sub.strip()}"
else:
    prefix = keuze

# 🔢 Automatisch volgnummer genereren
aantal = sum(1 for l in bestaande_labels if l.startswith(f"{prefix} "))
nieuw_label = f"{prefix} {aantal + 1}"
st.sidebar.success(f"📌 Nieuwe locatie: {nieuw_label}")

# 🔁 Zet de variabele 'locatie' gelijk aan de gegenereerde waarde
locatie = nieuw_label


lengte = st.sidebar.number_input("📏 Lengte (m)", min_value=0, value=0)
breedte = st.sidebar.number_input("📐 Breedte (m)", min_value=0, value=0)

wisselkoers = get_exchange_rate_eur_to_gmd()

# 🔔 Bypass checkbox
snel_verkocht = st.sidebar.checkbox("⚡ Snel invoeren als verkocht (historisch)")

verkoopdatum = None
verkoopprijs_eur = 0.0
verkoopprijs = 0
wordt_gesplitst = False

aankoopdatum = st.sidebar.date_input("🗕️ Aankoopdatum", value=date.today())

# 👇 KEUZEVAK VOOR EUR / GMD
invoer_valuta = st.sidebar.radio("Valuta aankoopprijs", ["EUR", "GMD"], horizontal=True)

valutasymbool = "€" if invoer_valuta == "EUR" else "GMD"
col1, col2 = st.sidebar.columns([1, 4])
col1.markdown(f"**{valutasymbool}**")
invoerwaarde = col2.number_input(
    "Aankoopprijs", label_visibility="collapsed",
    min_value=0.0, format="%.2f", value=0.0
)

if invoer_valuta == "EUR":
    aankoopprijs_eur = invoerwaarde
    if wisselkoers:
        aankoopprijs = round(aankoopprijs_eur * wisselkoers)
        st.sidebar.info(f"≈ {format_currency(aankoopprijs, 'GMD')} (koers: {wisselkoers:.2f})")
    else:
        aankoopprijs = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")
else:
    aankoopprijs = invoerwaarde
    if wisselkoers:
        aankoopprijs_eur = round(aankoopprijs / wisselkoers, 2)
        st.sidebar.info(f"≈ {format_currency(aankoopprijs_eur, 'EUR')} (koers: {wisselkoers:.2f})")
    else:
        aankoopprijs_eur = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — EUR niet omgerekend.")


if snel_verkocht:
    st.sidebar.markdown("### 💰 Verkoopgegevens (historisch)")
    verkoopdatum = st.sidebar.date_input("🗓️ Verkoopdatum", value=date.today())
    verkoopprijs_eur = st.sidebar.number_input("💶 Verkoopprijs (EUR)", min_value=0.0, format="%.2f", value=0.0)
    if wisselkoers:
        verkoopprijs = round(verkoopprijs_eur * wisselkoers)
        st.sidebar.info(f"≈ {format_currency(verkoopprijs, 'GMD')} (koers: {wisselkoers:.2f})")
    else:
        verkoopprijs = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")
else:
    verkoopdatum = None
    verkoopprijs = 0
    verkoopprijs_eur = 0.0


# 🎯 Nieuw: Strategie en planning
st.sidebar.markdown("### 🧭 Strategie en planning")
strategieopties = [
    "Korte termijn verkoop",
    "Verkavelen en verkopen",
    "Zelf woningen bouwen",
    "Zelf bedrijf starten",
    "Nog onbekend"
]

strategie = st.sidebar.selectbox("Doel met dit perceel", strategieopties)

if strategie == "Verkavelen en verkopen":
    # Nieuw veld: start verkooptraject
    start_traject = st.sidebar.date_input(
        "🗓️ Start verkooptraject",
        value=date.today(),
        key="start_verkooptraject_sidebar"
    )

    aantal_kavels = st.sidebar.number_input(
        "Aantal kavels",
        min_value=1,
        value=1,
        key="sidebar_aantal_kavels"
    )

    valuta_keuze_sidebar = st.sidebar.radio(
        "Valuta prijs per kavel",
        ["EUR", "GMD"],
        horizontal=True,
        key="valuta_kavel_sidebar"
    )

    if valuta_keuze_sidebar == "EUR":
        prijs_per_kavel_eur = st.sidebar.number_input(
            "Prijs per kavel (EUR)",
            min_value=0.0,
            format="%.2f",
            value=0.0,
            key="prijs_per_plot_eur_sidebar"
        )
        prijs_per_kavel_gmd = round(prijs_per_kavel_eur * wisselkoers) if wisselkoers else 0.0
    else:
        prijs_per_kavel_gmd = st.sidebar.number_input(
            "Prijs per kavel (GMD)",
            min_value=0.0,
            format="%.0f",
            value=0.0,
            key="prijs_per_plot_gmd_sidebar"
        )
        prijs_per_kavel_eur = round(prijs_per_kavel_gmd / wisselkoers, 2) if wisselkoers else 0.0

    verkoopperiode_maanden = st.sidebar.number_input(
        "Verkoopperiode (maanden)",
        min_value=1,
        value=12,
        key="periode_sidebar"
    )

    # Berekeningen
    totaal_opbrengst_gmd = aantal_kavels * prijs_per_kavel_gmd
    totaal_opbrengst_eur = aantal_kavels * prijs_per_kavel_eur
    opbrengst_per_maand_gmd = totaal_opbrengst_gmd / verkoopperiode_maanden if verkoopperiode_maanden else 0
    opbrengst_per_maand_eur = totaal_opbrengst_eur / verkoopperiode_maanden if verkoopperiode_maanden else 0

    st.sidebar.info(
        f"💶 Totale opbrengst: {format_currency(totaal_opbrengst_eur, 'EUR')} "
        f"≈ {format_currency(totaal_opbrengst_gmd, 'GMD')}"
    )
    st.sidebar.info(
        f"📅 Opbrengst per maand: {format_currency(opbrengst_per_maand_eur, 'EUR')} "
        f"≈ {format_currency(opbrengst_per_maand_gmd, 'GMD')}"
    )

else:
    start_traject = None
    aantal_kavels = None
    prijs_per_kavel_eur = 0.0
    prijs_per_kavel_gmd = 0.0
    verkoopperiode_maanden = None
    totaal_opbrengst_eur = 0.0
    totaal_opbrengst_gmd = 0.0
    opbrengst_per_maand_eur = 0.0
    opbrengst_per_maand_gmd = 0.0


# Deze velden blijven altijd
verwachte_opbrengst = st.sidebar.number_input("Verwachte opbrengst (EUR)", min_value=0.0, format="%.2f", value=0.0)
kosten_qg = st.sidebar.number_input("Verwachte kosten Quadraat Global (EUR)", min_value=0.0, format="%.2f", value=0.0)
kosten_extern = st.sidebar.number_input("Verwachte kosten Externen (EUR)", min_value=0.0, format="%.2f", value=0.0)
verwachte_kosten = kosten_qg + kosten_extern
st.sidebar.info(f"Totaal verwachte kosten: € {verwachte_kosten:,.2f}")

doorlooptijd_datum = st.sidebar.date_input("Verwachte einddatum", value=date.today())
status_toelichting = st.sidebar.text_area("Status / toelichting", value="")


# 👥 Investeerders
st.sidebar.markdown("### 👥 Investeerders")

financieringsvorm = st.sidebar.radio(
    "Financieringsvorm",
    ["Eigen beheer (geen externe investeerders)", "Met externe investeerders"],
    help="Kies ‘Eigen beheer’ als dit perceel 100% intern gefinancierd is."
)

inv_input_data = st.session_state.get("investeerders_input", [])
investeerders = []

if financieringsvorm.startswith("Eigen beheer"):
    st.sidebar.info("ℹ️ Dit perceel staat in **eigen beheer** (geen externe investeerders).")
    investeerders = []  # lege lijst in data
else:
    aantal_investeerders = st.sidebar.number_input(
        "Aantal externe investeerders",
        min_value=1, max_value=10, value=1
    )

    for i in range(1, aantal_investeerders + 1):
        st.sidebar.markdown(f"#### Investeerder {i}")
        inv_data = inv_input_data[i - 1] if i <= len(inv_input_data) else {}
        naam = st.sidebar.text_input(f"Naam {i}", value=inv_data.get("naam", ""), key=f"inv_naam_{i}")
        bedrag_eur = st.sidebar.number_input(
            f"Bedrag {i} (EUR)", min_value=0.0, format="%.2f",
            value=inv_data.get("bedrag_eur", 0.0), key=f"inv_bedrag_eur_{i}"
        )
        bedrag = round(bedrag_eur * wisselkoers) if wisselkoers else 0
        rente = st.sidebar.number_input(
            f"Rente {i} (%)", min_value=0.0, max_value=100.0, step=0.1,
            value=inv_data.get("rente", 0.0) * 100, key=f"inv_rente_{i}"
        ) / 100
        winst = st.sidebar.number_input(
            f"Winstdeling {i} (%)", min_value=0.0, max_value=100.0, step=1.0,
            value=inv_data.get("winstdeling", 0.0) * 100, key=f"inv_winst_{i}"
        ) / 100
        rentetype = st.sidebar.selectbox(
            f"Rentevorm {i}", ["maandelijks", "jaarlijks", "bij verkoop"],
            index=["maandelijks", "jaarlijks", "bij verkoop"].index(inv_data.get("rentetype", "bij verkoop")),
            key=f"inv_rentetype_{i}"
        )

        if naam and bedrag > 0:
            investeerders.append({
                "naam": naam,
                "bedrag": bedrag,
                "bedrag_eur": bedrag_eur,
                "rente": rente,
                "winstdeling": winst,
                "rentetype": rentetype
            })

st.session_state["investeerders_input"] = inv_input_data

# Documenten
st.sidebar.markdown("### 📋 Documenten")
eigendomstype = st.sidebar.selectbox(
    "Eigendomsvorm",
    ["Geregistreerd land"],  # samengevoegd
    index=0
)
st.sidebar.caption("ℹ️ Zowel *Customary land* als *Freehold land* worden in Gambia na registratie gelijk behandeld.")

def get_vereiste_documenten(perceel: dict = None, fase: str = None) -> list:
    """Haal de vereiste documenten op voor een perceel en fase.
    Als alleen fase wordt meegegeven, gebruik die.
    """
    if not fase:
        fase = perceel.get("dealstage", "Aankoop") if perceel else "Aankoop"

    docs = documentvereisten_per_fase.get(fase, []).copy()

    return docs

if snel_verkocht:
    docs_sidebar = []
else:
    docs_sidebar = get_vereiste_documenten(fase="Aankoop")

uploads = {}
uploads_urls = {}

for doc in docs_sidebar:
    col1, col2 = st.sidebar.columns([1, 2])

    with col1:
        uploads[doc] = st.checkbox(f"{doc} aanwezig?", value=False)

    with col2:
        uploads_urls[doc] = st.text_input(
            f"Link naar {doc}",
            key=f"url_{doc}",
            label_visibility="collapsed"
        )

    # Klikbare link tonen als aanwezig
    if uploads.get(doc) and uploads_urls.get(doc):
        st.sidebar.markdown(
            f"<a href='{uploads_urls[doc]}' target='_blank'>📄 Open {doc}</a>",
            unsafe_allow_html=True
        )

# 📍 Verzamel alle polygon-coördinaten
alle_coords = []
for perceel in st.session_state.get("percelen", []):
    polygon = perceel.get("polygon", [])
    if polygon and isinstance(polygon, list):
        for point in polygon:
            if isinstance(point, list) and len(point) == 2:
                alle_coords.append(point)

# 📌 Bepaal kaart-instelling met zoom op geselecteerde polygon (indien beschikbaar)
kaart_focus = st.session_state.get("kaart_focus_buffer")

if kaart_focus and isinstance(kaart_focus, list) and len(kaart_focus) >= 1:
    # Zoom op geselecteerde polygon
    m = folium.Map(
        tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google Hybrid"
    )
    m.fit_bounds(kaart_focus)
elif alle_coords:
    # Zoom op alle percelen
    min_lat = min(p[0] for p in alle_coords)
    max_lat = max(p[0] for p in alle_coords)
    min_lon = min(p[1] for p in alle_coords)
    max_lon = max(p[1] for p in alle_coords)
    bounds = [[min_lat, min_lon], [max_lat, max_lon]]
    m = folium.Map(
        tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google Hybrid"
    )
    m.fit_bounds(bounds)
else:
    # Fallback locatie
    m = folium.Map(
        location=[13.29583, -16.74694],
        zoom_start=18,
        tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google Hybrid"
    )

# ➕ Teken-tool toevoegen
Draw(export=False).add_to(m)


# Toon bestaande percelen, met check voor correcte dict structuur
for perceel in st.session_state.percelen:
    perceel.setdefault("uploads_urls", {})

for perceel in st.session_state.percelen:
    if not isinstance(perceel, dict):
        st.warning(f"Percel is geen dict maar {type(perceel)}, wordt overgeslagen: {perceel}")
        continue

    polygon = perceel.get("polygon")
    tooltip = perceel.get("locatie", "Onbekend")

    # Investeerders weergeven
    investeerders = ", ".join(
        i.get('naam') if isinstance(i, dict) else str(i)
        for i in perceel.get('investeerders', [])
    ) or "Geen"

    # Documenten weergeven (met links indien beschikbaar)
    documenten_links = []
    for doc, aanwezig in perceel.get("uploads", {}).items():
        if aanwezig:
            url = perceel.get("uploads_urls", {}).get(doc, "")
            if url:
                documenten_links.append(f'<a href="{url}" target="_blank">{doc}</a>')
            else:
                documenten_links.append(doc)
    documenten = ", ".join(documenten_links) or "Geen"

    # HTML voor popup met kleiner lettertype en scroll bij veel tekst
    popup_html = f"""
    <div style="font-size: 12px; line-height: 1.35; max-width: 260px; max-height: 300px; overflow-y: auto;">
        <b>📍 Locatie:</b> {perceel.get('locatie', 'Onbekend')}<br>
        <b>🗓️ Aankoopdatum:</b> {format_date_eu(perceel.get('aankoopdatum'))}<br>
        <b>💰 Aankoopprijs:</b> {format_currency(perceel.get('aankoopprijs', 0), 'GMD')}<br>
        <b>💶 Aankoopprijs (EUR):</b> {format_currency(perceel.get('aankoopprijs_eur', 0.0), 'EUR')}<br>
        <b>🔖 Dealstage:</b> {perceel.get('dealstage', 'Onbekend')}<br>
        <b>🏷️ Eigendom:</b> {perceel.get('eigendomstype', 'Onbekend')}<br>
        <b>🔹 Wordt gesplitst:</b> {'Ja' if perceel.get('wordt_gesplitst') else 'Nee'}<br>
        <b>👥 Investeerders:</b> {investeerders}<br>
        <b>📄 Documenten aanwezig:</b> {documenten}<br>
        <b>🎯 Strategie:</b> {perceel.get('strategie', 'n.v.t.')}<br>
        <b>⏳ Doorlooptijd:</b> {format_date_eu(perceel.get('doorlooptijd'))}<br>
        <b>📈 Verwachte opbrengst:</b> {format_currency(perceel.get('verwachte_opbrengst_eur', 0.0), 'EUR')}<br>
        <b>💸 Verwachte kosten:</b> {format_currency(perceel.get('verwachte_kosten_eur', 0.0), 'EUR')}<br>
        <b>📝 Status / toelichting:</b> {perceel.get('status_toelichting', '—')}
    </div>
    """

    # Polygon of marker toevoegen
    if polygon and isinstance(polygon, list):
        polygon_converted = [
            [lat, lon] for lat, lon in polygon if isinstance([lat, lon], list) and len([lat, lon]) == 2
        ]

        if len(polygon_converted) >= 3:
            folium.Polygon(
                locations=polygon_converted,
                color="blue",
                fill=True,
                fill_opacity=0.5,
                tooltip=tooltip,
                popup=folium.Popup(popup_html, max_width=300)
            ).add_to(m)
        elif len(polygon_converted) == 1:
            folium.Marker(
                location=polygon_converted[0],
                tooltip=tooltip,
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(m)

# Viewer container
with st.container():
    output = st_folium(m, width=1000, height=500)

    if output and output.get("last_object_clicked_tooltip"):
        st.session_state["active_locatie"] = output["last_object_clicked_tooltip"]
        for perceel in st.session_state["percelen"]:
            if perceel.get("locatie") == st.session_state["active_locatie"]:
                st.session_state["kaart_focus_buffer"] = perceel.get("polygon")
                break

    st.markdown("", unsafe_allow_html=True)


# 🔄 Actiebalk Undo & Reload
col_undo, col_reload = st.columns(2)

with col_undo:
    if st.button("↩ Undo laatste wijziging", key="undo_main", use_container_width=True):
        undo()
        st.rerun()

with col_reload:
    if st.button("📤 Percelen opnieuw laden", key="reload_main", use_container_width=True):
        st.session_state["percelen"] = load_percelen_from_json()
        st.success("Percelen zijn opnieuw geladen.")
        st.session_state.pop("skip_load", None)
        st.rerun()
 
   
# --- 📍 Perceel selectie: strakke uitlijning in rijen ---
percelen = st.session_state.get("percelen", [])
locaties = [p.get("locatie", f"Perceel {i+1}") for i, p in enumerate(percelen)]

if "active_locatie" not in st.session_state and locaties:
    st.session_state["active_locatie"] = locaties[0]

st.markdown("""
<style>
.selector .row { margin-bottom: .5rem; }
.selector .stButton>button {
  width: 100%;
  border-radius: 9999px;
  border: 1px solid #E5E7EB;
  padding: 10px 14px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
  background: #ffffff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.selector .stButton>button:hover { border-color: #00b39420; box-shadow: 0 2px 6px rgba(0,0,0,.08); }
.selector .active>button { background: #E8FFF7; border-color: #00b39466; }
</style>
""", unsafe_allow_html=True)

st.markdown("### 📍 Kies perceel")
st.markdown("<div class='selector'>", unsafe_allow_html=True)

def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

per_row = 3   # zet op 4 als je 4 kolommen wilt
for rij in chunk(locaties, per_row):
    cols = st.columns(per_row)
    st.markdown("<div class='row'></div>", unsafe_allow_html=True)
    for col, loc in zip(cols, rij):
        is_active = (st.session_state.get("active_locatie") == loc)
        with col:
            if is_active:
                st.markdown("<div class='active'>", unsafe_allow_html=True)
            label = f"✅ {loc}" if is_active else f"📍 {loc}"
            if st.button(label, key=f"btn_{loc}", use_container_width=True):
                st.session_state["active_locatie"] = loc
                st.rerun()
            if is_active:
                st.markdown("</div>", unsafe_allow_html=True)

st.markdown("</div>", unsafe_allow_html=True)

keuze = st.session_state.get("active_locatie")

# --- Toon alleen het geselecteerde perceel ---
for i, perceel in enumerate(percelen):
    if not isinstance(perceel, dict):
        st.warning(f"Percel op index {i} is ongeldig en wordt overgeslagen.")
        continue

    # Zorg dat deze keys altijd bestaan
    perceel.setdefault("uploads", {})
    perceel.setdefault("uploads_urls", {})

    # Alleen het gekozen perceel tonen
    if perceel.get("locatie") != keuze:
        continue

    huidige_fase = perceel.get("dealstage", "Aankoop")

    with st.expander(f"📍 {perceel.get('locatie', f'Perceel {i+1}')}", expanded=True):
        st.text_input("Locatie", value=perceel.get("locatie", ""), key=f"edit_locatie_{i}", disabled=True)

        # 👁️ Zoom-knop
        if st.button(f"🔍 Zoom in op {perceel.get('locatie')}", key=f"zoom_knop_{i}"):
            st.session_state["kaart_focus_buffer"] = perceel.get("polygon")
            st.rerun()

        # 🏷️ Snelle indicatie financieringsvorm
        _heeft_extern = bool(perceel.get("investeerders"))
        if not _heeft_extern:
            st.info("ℹ️ Dit perceel staat in **eigen beheer** (geen externe investeerders).")
        else:
            st.warning("ℹ️ Dit perceel heeft **externe investeerders**.")

        # Alleen bij fase Verkoop zichtbaar; anders geforceerd False
        perceel["wordt_gesplitst"] = (
            st.checkbox(
                "Wordt perceel gesplitst?",
                value=perceel.get("wordt_gesplitst", False),
                key=f"wordt_gesplitst_{i}",
            )
            if huidige_fase == "Verkoop"
            else False
        )

        # Afmetingen
        perceel["lengte"] = st.number_input("📏 Lengte (m)", min_value=0, value=int(perceel.get("lengte", 0)), key=f"edit_lengte_{i}")
        perceel["breedte"] = st.number_input("📐 Breedte (m)", min_value=0, value=int(perceel.get("breedte", 0)), key=f"edit_breedte_{i}")

        # Eigendomstype
        perceel["eigendomstype"] = st.selectbox("Eigendomsvorm", ["Geregistreerd land"], index=0, key=f"eigendom_{i}")
        st.caption("ℹ️ Zowel *Customary land* als *Freehold land* worden in Gambia na registratie gelijk behandeld.")

        # 🗓️ Aankoopdatum
        aankoopdatum_raw = perceel.get("aankoopdatum")
        try:
            aankoopdatum_dt = pd.to_datetime(aankoopdatum_raw, errors="coerce")
            aankoopdatum_value = aankoopdatum_dt.date() if pd.notnull(aankoopdatum_dt) else date.today()
        except Exception:
            aankoopdatum_value = date.today()
        perceel["aankoopdatum"] = st.date_input("🗕️ Aankoopdatum", value=aankoopdatum_value, key=f"aankoopdatum_{i}").isoformat()

        # 👥 Investeerdersbeheer
        st.markdown("#### 👥 Investeerders")
        huidige_investeerders = perceel.get("investeerders", []) or []
        nieuwe_investeerders = []

        if not huidige_investeerders:
            st.info("Geen investeerders geregistreerd voor dit perceel.")
        else:
            for j, inv in enumerate(huidige_investeerders):
                st.markdown(f"##### Investeerder {j+1}")
                naam = st.text_input(f"Naam investeerder {j+1}", value=inv.get("naam", ""), key=f"inv_naam_edit_{i}_{j}")
                bedrag_eur = st.number_input(
                    f"Bedrag {j+1} (EUR)",
                    min_value=0.0,
                    format="%.2f",
                    value=float(inv.get("bedrag_eur", 0.0)),
                    key=f"inv_bedrag_edit_{i}_{j}",
                )
                rente = (
                    st.number_input(
                        f"Rente {j+1} (%)",
                        min_value=0.0,
                        max_value=100.0,
                        step=0.1,
                        value=float(inv.get("rente", 0.0)) * 100,
                        key=f"inv_rente_edit_{i}_{j}",
                    )
                    / 100
                )
                winst = (
                    st.number_input(
                        f"Winstdeling {j+1} (%)",
                        min_value=0.0,
                        max_value=100.0,
                        step=1.0,
                        value=float(inv.get("winstdeling", 0.0)) * 100,
                        key=f"inv_winst_edit_{i}_{j}",
                    )
                    / 100
                )
                rentetype = st.selectbox(
                    f"Rentevorm {j+1}",
                    ["maandelijks", "jaarlijks", "bij verkoop"],
                    index=["maandelijks", "jaarlijks", "bij verkoop"].index(inv.get("rentetype", "bij verkoop")),
                    key=f"inv_type_edit_{i}_{j}",
                )

                _wissel = perceel.get("wisselkoers") or locals().get("wisselkoers", None)
                nieuwe_investeerders.append(
                    {
                        "naam": naam,
                        "bedrag": round(bedrag_eur * (_wissel or 0)),
                        "bedrag_eur": bedrag_eur,
                        "rente": rente,
                        "winstdeling": winst,
                        "rentetype": rentetype,
                    }
                )
        perceel["investeerders"] = nieuwe_investeerders

        # 📋 Documenten
        st.markdown("#### 📋 Documenten")
        vereiste_docs = get_vereiste_documenten(perceel, huidige_fase)
        nieuwe_uploads, nieuwe_uploads_urls = {}, {}

        for doc in vereiste_docs:
            col1, col2 = st.columns([1, 3])
            with col1:
                nieuwe_uploads[doc] = st.checkbox(
                    f"{doc} aanwezig?", value=perceel.get("uploads", {}).get(doc, False), key=f"upload_{i}_{doc}"
                )
            with col2:
                nieuwe_uploads_urls[doc] = st.text_input(
                    f"Link naar {doc}", value=perceel.get("uploads_urls", {}).get(doc, ""), key=f"upload_url_{i}_{doc}"
                )
                if nieuwe_uploads[doc] and nieuwe_uploads_urls[doc]:
                    st.markdown(
                        f"<a href='{nieuwe_uploads_urls[doc]}' target='_blank'>📄 Open {doc}</a>",
                        unsafe_allow_html=True,
                    )

        perceel["uploads"] = nieuwe_uploads
        perceel["uploads_urls"] = nieuwe_uploads_urls

        # 📌 Huidige fase + navigatie
        st.markdown(render_pipeline(huidige_fase))
        _PIPELINE_FASEN = ["Aankoop", "Omzetting / bewerking", "Verkoop", "Verkocht"]
        fase_index = _PIPELINE_FASEN.index(huidige_fase) if huidige_fase in _PIPELINE_FASEN else 0

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            if fase_index > 0:
                vorige_fase = _PIPELINE_FASEN[fase_index - 1]
                if st.button(f"⬅️ Vorige fase ({vorige_fase})", key=f"vorige_fase_{i}"):
                    perceel["dealstage"] = vorige_fase
                    save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                    st.session_state["skip_load"] = True
                    st.rerun()

        with col_f2:
            if fase_index < len(_PIPELINE_FASEN) - 1:
                volgende_fase = _PIPELINE_FASEN[fase_index + 1]
                if st.button(f"➡️ Volgende fase ({volgende_fase})", key=f"volgende_fase_{i}"):
                    perceel["dealstage"] = volgende_fase
                    save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                    st.session_state["skip_load"] = True
                    st.rerun()

        # 🟩 Extra invoer wanneer fase = Verkocht
        if huidige_fase == "Verkocht":
            st.markdown("#### 💰 Verkoopgegevens (gerealiseerd)")
            try:
                verkoopdt = pd.to_datetime(perceel.get("verkoopdatum"), errors="coerce")
                verkoop_value = verkoopdt.date() if pd.notnull(verkoopdt) else date.today()
            except Exception:
                verkoop_value = date.today()
            perceel["verkoopdatum"] = st.date_input("🗓️ Verkoopdatum", value=verkoop_value, key=f"verkoopdatum_{i}").isoformat()

            _koers = perceel.get("wisselkoers") or locals().get("wisselkoers", None)
            valuta_keuze_verkocht = st.radio("Valuta verkoopprijs", ["EUR", "GMD"], horizontal=True, key=f"valuta_verkoop_{i}")

            if valuta_keuze_verkocht == "EUR":
                prijs_eur = st.number_input(
                    "Verkoopprijs (EUR)",
                    min_value=0.0,
                    value=float(perceel.get("verkoopprijs_eur", 0.0)) if perceel.get("verkoopprijs_eur") else 0.0,
                    format="%.2f",
                    key=f"verkoopprijs_eur_{i}",
                )
                prijs_gmd = round(prijs_eur * _koers) if _koers else float(perceel.get("verkoopprijs", 0.0) or 0.0)
            else:
                prijs_gmd = st.number_input(
                    "Verkoopprijs (GMD)",
                    min_value=0.0,
                    value=float(perceel.get("verkoopprijs", 0.0) or 0.0),
                    format="%.0f",
                    key=f"verkoopprijs_gmd_{i}",
                )
                prijs_eur = round(prijs_gmd / _koers, 2) if _koers else float(perceel.get("verkoopprijs_eur", 0.0) or 0.0)

            perceel["verkoopprijs"] = prijs_gmd
            perceel["verkoopprijs_eur"] = prijs_eur

            if _koers:
                st.info(f"✅ Vastgelegd: {format_currency(prijs_eur, 'EUR')} ≈ {format_currency(prijs_gmd, 'GMD')} (koers {_koers:.2f})")
            else:
                st.info("✅ Vastgelegd (koers onbekend): bedragen niet omgerekend.")

        # 🌟 Strategie en planning
        st.markdown("#### 🌟 Strategie en planning")
        strategie_opties = ["Korte termijn verkoop", "Verkavelen en verkopen", "Zelf woningen bouwen", "Zelf bedrijf starten"]
        perceel["strategie"] = st.selectbox(
            "Strategie",
            strategie_opties,
            index=strategie_opties.index(perceel.get("strategie", "Korte termijn verkoop")),
            key=f"strategie_{i}",
        )

        if perceel["strategie"] == "Verkavelen en verkopen":
            perceel["start_verkooptraject"] = st.date_input(
                "🗓️ Start verkooptraject",
                value=pd.to_datetime(perceel.get("start_verkooptraject"), errors="coerce").date()
                if perceel.get("start_verkooptraject")
                else date.today(),
                key=f"start_verkooptraject_{i}",
            ).isoformat()

            perceel["aantal_plots"] = st.number_input(
                "Aantal kavels", min_value=1, value=int(perceel.get("aantal_plots", 1)), key=f"aantal_plots_{i}"
            )

            valuta_keuze = st.radio("Valuta invoer voor prijs per kavel", ["EUR", "GMD"], horizontal=True, key=f"valuta_kavel_{i}")
            if valuta_keuze == "EUR":
                prijs_eur = st.number_input(
                    "Prijs per kavel (EUR)",
                    min_value=0.0,
                    value=float(perceel.get("prijs_per_plot_eur", 0.0)),
                    format="%.2f",
                    key=f"prijs_plot_eur_{i}",
                )
                prijs_gmd = round(prijs_eur * (locals().get("wisselkoers") or perceel.get("wisselkoers") or 0))
            else:
                prijs_gmd = st.number_input(
                    "Prijs per kavel (GMD)",
                    min_value=0.0,
                    value=float(perceel.get("prijs_per_plot_gmd", 0.0)),
                    format="%.0f",
                    key=f"prijs_plot_gmd_{i}",
                )
                _k = (locals().get("wisselkoers") or perceel.get("wisselkoers") or 0) or None
                prijs_eur = round(prijs_gmd / _k, 2) if _k else 0.0

            perceel["prijs_per_plot_eur"], perceel["prijs_per_plot_gmd"] = prijs_eur, prijs_gmd

            doorlooptijd = pd.to_datetime(perceel.get("doorlooptijd"), errors="coerce")
            vandaag = date.today()
            if pd.notnull(doorlooptijd):
                delta = (doorlooptijd.year - vandaag.year) * 12 + (doorlooptijd.month - vandaag.month)
                verkoopperiode_maanden = max(delta, 1)
            else:
                verkoopperiode_maanden = 12
            perceel["verkoopperiode_maanden"] = verkoopperiode_maanden

            totaal_opbrengst_gmd = perceel["aantal_plots"] * prijs_gmd
            totaal_opbrengst_eur = perceel["aantal_plots"] * prijs_eur
            perceel["totaal_opbrengst_gmd"], perceel["totaal_opbrengst_eur"] = totaal_opbrengst_gmd, totaal_opbrengst_eur
            perceel["opbrengst_per_maand_gmd"] = totaal_opbrengst_gmd / verkoopperiode_maanden
            perceel["opbrengst_per_maand_eur"] = totaal_opbrengst_eur / verkoopperiode_maanden

            st.info(f"💶 Totale opbrengst: {format_currency(totaal_opbrengst_eur, 'EUR')} ≈ {format_currency(totaal_opbrengst_gmd, 'GMD')}")
            st.info(f"📅 Opbrengst per maand: {format_currency(perceel['opbrengst_per_maand_eur'], 'EUR')} ≈ {format_currency(perceel['opbrengst_per_maand_gmd'], 'GMD')}")
        else:
            perceel["verwachte_opbrengst_eur"] = st.number_input(
                "Verwachte opbrengst (EUR)",
                min_value=0.0,
                value=float(perceel.get("verwachte_opbrengst_eur", 0.0)),
                format="%.2f",
                key=f"verwachte_opbrengst_{i}",
            )
            perceel["verwachte_kosten_eur"] = st.number_input(
                "Verwachte kosten (EUR)",
                min_value=0.0,
                value=float(perceel.get("verwachte_kosten_eur", 0.0)),
                format="%.2f",
                key=f"verwachte_kosten_{i}",
            )

        # Verwachte einddatum
        perceel["doorlooptijd"] = st.date_input(
            "Verwachte einddatum",
            value=pd.to_datetime(perceel.get("doorlooptijd"), errors="coerce").date() if perceel.get("doorlooptijd") else date.today(),
            key=f"doorlooptijd_{i}",
        ).isoformat()

        # Status / toelichting
        perceel["status_toelichting"] = st.text_area("📜 Status / toelichting", value=perceel.get("status_toelichting", ""), key=f"status_toelichting_{i}")

        # 🔐 Opslaan + Verwijderen (zonder <a>, met confirm in state)
        if is_admin:
            col1, col2 = st.columns([3, 1])

            with col1:
                if st.button(f"💾 Opslaan wijzigingen ({perceel.get('locatie')})", key=f"opslaan_bewerken_{i}"):
                    save_state()
                    save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                    st.cache_data.clear()
                    st.success(f"Wijzigingen aan {perceel.get('locatie')} opgeslagen.")

            with col2:
                confirm_key = f"confirm_delete_{i}"
                if not st.session_state.get(confirm_key, False):
                    if st.button("🗑 Verwijder", key=f"delete_{i}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.error("⚠️ Weet je zeker dat je dit perceel wilt verwijderen? Dit kan niet ongedaan gemaakt worden.")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("✅ Ja, definitief verwijderen", key=f"do_delete_{i}"):
                            save_state()
                            st.session_state["percelen"].pop(i)
                            save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                            st.session_state.pop(confirm_key, None)
                            st.success("Perceel verwijderd.")
                            st.rerun()
                    with c2:
                        if st.button("↩ Nee, annuleren", key=f"cancel_delete_{i}"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
        else:
            st.info("🔐 Alleen admins kunnen wijzigingen opslaan of percelen verwijderen.")

# Coördinaten invoer (UTM of Lat/Lon)
st.sidebar.markdown("### 📍 Coördinaten invoer")
coord_type = st.sidebar.radio("Coördinatentype", ["UTM", "Latitude/Longitude"], index=1)

polygon_coords = []
for idx in range(1, 6):
    col1, col2 = st.sidebar.columns(2)
    x = col1.text_input(f"X{idx}", key=f"x_{idx}")
    y = col2.text_input(f"Y{idx}", key=f"y_{idx}")
    try:
        if coord_type == "UTM":
            transformer = Transformer.from_crs("epsg:32628", "epsg:4326", always_xy=True)
            lon, lat = transformer.transform(float(x), float(y))
        else:
            lat = float(y)
            lon = float(x)
        if lat and lon:
            polygon_coords.append([lat, lon])
    except Exception:
        pass

# Bij minder dan 3 punten is polygon ongeldig
if len(polygon_coords) > 0 and len(polygon_coords) < 3:
    st.sidebar.error("❗ Polygon moet minstens 3 punten bevatten.")

# Overschrijf polygon_coords alleen als er getekend is in Folium
if output := st.session_state.get("output", None):
    last_drawing = output.get("last_active_drawing", None)
    if last_drawing:
        geom = last_drawing.get("geometry", {})
        if geom.get("type") == "Polygon":
            coords = geom.get("coordinates", [[]])[0]
            polygon_coords = [[c[1], c[0]] for c in coords]

if is_admin:
    if st.sidebar.button("🧹 Migratie uitvoeren (eenmalig)"):
        migrate_percelen() 

if is_admin:
    toevoegen = st.sidebar.button("➕ Voeg perceel toe")
else:
    st.sidebar.info("🔒 Alleen admins kunnen percelen toevoegen.")
    toevoegen = False  # Zet toevoegen uit voor niet-admins

if is_admin and toevoegen:
    save_state()  

    # --- Validatie ---
    if not locatie:
        st.sidebar.error("❗ Vul een locatie in.")
    elif any(p.get("locatie") == locatie for p in st.session_state["percelen"]):
        st.sidebar.warning("⚠️ Er bestaat al een perceel met deze locatie.")
    elif financieringsvorm.startswith("Met externe") and not investeerders and not snel_verkocht:
        st.sidebar.error("❗ Voeg minimaal één externe investeerder toe óf kies ‘Eigen beheer’.")
    elif len(polygon_coords) < 3:
        st.sidebar.error("❗ Polygon moet minstens 3 punten bevatten.")
    else:
        # 🔔 Dealstage bepalen vóór aanmaken
        dealstage = "Verkoop" if snel_verkocht else "Aankoop"

        # 🛡️ Safeguard: investeerders normaliseren
        if isinstance(investeerders, str):
            investeerders = [{
                "naam": investeerders,
                "bedrag": 0,
                "bedrag_eur": 0,
                "rente": 0.0,
                "winstdeling": 0.0,
                "rentetype": "bij verkoop"
            }]

        # 🔹 Strategie-specifieke velden
        if strategie == "Verkavelen en verkopen":
            start_traject = st.session_state.get("start_verkooptraject_sidebar", date.today())
            aantal_kavels = st.session_state.get("sidebar_aantal_kavels", 1)
            prijs_per_plot_eur = st.session_state.get("prijs_per_plot_eur_sidebar", 0.0)
            prijs_per_plot_gmd = st.session_state.get("prijs_per_plot_gmd_sidebar", 0.0)
            verkoopperiode_maanden = st.session_state.get("periode_sidebar", 12)

            totaal_opbrengst_gmd = aantal_kavels * prijs_per_plot_gmd
            totaal_opbrengst_eur = aantal_kavels * prijs_per_plot_eur
            opbrengst_per_maand_gmd = totaal_opbrengst_gmd / verkoopperiode_maanden if verkoopperiode_maanden else 0
            opbrengst_per_maand_eur = totaal_opbrengst_eur / verkoopperiode_maanden if verkoopperiode_maanden else 0
        else:
            start_traject = None
            aantal_kavels = None
            prijs_per_plot_eur = 0.0
            prijs_per_plot_gmd = 0.0
            verkoopperiode_maanden = None
            totaal_opbrengst_eur = verwachte_opbrengst
            totaal_opbrengst_gmd = 0.0
            opbrengst_per_maand_eur = 0.0
            opbrengst_per_maand_gmd = 0.0

        # 💸 Kosten-splitsing (komt uit de sidebar – stap 1)
        # kosten_qg en kosten_extern heb je hierboven al gedefinieerd in de sidebar.
        verwachte_kosten = kosten_qg + kosten_extern

        # ✅ Netto verwachte winst berekenen
        verwachte_winst_eur = totaal_opbrengst_eur - verwachte_kosten - aankoopprijs_eur

        # 🧾 Perceel-object samenstellen
        perceel = {
            "locatie": locatie,
            "dealstage": dealstage,
            "wordt_gesplitst": False,

            # Financiering
            "investeerders": investeerders,

            # Basisgegevens
            "lengte": lengte,
            "breedte": breedte,
            "eigendomstype": eigendomstype,
            "polygon": polygon_coords,

            # Documenten
            "uploads": uploads,
            "uploads_urls": uploads_urls,

            # Datums & prijzen
            "aankoopdatum": aankoopdatum.strftime("%Y-%m-%d"),
            "verkoopdatum": verkoopdatum.strftime("%Y-%m-%d") if isinstance(verkoopdatum, date) else verkoopdatum,
            "aankoopprijs": aankoopprijs,
            "aankoopprijs_eur": aankoopprijs_eur,
            "wisselkoers": wisselkoers,
            "verkoopprijs": verkoopprijs,
            "verkoopprijs_eur": verkoopprijs_eur if wisselkoers else None,

            # 🎯 Strategie & planning
            "strategie": strategie,
            "verwachte_opbrengst_eur": totaal_opbrengst_eur,
            "kosten_qg_eur": kosten_qg,                 # <-- NIEUW
            "kosten_extern_eur": kosten_extern,         # <-- NIEUW
            "verwachte_kosten_eur": verwachte_kosten,   # som van beide
            "verwachte_winst_eur": verwachte_winst_eur,
            "doorlooptijd": doorlooptijd_datum.isoformat() if isinstance(doorlooptijd_datum, date) else "",
            "start_verkooptraject": start_traject.strftime("%Y-%m-%d") if isinstance(start_traject, date) else None,
            "status_toelichting": status_toelichting,

            # ➕ Verkavelen en verkopen (alleen relevant als strategie dat is)
            "aantal_plots": aantal_kavels,
            "prijs_per_plot_eur": prijs_per_plot_eur,
            "prijs_per_plot_gmd": prijs_per_plot_gmd,
            "verkoopperiode_maanden": verkoopperiode_maanden,
            "totaal_opbrengst_eur": totaal_opbrengst_eur,
            "totaal_opbrengst_gmd": totaal_opbrengst_gmd,
            "opbrengst_per_maand_eur": opbrengst_per_maand_eur,
            "opbrengst_per_maand_gmd": opbrengst_per_maand_gmd
        }

        # 🔐 Opslaan
        st.session_state.percelen.append(perceel)
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
        st.sidebar.success(f"Perceel '{locatie}' toegevoegd en opgeslagen.")

        st.session_state["skip_load"] = False
        st.cache_data.clear()
        st.rerun()
