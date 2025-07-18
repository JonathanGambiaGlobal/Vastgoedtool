import streamlit as st


st.set_page_config(page_title="Kadasterkaart", layout="wide")

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
from auth import login_check
login_check()

def migrate_percelen():
    transformer = Transformer.from_crs("epsg:32628", "epsg:4326", always_xy=True)
    changed = False

    for perceel in st.session_state.get("percelen", []):
        investeerders = perceel.get("investeerders")

        # 🧹 Fix investeerders: string óf foutieve lijst van losse letters → Eigen beheer
        if isinstance(investeerders, str) or (
            isinstance(investeerders, list) and
            len(investeerders) > 1 and
            all(isinstance(i.get("naam"), str) and len(i.get("naam")) == 1 for i in investeerders)
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

        # 🧹 Fix polygon: detecteer én corrigeer alle polygonen met abs(lat) > 90 of abs(lon) > 180
        polygon = perceel.get("polygon")
        if polygon and isinstance(polygon, list):
            if any(abs(p[0]) > 90 or abs(p[1]) > 180 for p in polygon if isinstance(p, list) and len(p) == 2):
                polygon_converted = []
                for pt in polygon:
                    if isinstance(pt, list) and len(pt) == 2:
                        lon_conv, lat_conv = transformer.transform(pt[0], pt[1])
                        polygon_converted.append([lat_conv, lon_conv])
                perceel["polygon"] = polygon_converted
                changed = True

    if changed:
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
        st.cache_data.clear()
        st.success("✅ Migratie uitgevoerd: oude records gecorrigeerd en opgeslagen.")
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
    "Verkoop"
]

documentvereisten_per_fase = {
    "Aankoop": [
        "Financiering / investering + ID’s",
        "Sales agreement",
        "Transfer of ownership",
        "Rates (grondbelasting) ontvangstbewijs"
    ],
    "Omzetting / bewerking": [
        "Goedkeuring Alkalo",
        "Sketch plan",
        "Land use",
        "Toestemming mede-eigenaren"
    ],
    "Verkoop": [
        "Sales agreement + ID’s",
        "Transfer of ownership",
        "Betalingsbewijs",
        "Nacalculatie"
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

st.title("Percelen Beheer")

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
        perceel.setdefault("dealstage", "Oriëntatie")

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

if invoer_valuta == "EUR":
    aankoopprijs_eur = st.sidebar.number_input(
        "💶 Aankoopprijs (EUR)", min_value=0.0, format="%.2f", value=0.0
    )
    if wisselkoers:
        aankoopprijs = round(aankoopprijs_eur * wisselkoers)
        st.sidebar.info(f"≈ {aankoopprijs:,.0f} GMD (koers: {wisselkoers:.2f})")
    else:
        aankoopprijs = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")
else:
    aankoopprijs = st.sidebar.number_input(
        "🇬🇲 Aankoopprijs (GMD)", min_value=0.0, format="%.0f", value=0.0
    )
    if wisselkoers:
        aankoopprijs_eur = round(aankoopprijs / wisselkoers, 2)
        st.sidebar.info(f"≈ {aankoopprijs_eur:,.2f} EUR (koers: {wisselkoers:.2f})")
    else:
        aankoopprijs_eur = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — EUR niet omgerekend.")


if snel_verkocht:
    st.sidebar.markdown("### 💰 Verkoopgegevens (historisch)")
    verkoopdatum = st.sidebar.date_input("🗓️ Verkoopdatum", value=date.today())
    verkoopprijs_eur = st.sidebar.number_input("💶 Verkoopprijs (EUR)", min_value=0.0, format="%.2f", value=0.0)
    if wisselkoers:
        verkoopprijs = round(verkoopprijs_eur * wisselkoers)
        st.sidebar.info(f"≈ {verkoopprijs:,.0f} GMD (koers: {wisselkoers:.2f})")
    else:
        verkoopprijs = 0
        st.sidebar.warning("Wisselkoers niet beschikbaar — GMD niet omgerekend.")
else:
    verkoopdatum = None
    verkoopprijs = 0
    verkoopprijs_eur = 0.0


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

def get_vereiste_documenten(perceel, fase):
    return documentvereisten_per_fase.get(fase, []).copy()

def bepaal_hoogste_fase(perceel):
    for fase in PIPELINE_FASEN:
        vereiste_docs = get_vereiste_documenten(perceel, fase)
        if vereiste_docs:
            if not all(perceel.get("uploads", {}).get(doc, False) for doc in vereiste_docs):
                return fase
    return PIPELINE_FASEN[-1]  # Laatste fase als alles aanwezig


if snel_verkocht:
    docs_sidebar = []
else:
    docs_sidebar = get_vereiste_documenten({
        "eigendomstype": eigendomstype,
        "wordt_gesplitst": False
    }, "Aankoop")

uploads = {}
for doc in docs_sidebar:
    col1, col2 = st.sidebar.columns([1, 2])
    with col1:
        uploads[doc] = st.checkbox(f"{doc} aanwezig?", value=False)
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

    # 🔔 Mooiere popup met nette opsomming
    investeerders = ", ".join(
        i.get('naam') if isinstance(i, dict) else str(i)
        for i in perceel.get('investeerders', [])
    ) or "Geen"

    documenten = ", ".join(
        doc for doc, aanwezig in perceel.get('uploads', {}).items() if aanwezig
    ) or "Geen"

    popup_html = f"""
    <b>📍 Locatie:</b> {perceel.get('locatie', 'Onbekend')}<br>
    <b>🗓️ Aankoopdatum:</b> {perceel.get('aankoopdatum', 'n.v.t.')}<br>
    <b>💰 Aankoopprijs:</b> {perceel.get('aankoopprijs', 0):,.0f} GMD<br>
    <b>🔖 Dealstage:</b> {perceel.get('dealstage', 'Onbekend')}<br>
    <b>🏷️ Eigendom:</b> {perceel.get('eigendomstype', 'Onbekend')}<br>
    <b>🔹 Wordt gesplitst:</b> {'Ja' if perceel.get('wordt_gesplitst') else 'Nee'}<br>
    <b>👥 Investeerders:</b> {investeerders}<br>
    <b>📄 Documenten aanwezig:</b> {documenten}
    """

    if polygon and isinstance(polygon, list):
        polygon_converted = []
        for point in polygon:
            if isinstance(point, list) and len(point) == 2:
                lat, lon = point
                polygon_converted.append([lat, lon])

        # 🔎 DEBUG output vóór tekenen
        st.write(f"🔎 Polygon DEBUG — {tooltip}: {polygon_converted}")

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

with st.container():
    output = st_folium(m, width=1000, height=500)
    st.markdown("", unsafe_allow_html=True)


if st.button("↩ Undo laatste wijziging"):
    undo()

    # Percelen beheer sectie
    st.subheader("✏️ Beheer percelen")
    col1, = st.columns(1)

    with col1:
        if st.button("📤 Percelen opnieuw laden"):
            st.session_state["percelen"] = load_percelen_from_json()
            st.success("Percelen zijn opnieuw geladen.")

    # 🔁 Verwijder skip_load zodat laden weer werkt na undo
    if "skip_load" in st.session_state:
        del st.session_state["skip_load"]
    
for i, perceel in enumerate(st.session_state["percelen"]):
    if not isinstance(perceel, dict):
        st.warning(f"Percel op index {i} is ongeldig en wordt overgeslagen.")
        continue

    # 🔒 Extra bescherming: zorg dat 'uploads' altijd dict is
    if "uploads" not in perceel or not isinstance(perceel["uploads"], dict):
        perceel["uploads"] = {}

    # 🔹 Bepaal huidige fase veilig
    huidige_fase = bepaal_hoogste_fase(perceel) if perceel["uploads"] else "Aankoop"

    with st.expander(f"📍 {perceel.get('locatie', f'Perceel {i+1}')}", expanded=False):

        # 📌 Locatie (disabled)
        st.text_input("Locatie", value=perceel.get("locatie", ""), key=f"edit_locatie_{i}", disabled=True)

        # ➗ Wordt gesplitst (alleen bij Verkoop fase)
        if huidige_fase == "Verkoop":
            perceel["wordt_gesplitst"] = st.checkbox(
                "Wordt perceel gesplitst?",
                value=perceel.get("wordt_gesplitst", False),
                key=f"wordt_gesplitst_{i}"
            )
        else:
            perceel["wordt_gesplitst"] = False

        # 📏 Lengte / Breedte
        perceel["lengte"] = st.number_input(
            "📏 Lengte (m)", min_value=0, value=perceel.get("lengte", 0), key=f"edit_lengte_{i}"
        )
        perceel["breedte"] = st.number_input(
            "📐 Breedte (m)", min_value=0, value=perceel.get("breedte", 0), key=f"edit_breedte_{i}"
        )

        # 🏷️ Eigendomstype
        perceel["eigendomstype"] = st.selectbox(
            "Eigendomsvorm",
            ["Customary land", "Freehold land"],
            index=["Customary land", "Freehold land"].index(perceel.get("eigendomstype", "Customary land")),
            key=f"eigendom_{i}"
        )

        # 📋 Documenten checklist
        st.markdown("#### 📋 Documenten")
        vereiste_docs = get_vereiste_documenten(perceel, huidige_fase)
        nieuwe_uploads = {}
        for doc in vereiste_docs:
            nieuwe_uploads[doc] = st.checkbox(
                f"{doc} aanwezig?",
                value=perceel.get("uploads", {}).get(doc, False),
                key=f"upload_{i}_{doc}"
            )
        perceel["uploads"] = nieuwe_uploads

        # 🔹 Huidige fase opnieuw berekenen na wijzigingen
        huidige_fase = bepaal_hoogste_fase(perceel)
        st.markdown(f"📌 Huidige fase (live berekend): {huidige_fase}")

        if st.button(f"💾 Opslaan wijzigingen voor {perceel.get('locatie')}", key=f"opslaan_context_{i}"):
            save_state()
            perceel["dealstage"] = bepaal_hoogste_fase(perceel)
            save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
            st.cache_data.clear()
            st.success(f"Wijzigingen aan {perceel.get('locatie')} opgeslagen.")

        # 🎯 Toon automatisch bepaalde fase
        st.markdown(f"**📌 Automatisch bepaalde fase: {perceel['dealstage']}**")

        # 👥 Investeerdersstructuur
        st.markdown("#### 👥 Investeerders")
        nieuwe_investeerders = []
        for j, inv in enumerate(perceel.get("investeerders", [])):
            col1, col2 = st.columns(2)

            naam_waarde = inv.get("naam", "") if isinstance(inv, dict) else str(inv)
            bedrag_waarde = inv.get("bedrag_eur", 0.0) if isinstance(inv, dict) else 0.0

            naam = col1.text_input(
                f"Naam {j+1}", value=naam_waarde, key=f"inv_naam_edit_{i}_{j}"
            )
            bedrag_eur = col2.number_input(
                f"Bedrag {j+1} (EUR)",
                min_value=0.0, format="%.2f",
                value=bedrag_waarde or 0.0,
                key=f"inv_bedrag_edit_{i}_{j}"
            )

            nieuwe_investeerders.append({
                "naam": naam,
                "bedrag": round(bedrag_eur * wisselkoers) if wisselkoers else 0,
                "bedrag_eur": bedrag_eur,
                "rente": 0.0,
                "winstdeling": 0.0,
                "rentetype": "bij verkoop"
            })

        perceel["investeerders"] = nieuwe_investeerders


        # 💾 Opslaan / verwijderen
        if is_admin:
            if st.button(f"💾 Opslaan wijzigingen voor {perceel.get('locatie')}", key=f"opslaan_bewerken_{i}"):
                save_state()
                save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                st.cache_data.clear()
                st.success(f"Wijzigingen aan {perceel.get('locatie')} opgeslagen.")

            if st.button(f"🗑️ Verwijder perceel", key=f"verwijder_{i}"):
                save_state()
                st.session_state["percelen"].pop(i)
                save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                st.cache_data.clear()
                st.session_state["rerun_trigger"] = True
        else:
            st.info("🔒 Alleen admins kunnen wijzigingen opslaan of percelen verwijderen.")

  
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
    # Validatie
    if not locatie:
        st.sidebar.error("❗ Vul een locatie in.")
    elif any(p.get("locatie") == locatie for p in st.session_state["percelen"]):
        st.sidebar.warning("⚠️ Er bestaat al een perceel met deze locatie.")
    elif not investeerders and not snel_verkocht:
        st.sidebar.error("❗ Voeg minimaal één investeerder toe of kies 'Eigen beheer'.")
    elif len(polygon_coords) < 3:
        st.sidebar.error("❗ Polygon moet minstens 3 punten bevatten.")
    else:
        # 🔔 Bepaal dealstage vóórdat we perceel dict aanmaken
        dealstage = "Verkoop" if snel_verkocht else bepaal_hoogste_fase({
            "uploads": uploads
        })

        perceel = {
            "locatie": locatie,
            "dealstage": dealstage,  # ✅ dealstage correct opgenomen
            "wordt_gesplitst": False,
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
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
        st.sidebar.success(f"Perceel '{locatie}' toegevoegd en opgeslagen.")


