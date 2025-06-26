import streamlit as st
import pandas as pd
import json
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw
from datetime import date
from utils import get_exchange_rate_eur_to_gmd, save_percelen_as_json, load_percelen_from_json


st.set_page_config(page_title="🧸️ Kadasterkaart", layout="wide")

if st.session_state.get("rerun_trigger") is True:
    st.session_state["rerun_trigger"] = False
    st.rerun()
st.markdown("""
    <script>
        window.scrollTo(0, 0);
    </script>
""", unsafe_allow_html=True)
st.title("🧸️ Interactieve Kadasterkaart")

if "percelen" not in st.session_state or not st.session_state.percelen:
    st.session_state.percelen = load_percelen_from_json()

st.sidebar.header("📝 Perceelinvoer")

# Geen dropdown, altijd lege velden voor nieuw perceel
locatie = ""
lengte = 0
breedte = 0
status = "In portfolio"
eigendomstype = "Customary land"
aankoopdatum = date.today()
verkoopdatum = None
aankoopprijs = 0
aankoopprijs_eur = 0
verkoopprijs = 0
verkoopprijs_eur = 0
investeerders = []
uploads = {}

locatie = st.sidebar.text_input("📍 locatie", placeholder="Bijv. Sanyang A", value=locatie)
lengte = st.sidebar.number_input("📏 Lengte (m)", min_value=0, value=lengte)
breedte = st.sidebar.number_input("📐 Breedte (m)", min_value=0, value=breedte)
status = st.sidebar.selectbox("📄 Status", ["In portfolio", "Verkocht", "In planning"], index=["In portfolio", "Verkocht", "In planning"].index(status))
wisselkoers = get_exchange_rate_eur_to_gmd()

if status == "Verkocht":
    verkoopdatum = st.sidebar.date_input("🗕️ Verkoopdatum", value=verkoopdatum if verkoopdatum else date.today())
    verkoopprijs_eur = st.sidebar.number_input(
        "💶 Verkoopprijs (EUR)",
        min_value=0.0,
        format="%.2f",
        value=float(verkoopprijs_eur) if verkoopprijs_eur is not None else 0.0
    )
    if wisselkoers:
        verkoopprijs = round(verkoopprijs_eur * wisselkoers)
        st.sidebar.info(f"≈ {verkoopprijs:,.0f} GMD (koers: {wisselkoers:.2f})")
    else:
        verkoopprijs = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")
else:
    verkoopdatum = None
    verkoopprijs = None
    verkoopprijs_eur = None

aankoopdatum = st.sidebar.date_input("🗕️ Aankoopdatum", value=aankoopdatum)
aankoopprijs_eur = st.sidebar.number_input(
    "💶 Aankoopprijs (EUR)",
    min_value=0.0,
    format="%.2f",
    value=float(aankoopprijs_eur) if aankoopprijs_eur is not None else 0.0
)
if wisselkoers:
    aankoopprijs = round(aankoopprijs_eur * wisselkoers)
    st.sidebar.info(f"≈ {aankoopprijs:,.0f} GMD (koers: {wisselkoers:.2f})")
else:
    aankoopprijs = 0
    st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")

st.sidebar.markdown("### 👥 Investeerdersstructuur")
aantal_investeerders = st.sidebar.number_input(
    "Aantal investeerders (0 = eigen beheer)",
    min_value=0,
    max_value=10,
    value=int(len(investeerders)) if investeerders is not None else 0,
    step=1
)
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
    st.session_state["investeerders_input"] = inv_input_data
else:
    for i in range(1, aantal_investeerders + 1):
        st.sidebar.markdown(f"#### Investeerder {i}")
        inv_data = inv_input_data[i - 1] if i <= len(inv_input_data) else {}
        naam = st.sidebar.text_input(f"Naam {i}", value=inv_data.get("naam", ""), key=f"inv_naam_{i}")
        bedrag_eur = st.sidebar.number_input(
            f"Bedrag {i} (EUR)",
            min_value=0.0,
            format="%.2f",
            value=float(inv_data.get("bedrag_eur", 0.0)) if inv_data.get("bedrag_eur") is not None else 0.0,
            key=f"inv_bedrag_eur_{i}"
        )
        bedrag = round(bedrag_eur * wisselkoers) if wisselkoers else 0
        rente = st.sidebar.number_input(
            f"Rente {i} (%)",
            min_value=0.0,
            max_value=100.0,
            step=0.1,
            value=float(inv_data.get("rente", 0.0)) * 100 if inv_data.get("rente") is not None else 0.0,
            key=f"inv_rente_{i}"
        )
        winst = st.sidebar.number_input(
            f"Winstdeling {i} (%)",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            value=float(inv_data.get("winstdeling", 0.0)) * 100 if inv_data.get("winstdeling") is not None else 0.0,
            key=f"inv_winst_{i}"
        )
        rentetype = st.sidebar.selectbox(
            f"Rentevorm {i}",
            options=["maandelijks", "jaarlijks", "bij verkoop"],
            index=["maandelijks", "jaarlijks", "bij verkoop"].index(inv_data.get("rentetype", "maandelijks")),
            key=f"inv_rentetype_{i}"
        )
        if naam and bedrag > 0:
            investeerders.append({
                "naam": naam,
                "bedrag": bedrag,
                "bedrag_eur": bedrag_eur,
                "rente": rente / 100,
                "winstdeling": winst / 100,
                "rentetype": rentetype
            })

# Bijwerken van de inputdata — niet opnieuw aanmaken
st.session_state["investeerders_input"] = inv_input_data

st.sidebar.markdown("### 📋 Documenten")
eigendomstype = st.sidebar.selectbox("Eigendomsvorm", ["Customary land", "Freehold land"], index=["Customary land", "Freehold land"].index(eigendomstype))

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

# Voeg polygon-tool toe aan kaart
start_coords = [13.3085, -16.6800]
m = folium.Map(location=start_coords, zoom_start=18, tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", attr="Google Hybrid")
Draw(export=False).add_to(m)

# ▼▼▼ TOON BESTAANDE PERCELEN OP KAART ▼▼▼
for perceel in st.session_state.percelen:
    polygon = perceel.get("polygon")
    tooltip = perceel.get("locatie", "Onbekend")
    popup_html = f"""
    <b>{tooltip}</b><br>
    Investeerders: {[i.get('naam') for i in perceel.get('investeerders', [])]}<br>
    Status: {perceel.get('status', 'onbekend')}<br>
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
# ▲▲▲ EINDE: Toon bestaande percelen

# ✅ Container-wrapped folium output om witruimte te beperken
with st.container():
    output = st_folium(m, width=1000, height=500)
    st.markdown("", unsafe_allow_html=True)

# 🛠️ Perceelbeheer per perceel
st.subheader("✏️ Beheer percelen")
col1, col2 = st.columns(2)
with col1:
    if st.button("💾 Percelen opslaan"):
        save_percelen_as_json([json.loads(json.dumps(p, default=str)) for p in st.session_state["percelen"]])
        st.success("Percelen zijn opgeslagen naar Google Sheet.")
with col2:
    if st.button("📤 Percelen opnieuw laden"):
        st.session_state["percelen"] = load_percelen_from_json()
        st.success("Percelen zijn opnieuw geladen.")

for i, perceel in enumerate(st.session_state.percelen):
    verkoopprijs_eur_edit = 0.0
    naam = perceel.get("locatie", f"Perceel {i+1}")
    with st.expander(f"📍 {naam}", expanded=False):
        # ✏️ Volledige bewerkbare velden per perceel
        perceel["locatie"] = st.text_input("📍 Locatie", value=perceel.get("locatie", ""), key=f"locatie_{i}")
        perceel["lengte"] = st.number_input("📏 Lengte (m)", min_value=0, value=perceel.get("lengte", 0), key=f"lengte_{i}")
        perceel["breedte"] = st.number_input("📐 Breedte (m)", min_value=0, value=perceel.get("breedte", 0), key=f"breedte_{i}")
        perceel["status"] = st.selectbox("📄 Status", ["In portfolio", "Verkocht", "In planning"], index=["In portfolio", "Verkocht", "In planning"].index(perceel.get("status", "In portfolio")), key=f"status_change_{i}")
        perceel["eigendomstype"] = st.selectbox("📁 Eigendomsvorm", ["Customary land", "Freehold land"], index=["Customary land", "Freehold land"].index(perceel.get("eigendomstype", "Customary land")), key=f"eigendom_{i}")

        perceel["aankoopdatum"] = st.date_input("📅 Aankoopdatum", value=pd.to_datetime(perceel.get("aankoopdatum", date.today())), key=f"aankoopdatum_{i}")
        perceel["aankoopprijs_eur"] = st.number_input("💶 Aankoopprijs (EUR)", min_value=0.0, format="%.2f", value=perceel.get("aankoopprijs_eur", 0.0) or 0.0, key=f"aankoopprijs_eur_{i}")
        perceel["aankoopprijs"] = round(perceel["aankoopprijs_eur"] * wisselkoers) if wisselkoers else 0

        if perceel["status"] == "Verkocht":
            perceel["verkoopdatum"] = st.date_input("📅 Verkoopdatum", value=pd.to_datetime(perceel.get("verkoopdatum", date.today())), key=f"verkoopdatum_{i}")
            perceel["verkoopprijs_eur"] = st.number_input("💶 Verkoopprijs (EUR)", min_value=0.0, format="%.2f", value=perceel.get("verkoopprijs_eur", 0.0) or 0.0, key=f"verkoopprijs_eur_{i}")
            perceel["verkoopprijs"] = round(perceel["verkoopprijs_eur"] * wisselkoers) if wisselkoers else 0

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

        st.markdown("---")
        st.markdown("#### 📎 Documenten")
        vereiste = vereiste_docs.get(perceel["eigendomstype"], [])
        nieuwe_uploads = {}
        for doc in vereiste:
            nieuwe_uploads[doc] = st.checkbox(f"{doc} aanwezig?", value=perceel.get("uploads", {}).get(doc, False), key=f"upload_{i}_{doc}")
        perceel["uploads"] = nieuwe_uploads

        if st.button(f"💾 Opslaan wijzigingen", key=f"opslaan_{i}"):
            perceel["aankoopdatum"] = perceel["aankoopdatum"].strftime("%Y-%m-%d") if isinstance(perceel["aankoopdatum"], date) else perceel["aankoopdatum"]
            if perceel["status"] == "Verkocht" and isinstance(perceel.get("verkoopdatum"), date):
                perceel["verkoopdatum"] = perceel["verkoopdatum"].strftime("%Y-%m-%d")
            save_percelen_as_json([json.loads(json.dumps(p, default=str)) for p in st.session_state["percelen"]])
        success_placeholder = st.empty()
        success_placeholder.success(f"Wijzigingen aan {naam} opgeslagen.")
        nieuwe_status = st.selectbox(
            "📄 Wijzig status",
            ["In portfolio", "Verkocht", "In planning"],
            index=["In portfolio", "Verkocht", "In planning"].index(perceel.get("status", "In portfolio")),
            key=f"status_{i}"
        )
        if nieuwe_status != perceel["status"]:
            perceel["status"] = nieuwe_status
            if perceel["status"] == "Verkocht" and isinstance(perceel.get("verkoopdatum"), date):
                perceel["verkoopdatum"] = perceel["verkoopdatum"].strftime("%Y-%m-%d")
            save_percelen_as_json([json.loads(json.dumps(p, default=str)) for p in st.session_state["percelen"]])
        success_placeholder = st.empty()
        success_placeholder.success(f"Status van {naam} gewijzigd naar: {nieuwe_status}")


        st.markdown(f"**Eigendom:** {perceel.get('eigendomstype', '-')}")
        st.markdown(f"**Investeerders:** {perceel.get('investeerders', '-')}")
        if st.button(f"🗑️ Verwijder perceel", key=f"verwijder_{i}"):
            st.session_state["percelen"].pop(i)
            save_percelen_as_json(st.session_state["percelen"])
            st.session_state["rerun_trigger"] = True

# 🔁 Coördinaten handmatig invoeren
from pyproj import Transformer

st.sidebar.markdown("### 📍 Coördinaten invoer")
coord_type = st.sidebar.radio("Coördinatentype", ["UTM", "Latitude/Longitude"], index=1)

polygon_coords = []
for i in range(1, 6):
    col1, col2 = st.sidebar.columns(2)
    x = col1.text_input(f"X{i}", key=f"x_{i}")
    y = col2.text_input(f"Y{i}", key=f"y_{i}")
    try:
        if coord_type == "UTM":
            # EPSG:32628 = UTM zone 28N (pas aan indien andere zone gebruikt wordt)
            transformer = Transformer.from_crs("epsg:32628", "epsg:4326", always_xy=True)
            lon, lat = transformer.transform(float(x), float(y))
        else:
            lat = float(y)
            lon = float(x)
        if lat and lon:
            polygon_coords.append([lat, lon])
    except:
        pass


# ⛔ overschrijf polygon_coords alleen als er getekend is
if output.get("last_active_drawing"):
    geom = output["last_active_drawing"].get("geometry", {})
    if geom.get("type") == "Polygon":
        polygon_coords = geom.get("coordinates", [[]])[0]
        polygon_coords = [[coord[1], coord[0]] for coord in polygon_coords]

toevoegen = st.sidebar.button("➕ Voeg perceel toe")

if toevoegen:
    if not locatie:
        st.sidebar.error("❗ Vul een locatie in.")
    elif any(p.get("locatie") == locatie for p in st.session_state["percelen"]):
        st.sidebar.warning("⚠️ Er bestaat al een perceel met deze locatie.")
    elif not investeerders:
        st.sidebar.error("❗ Voeg minimaal één investeerder toe of kies 'Eigen beheer'.")
    else:
        perceel = {
            "locatie": locatie,
            "investeerders": investeerders,
            "lengte": lengte,
            "breedte": breedte,
            "status": status,
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
        save_percelen_as_json(st.session_state.percelen)
        st.sidebar.success(f"Perceel '{locatie}' toegevoegd en opgeslagen.")




