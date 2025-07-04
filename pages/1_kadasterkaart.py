import streamlit as st


st.set_page_config(page_title="Kadasterkaart", layout="wide")

st.image("QG.png", width=180)


import pandas as pd
import json
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw
from datetime import date
from utils import get_exchange_rate_eur_to_gmd, save_percelen_as_json, load_percelen_from_json
from pyproj import Transformer
from utils import render_pipeline

# Pipelinefases en documentvereisten per fase
PIPELINE_FASEN = [
    "Oriëntatie",
    "In onderhandeling",
    "Te kopen",
    "Aangekocht",
    "Geregistreerd",
    "In beheer",
    "In verkoop",
    "Verkocht"
]

actiepunten_per_fase = {
    "Oriëntatie": ["Coördinaten controleren", "Betrouwbaarheidscheck"],
    "In onderhandeling": ["Prijsbespreking afronden"],
    "Te kopen": ["Documenten verzamelen", "Contract check"],
    "Aangekocht": ["Registratie voorbereiden"],
    "Geregistreerd": ["Controle eigendomsbewijs"],
    "In beheer": ["Beheersplan opstellen"],
    "In verkoop": ["Marketing starten"],
    "Verkocht": ["Overdrachtscontrole"]
}

documentvereisten_per_fase = {
    "In onderhandeling": ["ID-verkoper"],
    "Te kopen": ["Sale Agreement", "Sketch Plan"],
    "Aangekocht": ["Transfer of Ownership Form"],
    "Geregistreerd": ["Land Use Report", "Rates (grondbelasting) ontvangstbewijs"],
    "In verkoop": ["Split Plan"],
    "Verkocht": ["Verkoopcontract", "Nieuwe eigenaar ID"]
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
    st.experimental_rerun()

# Scroll naar boven bij laden
st.markdown("""
    <script>
        window.scrollTo(0, 0);
    </script>
""", unsafe_allow_html=True)

st.title("Interactieve Kadasterkaart")

# Laden percelen en validatie
if "percelen" not in st.session_state or not st.session_state.percelen:
    loaded = load_percelen_from_json()
    percelen_valid = []
    for i, p in enumerate(loaded):
        if isinstance(p, dict):
            percelen_valid.append(p)
        else:
            st.warning(f"Percel index {i} is geen dict maar {type(p)}, wordt genegeerd.")
    st.session_state.percelen = percelen_valid

# Sidebar invoer velden voor nieuw perceel
st.sidebar.header("📝 Perceelinvoer")

locatie = st.sidebar.text_input("📍 locatie", placeholder="Bijv. Sanyang A", value="")
lengte = st.sidebar.number_input("📏 Lengte (m)", min_value=0, value=0)
breedte = st.sidebar.number_input("📐 Breedte (m)", min_value=0, value=0)
dealstage = st.sidebar.selectbox(
    "🔁 Pipelinefase",
    PIPELINE_FASEN,
    index=PIPELINE_FASEN.index("Oriëntatie")
)
wisselkoers = get_exchange_rate_eur_to_gmd()

verkoopdatum = None
verkoopprijs_eur = 0.0
verkoopprijs = 0

if dealstage == "Verkocht":
    verkoopdatum = st.sidebar.date_input("🗕️ Verkoopdatum", value=date.today())
    verkoopprijs_eur = st.sidebar.number_input("💶 Verkoopprijs (EUR)", min_value=0.0, format="%.2f", value=0.0)
    if wisselkoers:
        verkoopprijs = round(verkoopprijs_eur * wisselkoers)
        st.sidebar.info(f"≈ {verkoopprijs:,.0f} GMD (koers: {wisselkoers:.2f})")
    else:
        st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")
else:
    verkoopprijs = None
    verkoopprijs_eur = None
    verkoopdatum = None

aankoopdatum = st.sidebar.date_input("🗕️ Aankoopdatum", value=date.today())
aankoopprijs_eur = st.sidebar.number_input("💶 Aankoopprijs (EUR)", min_value=0.0, format="%.2f", value=0.0)
if wisselkoers:
    aankoopprijs = round(aankoopprijs_eur * wisselkoers)
    st.sidebar.info(f"≈ {aankoopprijs:,.0f} GMD (koers: {wisselkoers:.2f})")
else:
    aankoopprijs = 0
    st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")

# Investeerders
st.sidebar.markdown("### 👥 Investeerdersstructuur")
aantal_investeerders = st.sidebar.number_input("Aantal investeerders (0 = eigen beheer)", min_value=0, max_value=10, value=0)

inv_input_data = st.session_state.get("investeerders_input", [])
investeerders = []

if aantal_investeerders == 0:
    inv_input_data = [{
        "naam": "Eigen beheer",
        "bedrag_eur": aankoopprijs_eur,
        "rente": 0.0,
        "winstdeling": 1.0,
        "rentetype": "bij verkoop"
    }]
    investeerders = [{
        "naam": "Eigen beheer",
        "bedrag": aankoopprijs,
        "bedrag_eur": aankoopprijs_eur,
        "rente": 0.0,
        "winstdeling": 1.0,
        "rentetype": "bij verkoop"
    }]
else:
    for i in range(1, aantal_investeerders + 1):
        st.sidebar.markdown(f"#### Investeerder {i}")
        inv_data = inv_input_data[i - 1] if i <= len(inv_input_data) else {}
        naam = st.sidebar.text_input(f"Naam {i}", value=inv_data.get("naam", ""), key=f"inv_naam_{i}")
        bedrag_eur = st.sidebar.number_input(f"Bedrag {i} (EUR)", min_value=0.0, format="%.2f", value=inv_data.get("bedrag_eur", 0.0), key=f"inv_bedrag_eur_{i}")
        bedrag = round(bedrag_eur * wisselkoers) if wisselkoers else 0
        rente = st.sidebar.number_input(f"Rente {i} (%)", min_value=0.0, max_value=100.0, step=0.1, value=inv_data.get("rente", 0.0)*100, key=f"inv_rente_{i}") / 100
        winst = st.sidebar.number_input(f"Winstdeling {i} (%)", min_value=0.0, max_value=100.0, step=1.0, value=inv_data.get("winstdeling", 0.0)*100, key=f"inv_winst_{i}") / 100
        rentetype = st.sidebar.selectbox(f"Rentevorm {i}", ["maandelijks", "jaarlijks", "bij verkoop"], index=["maandelijks", "jaarlijks", "bij verkoop"].index(inv_data.get("rentetype", "maandelijks")), key=f"inv_rentetype_{i}")

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
eigendomstype = st.sidebar.selectbox("Eigendomsvorm", ["Customary land", "Freehold land"], index=["Customary land", "Freehold land"].index("Customary land"))

vereiste_docs = {
    "Freehold land": [
        "Title Deed of Lease Certificate",
        "Kadasteronderzoek (Title Search)",
        "Sale/Purchase Agreement",
        "Overdrachtsakte (Conveyance of Assignment)",
        "Stamp Duty",
        "Income Tax Clearance",
        "Registratie bij Land Registry",
        "Nieuwe Title Deed"
    ],
    "Customary land": [
        "Sale Agreement",
        "Transfer of Ownership Form",
        "Sketch Plan",
        "Land Use Report",
        "Rates (grondbelasting) ontvangstbewijs",
        "Toestemming mede-eigenaren",
        "ID-kaarten verkoper en koper"
    ]
}

uploads = {}
for doc in vereiste_docs[eigendomstype]:
    col1, col2 = st.sidebar.columns([1, 2])
    with col1:
        uploads[doc] = st.checkbox(f"{doc} aanwezig?", value=uploads.get(doc, False))
    with col2:
        st.file_uploader(f"Upload {doc}", key=doc)

# Folium map initialisatie met polygon tool
start_coords = [13.3085, -16.6800]
m = folium.Map(location=start_coords, zoom_start=18, tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", attr="Google Hybrid")
Draw(export=False).add_to(m)

# Toon bestaande percelen, met check voor correcte dict structuur
for perceel in st.session_state.percelen:
    if not isinstance(perceel, dict):
        st.warning(f"Percel is geen dict maar {type(perceel)}, wordt overgeslagen: {perceel}")
        continue

    polygon = perceel.get("polygon")
    tooltip = perceel.get("locatie", "Onbekend")
    popup_html = f"""
    <b>{tooltip}</b><br>
    Investeerders: {[i.get('naam') for i in perceel.get('investeerders', [])]}<br>
    Eigendom: {perceel.get('eigendomstype', 'onbekend')}<br>
    Documenten: {[doc for doc, aanwezig in perceel.get('uploads', {}).items() if aanwezig]}
    """
    if polygon and isinstance(polygon, list):
        if len(polygon) >= 3:
            folium.Polygon(
                locations=polygon,
                color="blue",
                fill=True,
                fill_opacity=0.5,
                tooltip=tooltip,
                popup=folium.Popup(popup_html, max_width=300)
            ).add_to(m)
        elif len(polygon) == 1:
            folium.Marker(
                location=polygon[0],
                tooltip=tooltip,
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(m)

with st.container():
    output = st_folium(m, width=1000, height=500)
    st.markdown("", unsafe_allow_html=True)

st.markdown("#### 🧭 Procesfase (Pipeline)")

# Dealstage selecteren
dealstage = st.selectbox(
    "Pipeline status",
    PIPELINE_FASEN,
    index=PIPELINE_FASEN.index("Oriëntatie"),
    key="dealstage_nieuw"  
)

# Visuele weergave
st.markdown(f"**Pipeline:** {render_pipeline(dealstage)}")


# Documenten per fase tonen
vereiste_fase_docs = documentvereisten_per_fase.get(dealstage, [])
if vereiste_fase_docs:
    st.markdown("**📄 Vereiste documenten in deze fase:**")
    for doc in vereiste_fase_docs:
        upload_status = "✅" if perceel.get("uploads", {}).get(doc) else "❌"
        st.write(f"{upload_status} {doc}")
else:
    st.info("Geen documenten vereist in deze fase.")


# Percelen beheer sectie
st.subheader("✏️ Beheer percelen")
col1, col2 = st.columns(2)
with col1:
    if st.button("💾 Percelen opslaan"):
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
        st.success("Percelen zijn opgeslagen naar Google Sheet.")
with col2:
    if st.button("📤 Percelen opnieuw laden"):
        st.session_state["percelen"] = load_percelen_from_json()
        st.success("Percelen zijn opnieuw geladen.")

# Perceel details bewerken
for i, perceel in enumerate(st.session_state.percelen):
    if not isinstance(perceel, dict):
        st.warning(f"Percel op index {i} is geen dict, wordt overgeslagen: {perceel}")
        continue

    naam = perceel.get("locatie", f"Perceel {i+1}")
    with st.expander(f"📍 {naam}", expanded=False):
        st.markdown(f"**🔁 Pipeline:** {render_pipeline(perceel.get('dealstage', 'Oriëntatie'), perceel.get('fase_status', {}))}")
        st.markdown("### 📋 Voortgang per pipelinefase")

        if "fase_status" not in perceel or not isinstance(perceel["fase_status"], dict):
            perceel["fase_status"] = {}

        for fase in PIPELINE_FASEN:
            voltooid = perceel["fase_status"].get(fase, False)
            perceel["fase_status"][fase] = st.checkbox(
                f"✅ {fase} afgerond?", value=voltooid, key=f"fase_{i}_{fase}"
            )
            actiepunten = actiepunten_per_fase.get(fase, [])
            for actie in actiepunten:
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;🔹 _{actie}_", unsafe_allow_html=True)

        # Perceelinformatie
        perceel["locatie"] = st.text_input("📍 Locatie", value=perceel.get("locatie", ""), key=f"locatie_{i}")
        perceel["lengte"] = st.number_input("📏 Lengte (m)", min_value=0, value=perceel.get("lengte", 0), key=f"lengte_{i}")
        perceel["breedte"] = st.number_input("📐 Breedte (m)", min_value=0, value=perceel.get("breedte", 0), key=f"breedte_{i}")
        perceel["dealstage"] = st.selectbox("🔁 Pipelinefase", PIPELINE_FASEN, index=PIPELINE_FASEN.index(perceel.get("dealstage", "Oriëntatie")), key=f"dealstage_edit_{i}")
        perceel["eigendomstype"] = st.selectbox("📁 Eigendomsvorm", ["Customary land", "Freehold land"], index=["Customary land", "Freehold land"].index(perceel.get("eigendomstype", "Customary land")), key=f"eigendom_{i}")
        perceel["aankoopdatum"] = st.date_input("📅 Aankoopdatum", value=pd.to_datetime(perceel.get("aankoopdatum", date.today())), key=f"aankoopdatum_{i}")
        perceel["aankoopprijs_eur"] = st.number_input("💶 Aankoopprijs (EUR)", min_value=0.0, format="%.2f", value=perceel.get("aankoopprijs_eur", 0.0) or 0.0, key=f"aankoopprijs_eur_{i}")
        perceel["aankoopprijs"] = round(perceel["aankoopprijs_eur"] * wisselkoers) if wisselkoers else 0

        if perceel["dealstage"] == "Verkocht":
            perceel["verkoopdatum"] = st.date_input("📅 Verkoopdatum", value=pd.to_datetime(perceel.get("verkoopdatum", date.today())), key=f"verkoopdatum_{i}")
            perceel["verkoopprijs_eur"] = st.number_input("💶 Verkoopprijs (EUR)", min_value=0.0, format="%.2f", value=perceel.get("verkoopprijs_eur", 0.0) or 0.0, key=f"verkoopprijs_eur_{i}")
            perceel["verkoopprijs"] = round(perceel["verkoopprijs_eur"] * wisselkoers) if wisselkoers else 0

        # Investeerders
        st.markdown("---")
        st.markdown("#### 👥 Investeerders")
        nieuwe_investeerders = []
        for j, inv in enumerate(perceel.get("investeerders", [])):
            col1, col2 = st.columns(2)
            naam = col1.text_input(f"Naam {j+1}", value=inv.get("naam", ""), key=f"inv_naam_edit_{i}_{j}")
            bedrag_eur = col2.number_input(f"Bedrag {j+1} (EUR)", min_value=0.0, format="%.2f", value=inv.get("bedrag_eur", 0.0) or 0.0, key=f"inv_bedrag_edit_{i}_{j}")
            rente = st.slider(f"Rente {j+1} (%)", 0.0, 100.0, value=inv.get("rente", 0.0) * 100, step=0.1, key=f"inv_rente_edit_{i}_{j}") / 100
            winst = st.slider(f"Winstdeling {j+1} (%)", 0.0, 100.0, value=inv.get("winstdeling", 0.0) * 100, step=1.0, key=f"inv_winst_edit_{i}_{j}") / 100
            rentetype = st.selectbox(f"Rentetype {j+1}", ["maandelijks", "jaarlijks", "bij verkoop"], index=["maandelijks", "jaarlijks", "bij verkoop"].index(inv.get("rentetype", "maandelijks")), key=f"inv_type_edit_{i}_{j}")
            nieuwe_investeerders.append({
                "naam": naam,
                "bedrag": round(bedrag_eur * wisselkoers) if wisselkoers else 0,
                "bedrag_eur": bedrag_eur,
                "rente": rente,
                "winstdeling": winst,
                "rentetype": rentetype
            })
        perceel["investeerders"] = nieuwe_investeerders

        # Documenten
        st.markdown("---")
        st.markdown("#### 📎 Documenten")
        vereiste = vereiste_docs.get(perceel["eigendomstype"], [])
        nieuwe_uploads = {}
        for doc in vereiste:
            nieuwe_uploads[doc] = st.checkbox(f"{doc} aanwezig?", value=perceel.get("uploads", {}).get(doc, False), key=f"upload_{i}_{doc}")
        perceel["uploads"] = nieuwe_uploads

        # Opslaan/verwijderen
        if st.button(f"💾 Opslaan wijzigingen", key=f"opslaan_{i}"):
            perceel["aankoopdatum"] = perceel["aankoopdatum"].strftime("%Y-%m-%d") if isinstance(perceel["aankoopdatum"], date) else perceel["aankoopdatum"]
            if perceel["dealstage"] == "Verkocht" and isinstance(perceel.get("verkoopdatum"), date):
                perceel["verkoopdatum"] = perceel["verkoopdatum"].strftime("%Y-%m-%d")
            save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
            st.success(f"Wijzigingen aan {naam} opgeslagen.")

        if st.button(f"🗑️ Verwijder perceel", key=f"verwijder_{i}"):
            st.session_state["percelen"].pop(i)
            save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
            st.session_state["rerun_trigger"] = True


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

toevoegen = st.sidebar.button("➕ Voeg perceel toe")

if toevoegen:
    # Validatie
    if not locatie:
        st.sidebar.error("❗ Vul een locatie in.")
    elif any(p.get("locatie") == locatie for p in st.session_state["percelen"]):
        st.sidebar.warning("⚠️ Er bestaat al een perceel met deze locatie.")
    elif not investeerders:
        st.sidebar.error("❗ Voeg minimaal één investeerder toe of kies 'Eigen beheer'.")
    elif len(polygon_coords) < 3:
        st.sidebar.error("❗ Polygon moet minstens 3 punten bevatten.")
    else:
        perceel = {
            "locatie": locatie,
	    "dealstage": dealstage,
            "investeerders": investeerders,
            "lengte": lengte,
            "breedte": breedte,
            "eigendomstype": eigendomstype,
            "polygon": polygon_coords,
            "uploads": uploads,
            "aankoopdatum": aankoopdatum.strftime("%Y-%m-%d"),
            "verkoopdatum": verkoopdatum.strftime("%Y-%m-%d") if isinstance(verkoopdatum, date) else verkoopdatum,
            "aankoopprijs": aankoopprijs,
            "aankoopprijs_eur": aankoopprijs_eur,
            "wisselkoers": wisselkoers,
            "verkoopprijs": verkoopprijs,
            "verkoopprijs_eur": verkoopprijs_eur if wisselkoers else None
        }
        st.session_state.percelen.append(perceel)
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state.percelen))
        st.sidebar.success(f"Perceel '{locatie}' toegevoegd en opgeslagen.")


