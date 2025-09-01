import streamlit as st

# 🎨 moet ALTIJD eerst
st.set_page_config(page_title="Quadraat Global", layout="wide")  # 👈 alleen voor browser-tab

# utils
from utils import (
    get_ai_config,
    language_selector,
    get_exchange_rate_eur_to_gmd,
    save_percelen_as_json,
    load_percelen_from_json,
    render_pipeline,
    format_currency,
)

# 🌐 taal instellen
_, n_ = language_selector()

# logo en titel
st.image("QG.png", width=180)

import pandas as pd
from groq import Groq
import json
import copy
import folium
from folium.plugins import Draw
from datetime import date
from streamlit_folium import st_folium
from pyproj import Transformer

# auth
from auth import login_check

# 🔐 login altijd eerst
login_check()

RENTETYPES = {
    "monthly": _("maandelijks"),
    "yearly": _("jaarlijks"),
    "at_sale": _("bij verkoop"),
}

STRATEGIE_OPTIES = {
    "short_term": _("Korte termijn verkoop"),
    "hold": _("Lange termijn aanhouden"),
    "split_sell": _("Verkavelen en verkopen"),
}

_cfg = get_ai_config()
MODEL_PRIMARY  = _cfg.get("primary_model", "llama-3.3-70b-versatile")
MODEL_FALLBACK = _cfg.get("fallback_model", "llama-3.1-8b-instant")
TEMPERATURE    = float(_cfg.get("temperature", 0.2))
TOP_P          = float(_cfg.get("top_p", 0.9))

_api_key = st.secrets.get("GROQ_API_KEY")
if not _api_key:
    st.error(_("⚠️ GROQ_API_KEY ontbreekt in .streamlit/secrets.toml"))
    st.stop()

_groq = Groq(api_key=_api_key)

def groq_chat(messages, *, model: str | None = None) -> str:
    """Robuuste helper met automatische fallback naar kleiner model."""
    try:
        res = _groq.chat.completions.create(
            model=model or MODEL_PRIMARY,
            messages=messages,
            temperature=TEMPERATURE,
            top_p=TOP_P,
        )
        return (res.choices[0].message.content or "").strip()
    except Exception:
        res = _groq.chat.completions.create(
            model=MODEL_FALLBACK,
            messages=messages,
            temperature=TEMPERATURE,
            top_p=TOP_P,
        )
        return (res.choices[0].message.content or "").strip()
# ============================================================================

# ===== Query parameter helpers =====
def get_qp():
    try:
        from urllib.parse import urlparse, parse_qs
        qp = dict(st.query_params)
        return {k: (v[0] if isinstance(v, list) else v) for k,v in qp.items()}
    except Exception:
        return {}

def set_qp(**kwargs):
    try:
        st.query_params.clear()
        for k,v in kwargs.items():
            st.query_params[k] = str(v)
    except Exception:
        pass

def format_date_eu(date_str):
    try:
        dt = pd.to_datetime(date_str, errors="coerce")
        if pd.notnull(dt):
            return dt.strftime("%d-%m-%Y")
    except Exception:
        pass
    return date_str or _("n.v.t.")

def migrate_percelen():
    transformer = Transformer.from_crs("epsg:32628", "epsg:4326", always_xy=True)
    changed = False

    # Oude (8/4) fasen → nieuwe 3 fasen
    old_to_new = {
        "Oriëntatie": _("Aankoop"),
        "In onderhandeling": _("Aankoop"),
        "Te kopen": _("Aankoop"),
        "Aangekocht": _("Aankoop"),
        "Geregistreerd": _("Omzetting / bewerking"),
        "In beheer": _("Omzetting / bewerking"),
        "In verkoop": _("Verkoop"),
    }

    for perceel in st.session_state.get("percelen", []):
        ds = perceel.get("dealstage")
        if not ds:
            perceel["dealstage"] = _("Aankoop")
            changed = True
        elif ds in old_to_new:
            perceel["dealstage"] = old_to_new[ds]
            changed = True

        if "uploads" not in perceel:
            perceel["uploads"] = {}
            changed = True
        if "uploads_urls" not in perceel:
            perceel["uploads_urls"] = {}
            changed = True

        investeerders = perceel.get("investeerders")
        if isinstance(investeerders, str) or (
            isinstance(investeerders, list)
            and len(investeerders) > 1
            and all(isinstance(i.get("naam"), str) and len(i.get("naam")) == 1 for i in investeerders)
        ):
            perceel["investeerders"] = [{
                "naam": _("Eigen beheer"),
                "bedrag": 0,
                "bedrag_eur": 0,
                "rente": 0.0,
                "winstdeling": 1.0,
                "rentetype": _("bij verkoop")
            }]
            changed = True

        polygon = perceel.get("polygon")
        if polygon and isinstance(polygon, list):
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

        if perceel.get("eigendomstype") in ["Customary land", "Freehold land"]:
            perceel["eigendomstype"] = _("Geregistreerd land")
            changed = True

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
        st.success(_("✅ Migratie uitgevoerd: fasen gemapt, records opgeschoond en winst bijgewerkt."))
        st.session_state["skip_load"] = True
        st.rerun()

is_admin = st.session_state.get("rol") == "admin"

if "history" not in st.session_state:
    st.session_state["history"] = []

def save_state():
    if "history" not in st.session_state:
        st.session_state["history"] = []
    st.session_state["history"].append(copy.deepcopy(st.session_state["percelen"]))
    print(f"[DEBUG] save_state called. History length: {len(st.session_state['history'])}")

def undo():
    if st.session_state.get("history"):
        print(f"[DEBUG] undo called. History length before pop: {len(st.session_state['history'])}")
        st.session_state["percelen"] = st.session_state["history"].pop()
        st.write(_("🔄 Percelen na undo:"), st.session_state.get("percelen"))
        print(f"[DEBUG] History length after pop: {len(st.session_state['history'])}")
        st.write(_("🧪 Keys vóór reset:"), list(st.session_state.keys()))

        prefixes = [
            "edit_locatie_", "edit_lengte_", "edit_breedte_",
            "dealstage_edit_", "eigendom_", "aankoopdatum_",
            "aankoopprijs_eur_", "verkoopdatum_", "verkoopprijs_eur_",
            "fase_", "upload_", "opslaan_bewerken_", "x_", "y_", "verwijder_"
        ]

        inv_prefixes = [
            "inv_naam_edit_", "inv_bedrag_edit_", "inv_rente_edit_",
            "inv_winst_edit_", "inv_type_edit_"
        ]

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

        for i in range(max_index):
            for prefix in prefixes + inv_prefixes:
                st.session_state.pop(f"{prefix}{i}", None)
            for j in range(15):
                for prefix in inv_prefixes:
                    st.session_state.pop(f"{prefix}{i}_{j}", None)

        st.session_state.pop("investeerders_input", None)
        st.session_state["skip_load"] = True
        st.write(_("🧼 Keys ná reset:"), list(st.session_state.keys()))
        st.rerun()

PIPELINE_FASEN = [
    _("Aankoop"),
    _("Omzetting / bewerking"),
    _("Verkoop"),
    _("Verkocht")
]

documentvereisten_per_fase = {
    _("Aankoop"): [
        _("Sales agreement"),
        _("Transfer of ownership"),
        _("Sketch plan"),
        _("Rates ontvangstbewijs"),
        _("Land Use Report"),
        _("Goedkeuring Alkalo")
    ],
    _("Omzetting / bewerking"): [
        _("Resale agreement")
    ],
    _("Verkoop"): [
        _("Financieringsoverzicht"),
        _("ID’s investeerders"),
        _("Uitbetaling investeerders")
    ]
}

def prepare_percelen_for_saving(percelen: list[dict]) -> list[dict]:
    def serialize(obj):
        if isinstance(obj, date):
            return obj.isoformat()
        return obj
    return [json.loads(json.dumps(p, default=serialize)) for p in percelen]

if st.session_state.get("rerun_trigger") is True:
    st.session_state["rerun_trigger"] = False
    st.rerun()

st.markdown("""<script>window.scrollTo(0, 0);</script>""", unsafe_allow_html=True)
st.title(_("Percelenbeheer"))

if "percelen" not in st.session_state or st.session_state.get("skip_load") != True:
    loaded = load_percelen_from_json()
    percelen_valid = []
    for i, p in enumerate(loaded):
        if isinstance(p, dict):
            percelen_valid.append(p)
        else:
            st.warning(_("Percel index {i} is ongeldig en wordt genegeerd.").format(i=i))
    st.session_state.percelen = percelen_valid

    for perceel in st.session_state["percelen"]:
        perceel.setdefault("wordt_gesplitst", False)
        perceel.setdefault("dealstage", _("Aankoop"))

# Sidebar invoer voor nieuw perceel
st.sidebar.header(_("📝 Perceelinvoer"))

bestaande_labels = [p.get("locatie", "") for p in st.session_state.get("percelen", [])]
prefixes = sorted(set(l.rsplit(" ", 1)[0] for l in bestaande_labels if l.strip() and l.rsplit(" ", 1)[-1].isdigit()))

keuze = st.sidebar.selectbox(_("📍 Gebied & Subzone"), prefixes + [_("➕ Nieuw gebied...")])

if keuze == _("➕ Nieuw gebied..."):
    hoofd = st.sidebar.text_input(_("🌍 Hoofdgebied (bv. Serekunda)"))
    sub = st.sidebar.text_input(_("🏘 Subzone (bv. Sanyang)"))
    prefix = f"{hoofd.strip()}, {sub.strip()}"
else:
    prefix = keuze

aantal = sum(1 for l in bestaande_labels if l.startswith(f"{prefix} "))
nieuw_label = f"{prefix} {aantal + 1}"
st.sidebar.success(_("📌 Nieuwe locatie: {loc}").format(loc=nieuw_label))

locatie = nieuw_label
lengte = st.sidebar.number_input(_("📏 Lengte (m)"), min_value=0, value=0)
breedte = st.sidebar.number_input(_("📐 Breedte (m)"), min_value=0, value=0)

wisselkoers = get_exchange_rate_eur_to_gmd()
snel_verkocht = st.sidebar.checkbox(_("⚡ Snel invoeren als verkocht (historisch)"))

verkoopdatum = None
verkoopprijs_eur = 0.0
verkoopprijs = 0
wordt_gesplitst = False

aankoopdatum = st.sidebar.date_input(_("🗕️ Aankoopdatum"), value=date.today())

invoer_valuta = st.sidebar.radio(_("Valuta aankoopprijs"), ["EUR", "GMD"], horizontal=True)
valutasymbool = "€" if invoer_valuta == "EUR" else "GMD"

col1, col2 = st.sidebar.columns([1, 4])
col1.markdown(f"**{valutasymbool}**")
invoerwaarde = col2.number_input(_("Aankoopprijs"), label_visibility="collapsed", min_value=0.0, format="%.2f", value=0.0)

if invoer_valuta == "EUR":
    aankoopprijs_eur = invoerwaarde
    if wisselkoers:
        aankoopprijs = round(aankoopprijs_eur * wisselkoers)
        st.sidebar.info(_("≈ {prijs} (koers: {koers:.2f})").format(
            prijs=format_currency(aankoopprijs, "GMD"), koers=wisselkoers))
    else:
        aankoopprijs = 0
        st.sidebar.warning(_("⚠️ Wisselkoers niet beschikbaar — GMD niet omgerekend."))
else:
    aankoopprijs = invoerwaarde
    if wisselkoers:
        aankoopprijs_eur = round(aankoopprijs / wisselkoers, 2)
        st.sidebar.info(_("≈ {prijs} (koers: {koers:.2f})").format(
            prijs=format_currency(aankoopprijs_eur, "EUR"), koers=wisselkoers))
    else:
        aankoopprijs_eur = 0
        st.sidebar.warning(_("⚠️ Wisselkoers niet beschikbaar — EUR niet omgerekend."))

if snel_verkocht:
    st.sidebar.markdown("### " + _("💰 Verkoopgegevens (historisch)"))
    verkoopdatum = st.sidebar.date_input(_("🗓️ Verkoopdatum"), value=date.today())
    verkoopprijs_eur = st.sidebar.number_input(_("💶 Verkoopprijs (EUR)"), min_value=0.0, format="%.2f", value=0.0)
    if wisselkoers:
        verkoopprijs = round(verkoopprijs_eur * wisselkoers)
        st.sidebar.info(_("≈ {prijs} (koers: {koers:.2f})").format(
            prijs=format_currency(verkoopprijs, "GMD"), koers=wisselkoers))
    else:
        verkoopprijs = 0
        st.sidebar.warning(_("⚠️ Wisselkoers niet beschikbaar — GMD niet omgerekend."))
else:
    verkoopdatum = None
    verkoopprijs = 0
    verkoopprijs_eur = 0.0

st.sidebar.markdown("### " + _("🧭 Strategie en planning"))
strategieopties = [
    _("Korte termijn verkoop"),
    _("Verkavelen en verkopen"),
    _("Zelf woningen bouwen"),
    _("Zelf bedrijf starten"),
    _("Nog onbekend")
]

strategie = st.sidebar.selectbox(_("Doel met dit perceel"), strategieopties)

# 📅 Start verkooptraject
start_traject = st.sidebar.date_input(
    _("🗓️ Start verkooptraject"),
    value=date.today(),
    key="start_verkooptraject_sidebar"
)

# 📅 Einddatum kiezen
doorlooptijd_datum = st.sidebar.date_input(
    _("Verwachte einddatum"),
    value=date.today(),
    key="doorlooptijd_sidebar"
)

verkoopperiode_maanden = max(
    (doorlooptijd_datum.year - start_traject.year) * 12
    + (doorlooptijd_datum.month - start_traject.month),
    1
)
st.session_state["periode_sidebar"] = verkoopperiode_maanden

# 🏗️ Verkavelen en verkopen
if strategie == _("Verkavelen en verkopen"):
    aantal_kavels = st.sidebar.number_input(
        _("Aantal kavels"),
        min_value=1,
        value=1,
        key="sidebar_aantal_kavels"
    )

    valuta_keuze_sidebar = st.sidebar.radio(
        _("Valuta prijs per kavel"),
        ["EUR", "GMD"],
        horizontal=True,
        key="valuta_kavel_sidebar"
    )

    if valuta_keuze_sidebar == "EUR":
        prijs_per_kavel_eur = st.sidebar.number_input(
            _("Prijs per kavel (EUR)"),
            min_value=0.0,
            format="%.2f",
            value=0.0,
            key="prijs_per_plot_eur_sidebar"
        )
        prijs_per_kavel_gmd = round(prijs_per_kavel_eur * wisselkoers) if wisselkoers else 0.0
    else:
        prijs_per_kavel_gmd = st.sidebar.number_input(
            _("Prijs per kavel (GMD)"),
            min_value=0.0,
            format="%.0f",
            value=0.0,
            key="prijs_per_plot_gmd_sidebar"
        )
        prijs_per_kavel_eur = round(prijs_per_kavel_gmd / wisselkoers, 2) if wisselkoers else 0.0

    totaal_opbrengst_gmd = aantal_kavels * prijs_per_kavel_gmd
    totaal_opbrengst_eur = aantal_kavels * prijs_per_kavel_eur
    opbrengst_per_maand_gmd = totaal_opbrengst_gmd / verkoopperiode_maanden
    opbrengst_per_maand_eur = totaal_opbrengst_eur / verkoopperiode_maanden

    st.sidebar.info(
        _("💶 Totale opbrengst: {eur} ≈ {gmd}").format(
            eur=format_currency(totaal_opbrengst_eur, "EUR"),
            gmd=format_currency(totaal_opbrengst_gmd, "GMD")
        )
    )
    st.sidebar.info(
        _("📅 Opbrengst per maand: {eur} ≈ {gmd}").format(
            eur=format_currency(opbrengst_per_maand_eur, "EUR"),
            gmd=format_currency(opbrengst_per_maand_gmd, "GMD")
        )
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

# 📦 Verwachte opbrengst + kosten
if strategie == _("Verkavelen en verkopen"):
    verwachte_opbrengst = float(totaal_opbrengst_eur or 0.0)
    st.sidebar.info(
        _("Verwachte opbrengst (automatisch): {eur}").format(
            eur=format_currency(verwachte_opbrengst, "EUR")
        )
    )
else:
    verwachte_opbrengst = st.sidebar.number_input(
        _("Verwachte opbrengst (EUR)"),
        min_value=0.0, format="%.2f", value=0.0,
        key="sb_verwachte_opbrengst",
    )

kosten_qg = st.sidebar.number_input(
    _("Verwachte kosten Quadraat Global (EUR)"),
    min_value=0.0, format="%.2f", value=0.0,
    key="sb_kosten_qg",
)
kosten_extern = st.sidebar.number_input(
    _("Verwachte kosten Externen (EUR)"),
    min_value=0.0, format="%.2f", value=0.0,
    key="sb_kosten_extern",
)
verwachte_kosten = kosten_qg + kosten_extern
st.sidebar.info(_("Totaal verwachte kosten: € {kosten:,.2f}").format(kosten=verwachte_kosten))

status_toelichting = st.sidebar.text_area(
    _("Status / toelichting"),
    value="",
    key="sb_status_toelichting",
)

# 👥 Investeerders
st.sidebar.markdown("### " + _("👥 Investeerders"))
financieringsvorm = st.sidebar.radio(
    _("Financieringsvorm"),
    [_("Eigen beheer (geen externe investeerders)"), _("Met externe investeerders")],
    help=_("Kies ‘Eigen beheer’ als dit perceel 100% intern gefinancierd is.")
)

inv_input_data = st.session_state.get("investeerders_input", [])
investeerders = []

if financieringsvorm.startswith(_("Eigen beheer")):
    st.sidebar.info(_("ℹ️ Dit perceel staat in **eigen beheer** (geen externe investeerders)."))
    investeerders = []
else:
    aantal_investeerders = st.sidebar.number_input(
        _("Aantal externe investeerders"),
        min_value=1, max_value=10, value=1
    )

    for i in range(1, aantal_investeerders + 1):
        st.sidebar.markdown(_("#### Investeerder {i}").format(i=i))
        inv_data = inv_input_data[i - 1] if i <= len(inv_input_data) else {}
        naam = st.sidebar.text_input(_("Naam {i}").format(i=i), value=inv_data.get("naam", ""), key=f"inv_naam_{i}")
        bedrag_eur = st.sidebar.number_input(
            _("Bedrag {i} (EUR)").format(i=i), min_value=0.0, format="%.2f",
            value=inv_data.get("bedrag_eur", 0.0), key=f"inv_bedrag_eur_{i}"
        )
        bedrag = round(bedrag_eur * wisselkoers) if wisselkoers else 0
        rente = st.sidebar.number_input(
            _("Rente {i} (%)").format(i=i), min_value=0.0, max_value=100.0, step=0.1,
            value=inv_data.get("rente", 0.0) * 100, key=f"inv_rente_{i}"
        ) / 100
        winst = st.sidebar.number_input(
            _("Winstdeling {i} (%)").format(i=i), min_value=0.0, max_value=100.0, step=1.0,
            value=inv_data.get("winstdeling", 0.0) * 100, key=f"inv_winst_{i}"
        ) / 100
        rentetype = st.sidebar.selectbox(
            _("Rentevorm {i}").format(i=i), [_("maandelijks"), _("jaarlijks"), _("bij verkoop")],
            index=[_("maandelijks"), _("jaarlijks"), _("bij verkoop")].index(inv_data.get("rentetype", _("bij verkoop"))),
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

# 📋 Documenten
st.sidebar.markdown("### " + _("📋 Documenten"))
eigendomstype = st.sidebar.selectbox(
    _("Eigendomsvorm"),
    [_("Geregistreerd land")],
    index=0
)
st.sidebar.caption(_("ℹ️ Zowel *Customary land* als *Freehold land* worden in Gambia na registratie gelijk behandeld."))

def get_vereiste_documenten(perceel: dict = None, fase: str = None) -> list:
    if not fase:
        fase = perceel.get("dealstage", _("Aankoop")) if perceel else _("Aankoop")
    return documentvereisten_per_fase.get(fase, []).copy()

if snel_verkocht:
    docs_sidebar = []
else:
    docs_sidebar = get_vereiste_documenten(fase=_("Aankoop"))

uploads = {}
uploads_urls = {}

for doc in docs_sidebar:
    col1, col2 = st.sidebar.columns([1, 2])
    with col1:
        uploads[doc] = st.checkbox(_("{doc} aanwezig?").format(doc=doc), value=False)
    with col2:
        uploads_urls[doc] = st.text_input(
            _("Link naar {doc}").format(doc=doc),
            key=f"url_{doc}",
            label_visibility="collapsed"
        )
    if uploads.get(doc) and uploads_urls.get(doc):
        st.sidebar.markdown(
            f"<a href='{uploads_urls[doc]}' target='_blank'>📄 { _('Open {doc}').format(doc=doc) }</a>",
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

# 📌 Kaartfocus
kaart_focus = st.session_state.get("kaart_focus_buffer")

if kaart_focus and isinstance(kaart_focus, list) and len(kaart_focus) >= 1:
    m = folium.Map(
        tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google Hybrid"
    )
    m.fit_bounds(kaart_focus)
elif alle_coords:
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
    m = folium.Map(
        location=[13.29583, -16.74694],
        zoom_start=18,
        tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google Hybrid"
    )

# ➕ Teken-tool
Draw(export=False).add_to(m)

# ✅ Zorg dat uploads_urls altijd bestaat
for perceel in st.session_state.percelen:
    perceel.setdefault("uploads_urls", {})

# 📍 Plaats markers/polygons
for perceel in st.session_state.percelen:
    if not isinstance(perceel, dict):
        st.warning(_("Percel is geen dict maar {t}, wordt overgeslagen.").format(t=type(perceel)))
        continue

    polygon = perceel.get("polygon")
    tooltip = perceel.get("locatie", _("Onbekend"))

    investeerders = ", ".join(
        i.get('naam') if isinstance(i, dict) else str(i)
        for i in perceel.get('investeerders', [])
    ) or _("Geen")

    documenten_links = []
    for doc, aanwezig in perceel.get("uploads", {}).items():
        if aanwezig:
            url = perceel.get("uploads_urls", {}).get(doc, "")
            if url:
                documenten_links.append(f'<a href="{url}" target="_blank">{doc}</a>')
            else:
                documenten_links.append(doc)
    documenten = ", ".join(documenten_links) or _("Geen")

    popup_html = f"""
    <div style="font-size: 12px; line-height: 1.35; max-width: 260px; max-height: 300px; overflow-y: auto;">
        <b>📍 {_("Locatie")}:</b> {perceel.get('locatie', _('Onbekend'))}<br>
        <b>🗓️ {_("Aankoopdatum")}:</b> {format_date_eu(perceel.get('aankoopdatum'))}<br>
        <b>💰 {_("Aankoopprijs")}:</b> {format_currency(perceel.get('aankoopprijs', 0), 'GMD')}<br>
        <b>💶 {_("Aankoopprijs (EUR)")}:</b> {format_currency(perceel.get('aankoopprijs_eur', 0.0), 'EUR')}<br>
        <b>🔖 {_("Dealstage")}:</b> {perceel.get('dealstage', _('Onbekend'))}<br>
        <b>🏷️ {_("Eigendom")}:</b> {perceel.get('eigendomstype', _('Onbekend'))}<br>
        <b>🔹 {_("Wordt gesplitst")}:</b> { _('Ja') if perceel.get('wordt_gesplitst') else _('Nee') }<br>
        <b>👥 {_("Investeerders")}:</b> {investeerders}<br>
        <b>📄 {_("Documenten aanwezig")}:</b> {documenten}<br>
        <b>🎯 {_("Strategie")}:</b> {perceel.get('strategie', _('n.v.t.'))}<br>
        <b>⏳ {_("Doorlooptijd")}:</b> {format_date_eu(perceel.get('doorlooptijd'))}<br>
        <b>📈 {_("Verwachte opbrengst")}:</b> {format_currency(perceel.get('verwachte_opbrengst_eur', 0.0), 'EUR')}<br>
        <b>💸 {_("Verwachte kosten")}:</b> {format_currency(perceel.get('verwachte_kosten_eur', 0.0), 'EUR')}<br>
        <b>📝 {_("Status / toelichting")}:</b> {perceel.get('status_toelichting', '—')}
    </div>
    """

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

# 🗺️ Viewer
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
    if st.button(_("↩ Undo laatste wijziging"), key="undo_main", use_container_width=True):
        undo()
        st.rerun()

with col_reload:
    if st.button(_("📤 Percelen opnieuw laden"), key="reload_main", use_container_width=True):
        st.session_state["percelen"] = load_percelen_from_json()
        st.success(_("Percelen zijn opnieuw geladen."))
        st.session_state.pop("skip_load", None)
        st.rerun()

# --- 📍 Perceel selectie ---
percelen = st.session_state.get("percelen", [])
locaties = [p.get("locatie", _("Perceel {i}")).format(i=i+1) for i, p in enumerate(percelen)]

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

st.markdown("### " + _("📍 Kies perceel"))
st.markdown("<div class='selector'>", unsafe_allow_html=True)

def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

per_row = 3
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

# ===== Centrale afhandeling van ?del= en ?delask= =====
_qp = get_qp()
if "del" in _qp:
    try:
        idx = int(_qp["del"])
        if 0 <= idx < len(st.session_state["percelen"]):
            save_state()
            st.session_state["percelen"].pop(idx)
            save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
            st.success(_("Perceel verwijderd."))
    except Exception:
        st.error(_("Verwijderen mislukt."))
    set_qp()
    st.rerun()
elif "delask" in _qp:
    pass

# --- Toon alleen het geselecteerde perceel ---
for i, perceel in enumerate(percelen):
    if not isinstance(perceel, dict):
        st.warning(_("Percel op index {i} is ongeldig en wordt overgeslagen.").format(i=i))
        continue

    perceel.setdefault("uploads", {})
    perceel.setdefault("uploads_urls", {})

    if perceel.get("locatie") != keuze:
        continue

    huidige_fase = perceel.get("dealstage", _("Aankoop"))

    with st.expander(f"📍 {perceel.get('locatie', _('Perceel {i}').format(i=i+1))}", expanded=True):
        # Basisgegevens
        st.text_input(_("Locatie"), value=perceel.get("locatie", ""), key=f"edit_locatie_{i}", disabled=True)

        if st.button(_("🔍 Zoom in op {loc}").format(loc=perceel.get("locatie")), key=f"zoom_knop_{i}"):
            st.session_state["kaart_focus_buffer"] = perceel.get("polygon")
            st.rerun()

        if perceel.get("investeerders"):
            st.warning(_("ℹ️ Dit perceel heeft **externe investeerders**."))
        else:
            st.info(_("ℹ️ Dit perceel staat in **eigen beheer** (geen externe investeerders)."))

        perceel["wordt_gesplitst"] = (
            st.checkbox(
                _("Wordt perceel gesplitst?"),
                value=perceel.get("wordt_gesplitst", False),
                key=f"wordt_gesplitst_{i}",
            )
            if huidige_fase == _("Verkoop")
            else False
        )

        perceel["lengte"]  = st.number_input(_("📏 Lengte (m)"),  min_value=0, value=int(perceel.get("lengte", 0)),  key=f"edit_lengte_{i}")
        perceel["breedte"] = st.number_input(_("📐 Breedte (m)"), min_value=0, value=int(perceel.get("breedte", 0)), key=f"edit_breedte_{i}")

        perceel["eigendomstype"] = st.selectbox(_("Eigendomsvorm"), [_("Geregistreerd land")], index=0, key=f"eigendom_{i}")
        st.caption(_("ℹ️ Zowel *Customary land* als *Freehold land* worden in Gambia na registratie gelijk behandeld."))

        try:
            aankoopdatum_value = pd.to_datetime(perceel.get("aankoopdatum"), errors="coerce").date()
            if not pd.notnull(aankoopdatum_value):
                aankoopdatum_value = date.today()
        except Exception:
            aankoopdatum_value = date.today()
        perceel["aankoopdatum"] = st.date_input(_("🗓️ Aankoopdatum"), value=aankoopdatum_value, key=f"aankoopdatum_{i}").isoformat()

        # Investeerders
        st.markdown("#### " + _("👥 Investeerders"))
        huidige_investeerders = perceel.get("investeerders", []) or []
        nieuwe_investeerders = []

        if not huidige_investeerders:
            st.info(_("Geen investeerders geregistreerd voor dit perceel."))
        else:
            for j, inv in enumerate(huidige_investeerders):
                st.markdown(_("##### Investeerder {nr}").format(nr=j+1))
                naam = st.text_input(_("Naam investeerder {nr}").format(nr=j+1), value=inv.get("naam", ""), key=f"inv_naam_edit_{i}_{j}")
                bedrag_eur = st.number_input(
                    _("Bedrag {nr} (EUR)").format(nr=j+1),
                    min_value=0.0,
                    format="%.2f",
                    value=float(inv.get("bedrag_eur", 0.0)),
                    key=f"inv_bedrag_edit_{i}_{j}",
                )
                rente = (
                    st.number_input(
                        _("Rente {nr} (%)").format(nr=j+1),
                        min_value=0.0,
                        max_value=100.0,
                        step=0.1,
                        value=float(inv.get("rente", 0.0)) * 100,
                        key=f"inv_rente_edit_{i}_{j}",
                    ) / 100
                )
                winst = (
                    st.number_input(
                        _("Winstdeling {nr} (%)").format(nr=j+1),
                        min_value=0.0,
                        max_value=100.0,
                        step=1.0,
                        value=float(inv.get("winstdeling", 0.0)) * 100,
                        key=f"inv_winst_edit_{i}_{j}",
                    ) / 100
                )
                options = list(RENTETYPES.keys())             # ["monthly","yearly","at_sale"]
                labels = [RENTETYPES[o] for o in options]     # ["maandelijks","jaarlijks","bij verkoop"]

                current = inv.get("rentetype", "at_sale")
                if current not in options:
                    current = "at_sale"

                index = options.index(current)

                choice_label = st.selectbox(
                    _("Rentevorm {nr}").format(nr=j+1),
                    labels,
                    index=index,
                    key=f"inv_type_edit_{i}_{j}",
                )

                rentetype = options[labels.index(choice_label)]


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

        # Documenten
        st.markdown("#### " + _("📋 Documenten"))
        vereiste_docs = get_vereiste_documenten(perceel, huidige_fase)
        nieuwe_uploads, nieuwe_uploads_urls = {}, {}

        for doc in vereiste_docs:
            col1, col2 = st.columns([1, 3])
            with col1:
                nieuwe_uploads[doc] = st.checkbox(
                    _("{doc} aanwezig?").format(doc=doc),
                    value=perceel.get("uploads", {}).get(doc, False),
                    key=f"upload_{i}_{doc}"
                )
            with col2:
                nieuwe_uploads_urls[doc] = st.text_input(
                    _("Link naar {doc}").format(doc=doc),
                    value=perceel.get("uploads_urls", {}).get(doc, ""),
                    key=f"upload_url_{i}_{doc}"
                )
                if nieuwe_uploads[doc] and nieuwe_uploads_urls[doc]:
                    st.markdown(
                        f"<a href='{nieuwe_uploads_urls[doc]}' target='_blank'>📄 { _('Open {doc}').format(doc=doc) }</a>",
                        unsafe_allow_html=True,
                    )

        perceel["uploads"] = nieuwe_uploads
        perceel["uploads_urls"] = nieuwe_uploads_urls

        # Pipeline & navigatie
        st.markdown(render_pipeline(huidige_fase))
        _PIPELINE_FASEN = [_("Aankoop"), _("Omzetting / bewerking"), _("Verkoop"), _("Verkocht")]
        fase_index = _PIPELINE_FASEN.index(huidige_fase) if huidige_fase in _PIPELINE_FASEN else 0

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            if fase_index > 0:
                vorige_fase = _PIPELINE_FASEN[fase_index - 1]
                if st.button(_("⬅️ Vorige fase ({fase})").format(fase=vorige_fase), key=f"vorige_fase_{i}"):
                    perceel["dealstage"] = vorige_fase
                    save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                    st.session_state["skip_load"] = True
                    st.rerun()
        with col_f2:
            if fase_index < len(_PIPELINE_FASEN) - 1:
                volgende_fase = _PIPELINE_FASEN[fase_index + 1]
                if st.button(_("➡️ Volgende fase ({fase})").format(fase=volgende_fase), key=f"volgende_fase_{i}"):
                    perceel["dealstage"] = volgende_fase
                    save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                    st.session_state["skip_load"] = True
                    st.rerun()

        # Verkoopgegevens (gerealiseerd) — alleen bij fase Verkocht
        if huidige_fase == _("Verkocht"):
            st.markdown("#### " + _("💰 Verkoopgegevens (gerealiseerd)"))
            try:
                verkoop_value = pd.to_datetime(perceel.get("verkoopdatum"), errors="coerce").date()
                if not pd.notnull(verkoop_value):
                    verkoop_value = date.today()
            except Exception:
                verkoop_value = date.today()
            perceel["verkoopdatum"] = st.date_input(_("🗓️ Verkoopdatum"), value=verkoop_value, key=f"verkoopdatum_{i}").isoformat()

            _koers = perceel.get("wisselkoers") or locals().get("wisselkoers", None)
            valuta_keuze_verkocht = st.radio(_("Valuta verkoopprijs"), ["EUR", "GMD"], horizontal=True, key=f"valuta_verkoop_{i}")

            if valuta_keuze_verkocht == "EUR":
                prijs_eur = st.number_input(
                    _("Verkoopprijs (EUR)"),
                    min_value=0.0,
                    value=float(perceel.get("verkoopprijs_eur", 0.0)) if perceel.get("verkoopprijs_eur") else 0.0,
                    format="%.2f",
                    key=f"verkoopprijs_eur_{i}",
                )
                prijs_gmd = round(prijs_eur * _koers) if _koers else float(perceel.get("verkoopprijs", 0.0) or 0.0)
            else:
                prijs_gmd = st.number_input(
                    _("Verkoopprijs (GMD)"),
                    min_value=0.0,
                    value=float(perceel.get("verkoopprijs", 0.0) or 0.0),
                    format="%.0f",
                    key=f"verkoopprijs_gmd_{i}",
                )
                prijs_eur = round(prijs_gmd / _koers, 2) if _koers else float(perceel.get("verkoopprijs_eur", 0.0) or 0.0)

            perceel["verkoopprijs"] = prijs_gmd
            perceel["verkoopprijs_eur"] = prijs_eur

            if _koers:
                st.info(_("✅ Vastgelegd: {eur} ≈ {gmd} (koers {koers:.2f})").format(
                    eur=format_currency(prijs_eur, "EUR"),
                    gmd=format_currency(prijs_gmd, "GMD"),
                    koers=_koers
                ))
            else:
                st.info(_("✅ Vastgelegd (koers onbekend): bedragen niet omgerekend."))

        # Strategie & planning
        st.markdown("#### " + _("🌟 Strategie en planning"))
        strategie_opties = [
            _("Korte termijn verkoop"),
            _("Verkavelen en verkopen"),
            _("Zelf woningen bouwen"),
            _("Zelf bedrijf starten")
        ]
        
        options = list(STRATEGIE_OPTIES.keys())             # ["short_term","split_sell","self_build","self_company"]
        labels = [STRATEGIE_OPTIES[o] for o in options]     # vertaalde labels via _()

        current = perceel.get("strategie", "short_term")
        if current not in options:
            current = "short_term"

        index = options.index(current)

        choice_label = st.selectbox(
            _("Strategie"),
            labels,
            index=index,
            key=f"strategie_{i}",
        )

        perceel["strategie"] = options[labels.index(choice_label)]


        _k = perceel.get("wisselkoers") or locals().get("wisselkoers", None)
        aankoop_eur = float(perceel.get("aankoopprijs_eur", 0) or 0)

        if perceel["strategie"] == _("Verkavelen en verkopen"):
            try:
                start_val = pd.to_datetime(perceel.get("start_verkooptraject"), errors="coerce").date()
                if not pd.notnull(start_val):
                    start_val = date.today()
            except Exception:
                start_val = date.today()
            perceel["start_verkooptraject"] = st.date_input(_("🗓️ Start verkooptraject"), value=start_val, key=f"start_verkooptraject_{i}").isoformat()

            perceel["aantal_plots"] = st.number_input(
                _("Aantal kavels"),
                min_value=1,
                value=int(perceel.get("aantal_plots", 1)),
                key=f"aantal_plots_{i}"
            )
            valuta_keuze = st.radio(_("Valuta prijs per kavel"), ["EUR", "GMD"], horizontal=True, key=f"valuta_kavel_{i}")

            if valuta_keuze == "EUR":
                prijs_eur = st.number_input(
                    _("Prijs per kavel (EUR)"),
                    min_value=0.0,
                    value=float(perceel.get("prijs_per_plot_eur", 0.0)),
                    format="%.2f",
                    key=f"prijs_plot_eur_{i}",
                )
                prijs_gmd = round(prijs_eur * (_k or 0))
            else:
                prijs_gmd = st.number_input(
                    _("Prijs per kavel (GMD)"),
                    min_value=0.0,
                    value=float(perceel.get("prijs_per_plot_gmd", 0.0)),
                    format="%.0f",
                    key=f"prijs_plot_gmd_{i}",
                )
                prijs_eur = round(prijs_gmd / _k, 2) if _k else 0.0

            perceel["prijs_per_plot_eur"], perceel["prijs_per_plot_gmd"] = prijs_eur, prijs_gmd

            doorlooptijd_dt = pd.to_datetime(perceel.get("doorlooptijd"), errors="coerce")
            vandaag = date.today()
            if pd.notnull(doorlooptijd_dt):
                delta = (doorlooptijd_dt.year - vandaag.year) * 12 + (doorlooptijd_dt.month - vandaag.month)
                verkoopperiode_maanden = max(delta, 1)
            perceel["verkoopperiode_maanden"] = verkoopperiode_maanden

            totaal_opbrengst_gmd = perceel["aantal_plots"] * prijs_gmd
            totaal_opbrengst_eur = perceel["aantal_plots"] * prijs_eur
            perceel["totaal_opbrengst_gmd"] = totaal_opbrengst_gmd
            perceel["totaal_opbrengst_eur"] = totaal_opbrengst_eur
            perceel["opbrengst_per_maand_gmd"] = totaal_opbrengst_gmd / verkoopperiode_maanden
            perceel["opbrengst_per_maand_eur"] = totaal_opbrengst_eur / verkoopperiode_maanden

            perceel["verwachte_opbrengst_eur"] = totaal_opbrengst_eur

            st.info(_("💶 Totale opbrengst: {eur} ≈ {gmd}").format(
                eur=format_currency(totaal_opbrengst_eur, "EUR"),
                gmd=format_currency(totaal_opbrengst_gmd, "GMD")
            ))
            st.info(_("📅 Opbrengst per maand: {eur} ≈ {gmd}").format(
                eur=format_currency(perceel['opbrengst_per_maand_eur'], "EUR"),
                gmd=format_currency(perceel['opbrengst_per_maand_gmd'], "GMD")
            ))

            # Kosten
            st.markdown("#### " + _("💸 Kosten"))
            kosten_qg = st.number_input(
                _("Verwachte kosten Quadraat Global (EUR)"),
                min_value=0.0,
                value=float(perceel.get("verwachte_kosten_qg_eur", perceel.get("kosten_qg_eur", 0.0)) or 0.0),
                format="%.2f",
                key=f"verwachte_kosten_qg_{i}",
            )
            kosten_extern = st.number_input(
                _("Verwachte kosten Externen (EUR)"),
                min_value=0.0,
                value=float(perceel.get("verwachte_kosten_extern_eur", perceel.get("kosten_extern_eur", 0.0)) or 0.0),
                format="%.2f",
                key=f"verwachte_kosten_extern_{i}",
            )
            totaal_kosten = round((kosten_qg or 0) + (kosten_extern or 0), 2)
            st.info(_("**Totaal verwachte kosten:** € {k:,.2f}").format(k=totaal_kosten))

            perceel["verwachte_kosten_qg_eur"]     = kosten_qg
            perceel["verwachte_kosten_extern_eur"] = kosten_extern
            perceel["kosten_qg_eur"]               = kosten_qg
            perceel["kosten_extern_eur"]           = kosten_extern
            perceel["verwachte_kosten_eur"]        = totaal_kosten

            perceel["verwachte_winst_eur"] = totaal_opbrengst_eur - totaal_kosten - aankoop_eur
            st.success(_("📈 Netto verwachte winst: {eur}").format(eur=format_currency(perceel['verwachte_winst_eur'], "EUR")))

        else:
            perceel["verwachte_opbrengst_eur"] = st.number_input(
                _("Verwachte opbrengst (EUR)"),
                min_value=0.0,
                value=float(perceel.get("verwachte_opbrengst_eur", 0.0)),
                format="%.2f",
                key=f"verwachte_opbrengst_{i}",
            )

            st.markdown("#### " + _("💸 Kosten"))
            kosten_qg = st.number_input(
                _("Verwachte kosten Quadraat Global (EUR)"),
                min_value=0.0,
                value=float(perceel.get("verwachte_kosten_qg_eur", perceel.get("kosten_qg_eur", 0.0)) or 0.0),
                format="%.2f",
                key=f"verwachte_kosten_qg_{i}",
            )
            kosten_extern = st.number_input(
                _("Verwachte kosten Externen (EUR)"),
                min_value=0.0,
                value=float(perceel.get("verwachte_kosten_extern_eur", perceel.get("kosten_extern_eur", 0.0)) or 0.0),
                format="%.2f",
                key=f"verwachte_kosten_extern_{i}",
            )
            totaal_kosten = round((kosten_qg or 0) + (kosten_extern or 0), 2)
            st.info(_("**Totaal verwachte kosten:** € {k:,.2f}").format(k=totaal_kosten))

            perceel["verwachte_kosten_qg_eur"]     = kosten_qg
            perceel["verwachte_kosten_extern_eur"] = kosten_extern
            perceel["kosten_qg_eur"]               = kosten_qg
            perceel["kosten_extern_eur"]           = kosten_extern
            perceel["verwachte_kosten_eur"]        = totaal_kosten

            perceel["verwachte_winst_eur"] = (perceel["verwachte_opbrengst_eur"] or 0.0) - totaal_kosten - aankoop_eur
            st.success(_("📈 Netto verwachte winst: {eur}").format(eur=format_currency(perceel['verwachte_winst_eur'], "EUR")))

        # Verwachte einddatum
        try:
            eind_value = pd.to_datetime(perceel.get("doorlooptijd"), errors="coerce").date()
            if not pd.notnull(eind_value):
                eind_value = date.today()
        except Exception:
            eind_value = date.today()
        perceel["doorlooptijd"] = st.date_input(
            _("Verwachte einddatum"),
            value=eind_value,
            key=f"doorlooptijd_{i}",
        ).isoformat()

        perceel["status_toelichting"] = st.text_area(
            _("📜 Status / toelichting"),
            value=perceel.get("status_toelichting", ""),
            key=f"status_toelichting_{i}",
        )

        # Opslaan + Verwijderen
        if is_admin:
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(_("💾 Opslaan wijzigingen ({loc})").format(loc=perceel.get('locatie')), key=f"opslaan_bewerken_{i}"):
                    save_state()
                    save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                    st.cache_data.clear()
                    st.success(_("Wijzigingen aan {loc} opgeslagen.").format(loc=perceel.get('locatie')))
            with col2:
                confirm_key = f"confirm_delete_{i}"
                if not st.session_state.get(confirm_key, False):
                    if st.button(_("🗑 Verwijder"), key=f"delete_{i}"):
                        st.session_state[confirm_key] = True
                else:
                    with st.error(_("⚠️ Weet je zeker dat je dit perceel wilt verwijderen? Dit kan niet ongedaan gemaakt worden.")):
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button(_("✅ Ja, definitief verwijderen"), key=f"do_delete_{i}"):
                                save_state()
                                st.session_state["percelen"].pop(i)
                                save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
                                st.session_state.pop(confirm_key, None)
                                st.success(_("Perceel verwijderd."))
                                st.rerun()
                        with c2:
                            if st.button(_("↩ Nee, annuleren"), key=f"cancel_delete_{i}"):
                                st.session_state.pop(confirm_key, None)
                                st.info(_("Verwijderen geannuleerd."))
        else:
            st.info(_("🔐 Alleen admins kunnen wijzigingen opslaan of percelen verwijderen."))

# 📍 Coördinaten invoer
st.sidebar.markdown("### " + _("📍 Coördinaten invoer"))
coord_type = st.sidebar.radio(_("Coördinatentype"), [_("UTM"), _("Latitude/Longitude")], index=1)

polygon_coords = []
for idx in range(1, 6):
    col1, col2 = st.sidebar.columns(2)
    x = col1.text_input(f"X{idx}", key=f"x_{idx}")
    y = col2.text_input(f"Y{idx}", key=f"y_{idx}")
    try:
        if coord_type == _("UTM"):
            transformer = Transformer.from_crs("epsg:32628", "epsg:4326", always_xy=True)
            lon, lat = transformer.transform(float(x), float(y))
        else:
            lat = float(y)
            lon = float(x)
        if lat and lon:
            polygon_coords.append([lat, lon])
    except Exception:
        pass

if len(polygon_coords) > 0 and len(polygon_coords) < 3:
    st.sidebar.error(_("❗ Polygon moet minstens 3 punten bevatten."))

# 🔁 Overschrijf polygon_coords alleen als er getekend is in Folium
if output := st.session_state.get("output", None):
    last_drawing = output.get("last_active_drawing", None)
    if last_drawing:
        geom = last_drawing.get("geometry", {})
        if geom.get("type") == "Polygon":
            coords = geom.get("coordinates", [[]])[0]
            polygon_coords = [[c[1], c[0]] for c in coords]

# 🧹 Migratieknop (alleen admin)
if is_admin:
    if st.sidebar.button(_("🧹 Migratie uitvoeren (eenmalig)")):
        migrate_percelen() 

# ➕ Perceel toevoegen (alleen admin)
if is_admin:
    toevoegen = st.sidebar.button(_("➕ Voeg perceel toe"))
else:
    st.sidebar.info(_("🔒 Alleen admins kunnen percelen toevoegen."))
    toevoegen = False

if is_admin and toevoegen:
    save_state()  

    if not locatie:
        st.sidebar.error(_("❗ Vul een locatie in."))
    elif any(p.get("locatie") == locatie for p in st.session_state["percelen"]):
        st.sidebar.warning(_("⚠️ Er bestaat al een perceel met deze locatie."))
    elif financieringsvorm.startswith(_("Met externe")) and not investeerders and not snel_verkocht:
        st.sidebar.error(_("❗ Voeg minimaal één externe investeerder toe óf kies ‘Eigen beheer’."))
    elif len(polygon_coords) < 3:
        st.sidebar.error(_("❗ Polygon moet minstens 3 punten bevatten."))
    else:
        dealstage = _("Verkoop") if snel_verkocht else _("Aankoop")

        if isinstance(investeerders, str):
            investeerders = [{
                "naam": investeerders,
                "bedrag": 0,
                "bedrag_eur": 0,
                "rente": 0.0,
                "winstdeling": 0.0,
                "rentetype": _("bij verkoop")
            }]

        if strategie == _("Verkavelen en verkopen"):
            start_traject = st.session_state.get("start_verkooptraject_sidebar", date.today())
            aantal_kavels = st.session_state.get("sidebar_aantal_kavels", 1)
            prijs_per_plot_eur = st.session_state.get("prijs_per_plot_eur_sidebar", 0.0)
            prijs_per_plot_gmd = st.session_state.get("prijs_per_plot_gmd_sidebar", 0.0)
            verkoopperiode_maanden = st.session_state.get("periode_sidebar", 0)

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

        verwachte_kosten = kosten_qg + kosten_extern
        verwachte_winst_eur = totaal_opbrengst_eur - verwachte_kosten - aankoopprijs_eur

        perceel = {
            "locatie": locatie,
            "dealstage": dealstage,
            "wordt_gesplitst": False,

            "investeerders": investeerders,
            "lengte": lengte,
            "breedte": breedte,
            "eigendomstype": eigendomstype,
            "polygon": polygon_coords,

            "uploads": uploads,
            "uploads_urls": uploads_urls,

            "aankoopdatum": aankoopdatum.strftime("%Y-%m-%d"),
            "verkoopdatum": verkoopdatum.strftime("%Y-%m-%d") if isinstance(verkoopdatum, date) else verkoopdatum,
            "aankoopprijs": aankoopprijs,
            "aankoopprijs_eur": aankoopprijs_eur,
            "wisselkoers": wisselkoers,
            "verkoopprijs": verkoopprijs,
            "verkoopprijs_eur": verkoopprijs_eur if wisselkoers else None,

            "strategie": strategie,
            "verwachte_opbrengst_eur": totaal_opbrengst_eur,
            "kosten_qg_eur": kosten_qg,
            "kosten_extern_eur": kosten_extern,
            "verwachte_kosten_eur": verwachte_kosten,
            "verwachte_winst_eur": verwachte_winst_eur,
            "doorlooptijd": doorlooptijd_datum.isoformat() if isinstance(doorlooptijd_datum, date) else "",
            "start_verkooptraject": start_traject.strftime("%Y-%m-%d") if isinstance(start_traject, date) else None,
            "status_toelichting": status_toelichting,

            "aantal_plots": aantal_kavels,
            "prijs_per_plot_eur": prijs_per_plot_eur,
            "prijs_per_plot_gmd": prijs_per_plot_gmd,
            "verkoopperiode_maanden": verkoopperiode_maanden,
            "totaal_opbrengst_eur": totaal_opbrengst_eur,
            "totaal_opbrengst_gmd": totaal_opbrengst_gmd,
            "opbrengst_per_maand_eur": opbrengst_per_maand_eur,
            "opbrengst_per_maand_gmd": opbrengst_per_maand_gmd
        }

        st.session_state.percelen.append(perceel)
        save_percelen_as_json(prepare_percelen_for_saving(st.session_state["percelen"]))
        st.sidebar.success(_("Perceel '{loc}' toegevoegd en opgeslagen.").format(loc=locatie))

        st.session_state["skip_load"] = False
        st.cache_data.clear()
        st.rerun()

# ==== Groq-chatblok – Percelenbeheer =========================================

tab_chat, = st.tabs([_("💬 Chat (Groq)")])

with tab_chat:
    st.caption(_("Copilot: beheer-chat met kaartfocus en analyses (lokale tools, NL-intent)."))

    # ---------- Helpers ----------
    import json, re
    from datetime import date, datetime
    from difflib import get_close_matches
    from geopy.distance import geodesic
    from utils import hoofdsteden_df  # voor nabijste_regio

    def _percelen_raw():
        return st.session_state.get("percelen", []) or []

    def _normalize_perceel(p: dict) -> dict:
        p = dict(p or {})
        p.setdefault("uploads", {}); p.setdefault("uploads_urls", {})
        p.setdefault("investeerders", []); p.setdefault("dealstage", _("Aankoop"))
        for k in ("lengte","breedte","aankoopprijs_eur","verwachte_opbrengst_eur","verwachte_kosten_eur"):
            try: p[k] = float(p.get(k) or 0)
            except: p[k] = 0.0
        p["polygon"] = p.get("polygon") or []
        return p

    def _percelen_norm():
        return [_normalize_perceel(p) for p in _percelen_raw()]

    def _closest_loc(name: str) -> str | None:
        locs = [p.get("locatie","") for p in _percelen_raw()]
        m = get_close_matches((name or "").strip(), locs, n=1, cutoff=0.6)
        return m[0] if m else None

    def _resolve_loc(name: str):
        """Exact → fuzzy → None. Return (perceel_dict, suggestie)."""
        for p in _percelen_norm():
            if (p.get("locatie","").lower().strip() == (name or "").lower().strip()):
                return p, None
        guess = _closest_loc(name)
        if guess:
            for p in _percelen_norm():
                if p.get("locatie")==guess:
                    return p, None
            return None, guess
        return None, None

    def _parse_loc_list(txt: str) -> list[str]:
        """Sta lijsten toe: 'Sanyang 1, Kunkujang 2 en Tanji' → ['Sanyang 1','Kunkujang 2','Tanji']"""
        m = re.search(r"(?:voor|van)\s+(.+)$", (txt or "").lower())
        chunk = m.group(1) if m else txt
        parts = re.split(r"\s*,\s*|\s+en\s+", chunk or "")
        return [p for p in (x.strip() for x in parts) if p]

    # ---------- Lokale tools (single + geo) ----------
    def get_aantal_percelen():
        return {"aantal": len(_percelen_raw())}

    def list_locaties(limit: int = 20):
        ps = _percelen_raw()
        locs = [p.get("locatie", _("Onbekend")) for p in ps if isinstance(p, dict)]
        return {"locaties": locs[:max(1, int(limit))], "totaal": len(locs)}

    def laatste_toegevoegd():
        ps = _percelen_raw()
        if not ps:
            return {"laatste": None}
        p = ps[-1]
        return {"laatste": {"locatie": p.get("locatie"), "aankoopdatum": p.get("aankoopdatum")}}

    def _get_doc_requirements():
        return documentvereisten_per_fase

    def check_missing_docs(only_missing: bool = True):
        req = _get_doc_requirements()
        ps = _percelen_raw()
        items = []
        for p in ps:
            fase = p.get("dealstage", _("Aankoop"))
            must = list(req.get(fase, []))
            have = p.get("uploads") or {}
            urls = p.get("uploads_urls") or {}
            missing = [d for d in must if not have.get(d)]
            present = [{"doc": d, "url": urls.get(d, "")} for d in must if have.get(d)]
            row = {
                "locatie": p.get("locatie", _("Onbekend")),
                "fase": fase,
                "ontbrekend": missing,
                "aanwezig": present
            }
            if (not only_missing) or missing:
                items.append(row)
        summary = {
            "totaal_percelen": len(ps),
            "met_ontbrekende_docs": sum(1 for x in items if x["ontbrekend"]),
            "fases": sorted(list(_get_doc_requirements().keys())),
        }
        return {"summary": summary, "items": items}

    def summary_perceel(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p:
            return {
                "error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})
            }
        opb = float(p.get("verwachte_opbrengst_eur") or 0)
        kos = float(p.get("verwachte_kosten_eur") or 0)
        ank = float(p.get("aankoopprijs_eur") or 0)
        w = opb - kos - ank
        req = _get_doc_requirements()
        must = req.get(p.get("dealstage", _("Aankoop")), [])
        have = p.get("uploads") or {}
        return {
            "locatie": p.get("locatie"),
            "fase": p.get("dealstage"),
            "aankoop_eur": ank,
            "opbrengst_eur": opb,
            "kosten_eur": kos,
            "verwachte_winst_eur": w,
            "docs_ok": [d for d in must if have.get(d)],
            "docs_missing": [d for d in must if not have.get(d)],
        }

    def investor_report():
        agg = {}
        for p in _percelen_norm():
            for inv in (p.get("investeerders") or []):
                naam = (inv.get("naam") or _("Onbekend")).strip()
                a = agg.setdefault(naam, {"totaal_inleg_eur": 0.0, "percelen": 0, "rentetypes": set()})
                a["totaal_inleg_eur"] += float(inv.get("bedrag_eur") or 0)
                a["percelen"] += 1
                if inv.get("rentetype"):
                    a["rentetypes"].add(inv.get("rentetype"))
        for v in agg.values():
            v["rentetypes"] = sorted(list(v["rentetypes"]))
        return {"investeerders": agg}

    def advies_perceel(locatie: str):
        try:
            from utils import beoordeel_perceel_modulair, read_marktprijzen, hoofdsteden_df as _hdf
            markt = read_marktprijzen()
        except Exception:
            beoordeel_perceel_modulair = None
            markt = None
            _hdf = None

        target, sug = _resolve_loc(locatie)
        if not target:
            return {
                "error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})
            }

        if callable(beoordeel_perceel_modulair) and markt is not None and _hdf is not None:
            try:
                score, toel, adv = beoordeel_perceel_modulair(target, markt, _hdf)
                return {"locatie": target.get("locatie"), "score": score, "toelichting": toel, "advies": adv}
            except Exception:
                pass

        ank = float(target.get("aankoopprijs_eur") or 0)
        opb = float(target.get("verwachte_opbrengst_eur") or 0)
        kos = float(target.get("verwachte_kosten_eur") or 0)
        winst = opb - kos - ank
        score = (1 if winst > 0 else -1) + (1 if (opb > 0 and ank > 0 and opb/ank >= 1.2) else 0)
        advies = _("Kopen") if score >= 2 else (_("Twijfel") if score == 1 else _("Mijden"))
        toel = _("Verwachte winst €{w}").format(w=f"{winst:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        return {"locatie": target.get("locatie"), "score": score, "toelichting": toel, "advies": advies}

    # ---------- Extra tools: geo & metrics ----------
    def get_totale_winst():
        ps = _percelen_norm()
        opb = sum(float(p.get("verwachte_opbrengst_eur") or 0) for p in ps)
        kos = sum(float(p.get("verwachte_kosten_eur") or 0) for p in ps)
        ank = sum(float(p.get("aankoopprijs_eur") or 0) for p in ps)
        return {"opbrengst": opb, "kosten": kos, "aankoop": ank, "winst": opb - kos - ank}

    def find_deadlines(days: int = 30):
        ps = _percelen_raw()
        today = date.today()
        due = []
        for p in ps:
            d = p.get("doorlooptijd")
            if not d:
                continue
            try:
                dt = datetime.fromisoformat(d).date()
                delta = (dt - today).days
                if delta <= days:
                    due.append({
                        "locatie": p.get("locatie"),
                        "fase": p.get("dealstage"),
                        "einddatum": d,
                        "dagen_tot": delta
                    })
            except Exception:
                pass
        return {"horizon_dagen": days, "items": sorted(due, key=lambda x: x["dagen_tot"])}

    def score_readiness():
        req = _get_doc_requirements()
        rows = []
        for p in _percelen_norm():
            fase = p.get("dealstage", _("Aankoop"))
            must = req.get(fase, [])
            have = p.get("uploads") or {}
            docs_ok = sum(1 for d in must if have.get(d))
            docs_ratio = docs_ok / max(1, len(must))
            winst = float(p.get("verwachte_winst_eur") or 0)
            score = round(60 * docs_ratio + 40 * (1 if winst > 0 else 0), 1)
            rows.append({
                "locatie": p.get("locatie"),
                "fase": fase,
                "docs_ok": f"{docs_ok}/{len(must)}",
                "winst": winst,
                "score": score
            })
        return {"scores": sorted(rows, key=lambda x: x["score"], reverse=True)}

    def focus_map_perceel(locatie: str):
        p, sug = _resolve_loc(locatie)
        if p:
            st.session_state["kaart_focus_buffer"] = p.get("polygon")
            return {"zoomed_to": p.get("locatie")}
        return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}

    def get_docs_perceel(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p:
            return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        have = p.get("uploads") or {}
        urls = p.get("uploads_urls") or {}
        docs = [{"doc": d, "url": urls.get(d, ""), "aanwezig": bool(v)} for d, v in (have.items() or [])]
        return {"locatie": p.get("locatie"), "fase": p.get("dealstage"), "docs": docs}

    def simulate_fx(delta_pct: float = -10):
        ps = _percelen_norm()
        out = []
        for p in ps:
            opb = float(p.get("verwachte_opbrengst_eur") or 0)
            kos = float(p.get("verwachte_kosten_eur") or 0)
            ank = float(p.get("aankoopprijs_eur") or 0)
            opb_new = opb * (1 + delta_pct/100.0)
            out.append({
                "locatie": p.get("locatie"),
                "winst_oud": opb - kos - ank,
                "winst_nieuw": round(opb_new - kos - ank, 2)
            })
        return {"delta_pct": delta_pct, "items": out}

    def get_coordinaten(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p:
            return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        poly = p.get("polygon") or []
        if not poly:
            return {"locatie": p.get("locatie"), "coords": None, "message": _("Geen polygon opgeslagen")}
        lat = sum(pt[0] for pt in poly) / len(poly)
        lon = sum(pt[1] for pt in poly) / len(poly)
        st.session_state["kaart_focus_buffer"] = poly
        return {
            "locatie": p.get("locatie"),
            "poly_points": len(poly),
            "centroid": {"lat": round(lat, 6), "lon": round(lon, 6)},
            "first_point": {"lat": poly[0][0], "lon": poly[0][1]}
        }

    def get_bbox(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p or not p.get("polygon"):
            return {"error": _("Geen polygon voor '{loc}'").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        lats = [pt[0] for pt in p["polygon"]]
        lons = [pt[1] for pt in p["polygon"]]
        return {"locatie": p.get("locatie"), "bbox": [[min(lats), min(lons)], [max(lats), max(lons)]]}

    def area_m2(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p:
            return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        L = float(p.get("lengte") or 0)
        B = float(p.get("breedte") or 0)
        if L > 0 and B > 0:
            return {"locatie": p["locatie"], "oppervlakte_m2": L * B, "bron": _("lengte × breedte")}
        return {"locatie": p["locatie"], "oppervlakte_m2": None, "message": _("Geen lengte/breedte bekend")}

    def prijs_per_m2(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p:
            return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        prijs = float(p.get("aankoopprijs_eur") or 0)
        L = float(p.get("lengte") or 0)
        B = float(p.get("breedte") or 0)
        opp = L * B if L > 0 and B > 0 else 0
        if prijs > 0 and opp > 0:
            return {"locatie": p["locatie"], "prijs_per_m2_eur": round(prijs/opp, 2), "opp_m2": opp}
        return {"locatie": p["locatie"], "prijs_per_m2_eur": None, "message": _("Ontbrekende prijs of afmetingen")}

    def nabijste_regio(locatie: str):
        p, sug = _resolve_loc(locatie)
        if not p or not p.get("polygon"):
            return {"error": _("Geen polygon voor '{loc}'").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        lat = sum(pt[0] for pt in p["polygon"]) / len(p["polygon"])
        lon = sum(pt[1] for pt in p["polygon"]) / len(p["polygon"])
        best = None
        bestkm = 1e9
        for _, r in hoofdsteden_df.iterrows():
            km = geodesic((lat, lon), (r["Latitude"], r["Longitude"])).km
            if km < bestkm:
                bestkm, best = km, r["regio"]
        return {
            "locatie": p.get("locatie"),
            "centroid": {"lat": round(lat, 6), "lon": round(lon, 6)},
            "nabijste_regio": best,
            "afstand_km": round(bestkm, 2)
        }

    # ---------- Batch/wijzere tools ----------
    def summary_all():
        return {"items": [summary_perceel(p.get("locatie", "")) for p in _percelen_norm()]}

    def docs_all():
        return check_missing_docs(only_missing=False)

    def readiness_top(n: int = 5):
        res = score_readiness().get("scores", [])
        return {"top": res[:max(1, int(n))]}

    def advies_all():
        out = []
        for p in _percelen_norm():
            try:
                out.append(advies_perceel(p.get("locatie")))
            except Exception:
                pass
        return {"adviezen": out}

    def rank_by(field: str = "verwachte_winst_eur", n: int = 5, desc: bool = True):
        ps = _percelen_norm()
        rows = []
        for p in ps:
            val = float(p.get(field) or 0)
            rows.append({"locatie": p.get("locatie"), field: val})
        rows.sort(key=lambda r: r[field], reverse=bool(desc))
        return {"veld": field, "top": rows[:max(1, int(n))]}

    # ---------- Mapping functies ----------
    FUNCTIONS = {
        # single
        "get_aantal_percelen": get_aantal_percelen,
        "list_locaties": list_locaties,
        "laatste_toegevoegd": laatste_toegevoegd,
        "check_missing_docs": check_missing_docs,
        "summary_perceel": summary_perceel,
        "investor_report": investor_report,
        "advies_perceel": advies_perceel,
        "get_totale_winst": get_totale_winst,
        "find_deadlines": find_deadlines,
        "score_readiness": score_readiness,
        "focus_map_perceel": focus_map_perceel,
        "get_docs_perceel": get_docs_perceel,
        "simulate_fx": simulate_fx,
        "get_coordinaten": get_coordinaten,
        "get_bbox": get_bbox,
        "area_m2": area_m2,
        "prijs_per_m2": prijs_per_m2,
        "nabijste_regio": nabijste_regio,
        # batch
        "summary_all": summary_all,
        "docs_all": docs_all,
        "readiness_top": readiness_top,
        "advies_all": advies_all,
        "rank_by": rank_by,
    }

    # ---------- Intent-router (NL + synoniemen + geo + fuzzy) ----------
    def route_intent(txt: str):
        t = (txt or "").lower().strip()

        def has_any(s, words): 
            return any(w in s for w in words)

        perceel_hit = has_any(t, ["perceel","percelen","grondstuk","kavel","plot","plots"])

        # Aantal / lijst / laatste
        if perceel_hit and has_any(t, ["hoeveel","aantal"]):
            return "get_aantal_percelen", {}
        if (has_any(t, ["lijst","toon","geef","laat zien"]) and "locat" in t) or "locaties" in t:
            m = (re.search(r"limit\s*=\s*(\d+)", t) or re.search(r"\btop\s+(\d+)\b", t))
            limit = int(m.group(1)) if m else 20
            return "list_locaties", {"limit": limit}
        if has_any(t, ["laatste","recent","recentste","meest recent"]):
            return "laatste_toegevoegd", {}

        # Geo: waar ligt / coördinaten / polygon / bbox
        loc = (
            re.search(r"waar\s+ligt\s+(.+)$", t)
            or re.search(r"co[oö]rdina[at]{1,2}en\s+(?:van|voor)\s+(.+)$", t)
            or re.search(r"polygon\s+(?:van|voor)\s+(.+)$", t)
            or re.search(r"bbox\s+(?:van|voor)\s+(.+)$", t)
        )
        if loc:
            name = loc.group(loc.lastindex).strip()
            if "bbox" in t:
                return "get_bbox", {"locatie": name}
            return "get_coordinaten", {"locatie": name}

        # Kaartfocus
        if any(w in t for w in ["zoom","kaart","focus","toon op kaart"]):
            m = re.search(r"(zoom|kaart|focus)\s+(op\s+)?(.+)$", t)
            if m:
                return "focus_map_perceel", {"locatie": m.group(3).strip()}

        # Documenten
        if "alle" in t and "document" in t:
            return "docs_all", {}
        if has_any(t, ["document","documenten","docs","papieren"]) and has_any(t, ["ontbreek","ontbreken","mis","missen","compleet","in orde"]):
            return "check_missing_docs", {}
        m = re.search(r"(documenten|docs|papieren)\s+(voor|van)\s+(.+)$", t)
        if m:
            return "get_docs_perceel", {"locatie": m.group(3).strip()}

        # Samenvatting
        if "alle" in t and perceel_hit and has_any(t, ["samenvat","samenvatting","overzicht"]):
            return "summary_all", {}
        m = re.search(r"(samenvatting|summary|financ)[^\w]+(voor|van)\s+(.+)$", t)
        if m and perceel_hit:
            locs = _parse_loc_list(txt)
            if len(locs) > 1:
                return "summary_all", {}
            return "summary_perceel", {"locatie": locs[0]}

        # Prijs/opp/regio
        m = re.search(r"(?:prijs.*m2|€/m2|euro per m2).*(?:van|voor)\s+(.+)$", t)
        if m:
            return "prijs_per_m2", {"locatie": m.group(1).strip()}
        m = re.search(r"(?:opp(?:ervlakte)?|m2).*(?:van|voor)\s+(.+)$", t)
        if m:
            return "area_m2", {"locatie": m.group(1).strip()}
        m = re.search(r"(?:nabijste|dichtstbijzijnde)\s+regio\s+(?:van|voor)\s+(.+)$", t)
        if m:
            return "nabijste_regio", {"locatie": m.group(1).strip()}

        # Investeerdersrapport
        if has_any(t, ["investeerder","investeerders","investor","investors"]) and has_any(t, ["rapport","overzicht","report"]):
            return "investor_report", {}

        # Advies
        if "advies" in t and "alle" in t and perceel_hit:
            return "advies_all", {}
        m = re.search(r"(advies|beoordeel|beoordeling|goede?\s*koop)\s+(voor|van)\s+(.+)$", t)
        if m and perceel_hit:
            locs = _parse_loc_list(txt)
            if len(locs) > 1:
                return "advies_all", {}
            return "advies_perceel", {"locatie": locs[0]}

        # Totale winst
        if "winst" in t or "profit" in t:
            return "get_totale_winst", {}

        # Deadlines
        if any(w in t for w in ["deadline","deadlines","einddatum","einddata","binnen"]):
            m = re.search(r"binnen\s*(\d+)\s*dag", t)
            days = int(m.group(1)) if m else 30
            return "find_deadlines", {"days": days}

        # Readiness / verkoopklaar & Top N
        m = re.search(r"(?:meest|hoogste)\s+(verkoopklaar(?:heid)?|readiness|winst)[^\d]*(\d+)?", t)
        if any(w in t for w in ["verkoopklaar","verkoopklaarheid","readiness","klaar voor verkoop","risico","score"]):
            return "score_readiness", {}
        if m:
            n = int(m.group(2) or 5)
            if "winst" in m.group(1):
                return "rank_by", {"field": "verwachte_winst_eur", "n": n, "desc": True}
            return "readiness_top", {"n": n}

        # FX simulatie
        m = re.search(r"(fx|wisselkoers|eur).*(\+|-)?\s*(\d+)\s*%", t)
        if m:
            sign = -1 if m.group(2) == "-" else 1
            return "simulate_fx", {"delta_pct": sign * int(m.group(3))}

        # Fuzzy geo fallback
        m = re.search(r"waar\s+ligt\s+(.+)$", t)
        if m:
            guess = _closest_loc(m.group(1))
            if guess:
                return "get_coordinaten", {"locatie": guess}

        return None, None

    # ---------- Chatgeschiedenis ----------
    if "chat_history_tools_beheer" not in st.session_state:
        st.session_state.chat_history_tools_beheer = []
    for m in st.session_state.chat_history_tools_beheer:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    # Toggle om lokale tools te gebruiken
    use_tools = st.toggle(_("🔧 Tools gebruiken (lokaal)"), value=True, key="beheer_tools_toggle")

    # ---------- Chat-afhandeling ----------
    if prompt := st.chat_input(_("Typ je beheer-vraag…"), key="beheer_chat_input"):
        st.session_state.chat_history_tools_beheer.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        base_messages = [
            {"role": "system", "content": _(
                "Je bent een beknopte NL-copilot voor Percelenbeheer. "
                "Regels: (1) Probeer eerst een lokale tool; (2) TOOL_RESULT is waarheid; "
                "Kies batch-varianten bij 'alle' of meerdere percelen; gebruik fuzzy-matching voor locatienamen "
                "en toon 'suggestie' als beschikbaar. Antwoord in 1–3 zinnen; bedragen in EUR; "
                "coördinaten als 'lat, lon' (6 decimalen)."
            )},
            *st.session_state.chat_history_tools_beheer,
        ]

        first_answer = groq_chat(base_messages)

        fname, fargs = route_intent(prompt) if use_tools else (None, None)
        if fname:
            try:
                out = FUNCTIONS[fname](**(fargs or {}))
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {e}"}

            # Suggestie bij typefout locatie
            if isinstance(out, dict) and out.get("error") and fargs and fargs.get("locatie"):
                guess = _closest_loc(fargs["locatie"])
                if guess:
                    out["suggestie"] = _("Bedoelde je '{sug}'?").format(sug=guess)

            tool_note = f"TOOL_RESULT {fname}: {json.dumps(out, ensure_ascii=False)}"
            messages2 = base_messages + [
                {"role": "assistant", "content": tool_note},
                {"role": "system", "content": _("Vat TOOL_RESULT kort samen (1–3 zinnen), noem getallen expliciet.")}
            ]
            answer = groq_chat(messages2)
        else:
            answer = first_answer or _(
                "Voorbeelden: ‘waar ligt <locatie>’, ‘bbox van <locatie>’, "
                "‘prijs per m2 van <locatie>’, ‘welke percelen missen documenten?’, "
                "‘verkoopklaar top 5’, ‘zoom op <locatie>’."
            )

        with st.chat_message("assistant"):
            # Bugfix: nette fallback i.p.v. stringconstructie
            st.markdown(answer or _("Geen antwoord"))

            # Quick actions bij geo/samenvatting
            try:
                if fname in ("get_coordinaten", "summary_perceel") and fargs and fargs.get("locatie"):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if st.button(_("🔍 Zoom op kaart"), key=f"zoom_{fargs['locatie']}"):
                            FUNCTIONS["focus_map_perceel"](locatie=fargs["locatie"])
                            st.rerun()
                    with c2:
                        st.write(_("📄 Typ: documenten van {loc}").format(loc=fargs["locatie"]))
                    with c3:
                        st.write(_("ℹ️ Typ: samenvatting van {loc}").format(loc=fargs["locatie"]))
            except Exception:
                pass

        st.session_state.chat_history_tools_beheer.append({"role": "assistant", "content": answer})

# ==== einde Groq-chatblok – Percelenbeheer ====================================



