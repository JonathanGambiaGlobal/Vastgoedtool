import streamlit as st

st.image("QG.png", width=180) 

import pandas as pd
import numpy as np
import json
import requests
import pydeck as pdk
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from groq import Groq

import utils

from utils import (
    get_ai_config,
    language_selector,
    build_rentebetalingen,
    get_exchange_rate_eur_to_gmd,
    get_exchange_rate_volatility,
    load_percelen_from_json,
    format_currency,
    analyse_portfolio_perceel,
    analyse_verkocht_perceel,
    verdeel_winst,
)

# auth
from auth import login_check

# 🔐 login eerst
login_check()

# 🌐 taal instellen
_, n_ = language_selector()

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

# --- Data laden ---
if "percelen" not in st.session_state or not st.session_state["percelen"]:
    st.session_state["percelen"] = load_percelen_from_json()

# --- Titel & Koersen ---
st.title(_("Vastgoeddashboard – Gambia"))
wisselkoers = get_exchange_rate_eur_to_gmd()
volatiliteit_pct = get_exchange_rate_volatility()

col1, col2 = st.columns(2)
with col1:
    if wisselkoers:
        st.metric(_("💶 Live wisselkoers (EUR → GMD)"), f"{wisselkoers:.2f}")
    else:
        st.warning(_("Wisselkoers niet beschikbaar."))
with col2:
    if volatiliteit_pct is not None:
        label = _("🟢 Laag") if volatiliteit_pct < 1 else (_("🟡 Gemiddeld") if volatiliteit_pct < 2 else _("🔴 Hoog"))
        st.metric(_("📉 Wisselkoersvolatiliteit (30 dagen)"), f"{volatiliteit_pct}%", label)
    else:
        st.warning(_("Geen historische wisselkoersdata beschikbaar."))

st.markdown("---")

# 📊 DASHBOARD KERNCIJFERS
st.markdown("## " + _("📊 Kerngegevens vastgoedportfolio"))

percelen = st.session_state["percelen"]
aantal_percelen = len(percelen)
totaal_m2 = sum(
    p.get("lengte", 0) * p.get("breedte", 0)
    for p in percelen if isinstance(p, dict)
)

col1, col2 = st.columns(2)
with col1:
    st.metric(_("📍 Aantal percelen"), aantal_percelen)
with col2:
    st.metric(_("📐 Totale oppervlakte"), f"{totaal_m2:,.0f} m²")

# 📅 Komende betalingen
st.subheader(_("📅 Komende betalingen & opgebouwde rente"))

df_betalingen = build_rentebetalingen(st.session_state["percelen"], date.today())

if not df_betalingen.empty:
    for idx, row in df_betalingen.iterrows():   # let op: idx ipv _
        st.markdown(
            f"""
<div style="background-color:#f8f9fa; padding:20px; border-radius:10px; margin-bottom:20px;
            box-shadow:0 2px 6px rgba(0,0,0,0.1); display:grid; grid-template-columns: 2fr 1fr; gap:30px; align-items:start;">
  <div style="display:grid; grid-template-columns: 160px auto; row-gap:6px;">
    <div><b>{_('📍 Perceel')}:</b></div><div>{row['Perceel']}</div>
    <div><b>{_('👤 Investeerder')}:</b></div><div>{row['Investeerder']}</div>
    <div><b>{_('📄 Rentetype')}:</b></div><div>{row['Rentetype']}</div>
    <div><b>{_('📅 Startdatum')}:</b></div><div style="white-space:nowrap;">{row['Startdatum']}</div>
    <div><b>{_('➡️ Volgende betaling')}:</b></div><div style="white-space:nowrap;">{row['Volgende betaling']}</div>
  </div>
  <div style="text-align:right;">
    <div style="margin-bottom:15px;">
      <p style="margin:0; font-size:18px;"><b>{_('💶 Bedrag volgende betaling')}</b></p>
      <p style="margin:0; font-size:26px; font-weight:bold;">€ {row['Bedrag volgende betaling (€)']:.2f}</p>
    </div>
    <div>
      <p style="margin:0; font-size:18px;"><b>{_('🏦 Opgebouwde rente')}</b></p>
      <p style="margin:0; font-size:26px; font-weight:bold;">€ {row['Opgebouwde rente tot nu (€)']:.2f}</p>
    </div>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )

else:
    st.info(_("Geen rentebetalingen gepland."))

# 📈 Analyse verkochte percelen
st.subheader(_("📈 Analyse verkochte percelen"))
exchange_rate = get_exchange_rate_eur_to_gmd()
if not exchange_rate:
    st.warning(_("Wisselkoers niet beschikbaar. Euro-waardes worden niet getoond."))

if "percelen" not in st.session_state or not st.session_state["percelen"]:
    st.warning(_("Er zijn nog geen percelen beschikbaar in het geheugen."))
    st.stop()

percelen = st.session_state["percelen"]
df = pd.DataFrame(percelen)
df.columns = df.columns.str.lower()
df["aankoopdatum"] = pd.to_datetime(df["aankoopdatum"], errors="coerce")
df["start_verkooptraject"] = pd.to_datetime(df.get("start_verkooptraject", pd.NaT), errors="coerce")
df["doorlooptijd"] = pd.to_datetime(df["doorlooptijd"], errors="coerce")

verkochte_df = df[df["dealstage"].str.lower() == "verkocht"]

if "verkoopprijs" not in df.columns:
    df["verkoopprijs"] = 0
if "verkoopprijs_eur" not in df.columns:
    df["verkoopprijs_eur"] = 0.0

# Strategieanalyse
if "doorlooptijd" not in df.columns:
    df["doorlooptijd"] = pd.NaT

df["doorlooptijd"] = pd.to_datetime(df["doorlooptijd"], errors="coerce")

# Kolommen voorbereiden
for col in ["verwachte_opbrengst_eur", "verwachte_kosten_eur", "verwachte_winst_eur", "aankoopprijs_eur"]:
    if col not in df.columns:
        df[col] = [0] * len(df)

df["verwachte_opbrengst_eur"] = pd.to_numeric(df["verwachte_opbrengst_eur"], errors="coerce").fillna(0)
df["verwachte_kosten_eur"]    = pd.to_numeric(df["verwachte_kosten_eur"], errors="coerce").fillna(0)
df["aankoopprijs_eur"]        = pd.to_numeric(df["aankoopprijs_eur"], errors="coerce").fillna(0)
df["verwachte_winst_eur"]     = df["verwachte_opbrengst_eur"] - df["verwachte_kosten_eur"]

if "strategie" not in df.columns:
    df["strategie"] = None

# 1️⃣ Percelen in planning
planning_df = df[pd.to_datetime(df["doorlooptijd"], errors="coerce").notnull()].copy()
planning_df["einddatum_calc"] = pd.to_datetime(planning_df["doorlooptijd"], errors="coerce")

# 2️⃣ Verkochte percelen
verkocht_df = df[df["dealstage"].str.lower() == "verkocht"].copy()
verkocht_df["einddatum_calc"] = pd.to_datetime(verkocht_df["verkoopdatum"], errors="coerce")
verkocht_df["verwachte_opbrengst_eur"] = pd.to_numeric(verkocht_df.get("verkoopprijs_eur", 0), errors="coerce").fillna(0)
verkocht_df["verwachte_kosten_eur"] = pd.to_numeric(verkocht_df.get("verwachte_kosten_eur", 0), errors="coerce").fillna(0)
verkocht_df["aankoopprijs_eur"] = pd.to_numeric(verkocht_df.get("aankoopprijs_eur", 0), errors="coerce").fillna(0)

# 3️⃣ Combineer
df_planning = pd.concat([planning_df, verkocht_df], ignore_index=True)
df_planning["doorlooptijd"] = df_planning["einddatum_calc"]

if not df_planning.empty:
    alle_winst = []
    perceel_debug = []

    for idx, perceel in df_planning.iterrows():   # idx ipv _
        deel_df = verdeel_winst(perceel)
        if not deel_df.empty:
            alle_winst.append(deel_df)

            eerste = deel_df.iloc[0]
            perceel_debug.append({
                _("Perceel"): perceel.get("locatie", _("Onbekend")),
                _("Totale winst (EUR)"): round(deel_df["winst_eur"].sum(), 2),
                _("Looptijd (jaren)"): round(eerste["looptijd_jaren"], 2),
                _("Winst per jaar (EUR)"): round(eerste["winst_per_jaar"], 2),
                _("Rendement per jaar (%)"): round(eerste["rendement_per_jaar_pct"], 2),
                _("Investering (EUR)"): round(eerste["investering"], 2),
                _("Opbrengst (EUR)"): round(eerste["opbrengst"], 2),
            })

    if alle_winst:
        df_winst = pd.concat(alle_winst)

        # Debug-overzicht
        st.subheader(_("📋 Debug-overzicht per perceel (totaal)"))
        st.dataframe(pd.DataFrame(perceel_debug), use_container_width=True)

        # Jaaroverzicht
        jaartotalen = df_winst.groupby("jaar").agg({
            "winst_eur": "sum",
            "winst_per_jaar": "mean",
            "rendement_per_jaar_pct": "mean"
        }).reset_index()

        jaartotalen["winst_per_jaar"] = jaartotalen["winst_per_jaar"].round(2)
        jaartotalen["rendement_per_jaar_pct"] = jaartotalen["rendement_per_jaar_pct"].round(2)
        jaartotalen["winst_eur"] = jaartotalen["winst_eur"].round(2)

        st.subheader(_("📆 Totale verwachte winst per jaar"))
        st.dataframe(
            jaartotalen.rename(columns={
                "jaar": _("Jaar"),
                "winst_eur": _("Totale verwachte winst (EUR)"),
                "winst_per_jaar": _("Gemiddelde winst per jaar (EUR)"),
                "rendement_per_jaar_pct": _("Gemiddeld rendement per jaar (%)")
            }),
            use_container_width=True
        )

        # Maandoverzicht
        maandtotalen = df_winst.groupby(["jaar", "maand"])["winst_eur"].sum().reset_index()
        maandtotalen["datum"] = pd.to_datetime(maandtotalen["jaar"].astype(str) + "-" + maandtotalen["maand"].astype(str) + "-01")

        st.subheader(_("📆 Verwachte winst per maand"))
        st.line_chart(maandtotalen.set_index("datum")["winst_eur"])

        # Controle
        check_maanden = maandtotalen.groupby("jaar")["winst_eur"].sum().reset_index()
        vergelijk = jaartotalen.merge(check_maanden, on="jaar", suffixes=("_jaar", "_maand"))
        vergelijk["verschil"] = (vergelijk["winst_eur_jaar"] - vergelijk["winst_eur_maand"]).round(2)

        st.subheader(_("🔍 Controle jaartotaal vs. som maanden"))
        st.dataframe(
            vergelijk.rename(columns={
                "jaar": _("Jaar"),
                "winst_eur_jaar": _("Winst uit jaartabel (EUR)"),
                "winst_eur_maand": _("Som maandwaarden (EUR)"),
                "verschil": _("Verschil (EUR)")
            }),
            use_container_width=True
        )

    else:
        st.info(_("Geen geldige verdeling mogelijk."))
else:
    st.info(_("Nog geen geldige doorlooptijden of verkoopdata ingevoerd."))

# 📌 Overzicht per strategie
st.subheader(_("🧭 Strategieoverzicht"))

df_strategie = df[df["strategie"].notna()].copy()

if not df_strategie.empty:
    df_strategie["totale_winst_eur"] = df_strategie["verwachte_opbrengst_eur"] - df_strategie["verwachte_kosten_eur"]

    strategie_stats = df_strategie.groupby("strategie").agg({
        "locatie": "count",
        "verwachte_opbrengst_eur": "sum",
        "verwachte_kosten_eur": "sum",
        "totale_winst_eur": "sum"
    }).reset_index()

    strategie_stats = strategie_stats.rename(columns={
        "strategie": _("Strategie"),
        "locatie": _("Aantal percelen"),
        "verwachte_opbrengst_eur": _("Totale opbrengst (EUR)"),
        "verwachte_kosten_eur": _("Totale kosten (EUR)"),
        "totale_winst_eur": _("Totale winst (EUR)")
    })

    st.dataframe(strategie_stats, use_container_width=True)
else:
    st.info(_("Er zijn nog geen strategieën ingevoerd."))

resultaten = []

if verkochte_df.empty:
    st.info(_("Er zijn nog geen verkochte percelen. Hieronder volgt een prognose van actieve percelen."))
    portfolio_df = df[df["dealstage"].str.lower().isin(["in portfolio", "in planning"])]
    if not portfolio_df.empty:
        groei_pct = st.number_input(_("Verwachte jaarlijkse waardestijging (%)"), min_value=0.0, max_value=100.0, value=5.0)
        horizon = st.slider(_("Prognoseperiode (jaren)"), 1, 15, 5)
        for idx, perceel in portfolio_df.iterrows():
            analyse = analyse_portfolio_perceel(perceel, groei_pct, horizon, exchange_rate)
            if analyse:
                resultaten.append(analyse)
else:
    for idx, perceel in verkochte_df.iterrows():
        analyse = analyse_verkocht_perceel(perceel, exchange_rate)
        if analyse:
            resultaten.append(analyse)

if resultaten:
    df_result = pd.DataFrame(resultaten)

    if not verkochte_df.empty:
        st.subheader(_("📊 Resultaten verkochte percelen"))
    else:
        st.subheader(_("📊 Prognose actieve percelen"))

    # ✅ Kolommen zekerstellen
    for col in [
        "verkoopprijs", "verkoopprijs_eur",
        "totaal_opbrengst_gmd", "totaal_opbrengst_eur",
        "opbrengst_per_maand_gmd", "opbrengst_per_maand_eur",
        "verkoopwaarde", "verkoopwaarde_eur",
        "totaal_inleg", "totaal_rente",
        "netto_winst", "netto_winst_eur"
    ]:
        if col not in df_result.columns:
            df_result[col] = 0

    kolommen = [
        "locatie",
        "verkoopprijs", "verkoopprijs_eur",
        "totaal_opbrengst_gmd", "totaal_opbrengst_eur",
        "opbrengst_per_maand_gmd", "opbrengst_per_maand_eur",
        "verkoopwaarde", "verkoopwaarde_eur",
        "totaal_inleg", "totaal_rente",
        "netto_winst", "netto_winst_eur"
    ]

    df_view = df_result.reindex(columns=kolommen, fill_value=0).copy()

    # ✅ Formatteren
    df_view["verkoopprijs"] = df_view["verkoopprijs"].apply(lambda x: format_currency(x, "GMD"))
    df_view["verkoopprijs_eur"] = df_view["verkoopprijs_eur"].apply(lambda x: format_currency(x, "EUR"))
    df_view["totaal_opbrengst_gmd"] = df_view["totaal_opbrengst_gmd"].apply(lambda x: format_currency(x, "GMD"))
    df_view["totaal_opbrengst_eur"] = df_view["totaal_opbrengst_eur"].apply(lambda x: format_currency(x, "EUR"))
    df_view["opbrengst_per_maand_gmd"] = df_view["opbrengst_per_maand_gmd"].apply(lambda x: format_currency(x, "GMD"))
    df_view["opbrengst_per_maand_eur"] = df_view["opbrengst_per_maand_eur"].apply(lambda x: format_currency(x, "EUR"))
    df_view["verkoopwaarde"] = df_view["verkoopwaarde"].apply(lambda x: format_currency(x, "GMD"))
    df_view["verkoopwaarde_eur"] = df_view["verkoopwaarde_eur"].apply(lambda x: format_currency(x, "EUR"))
    df_view["totaal_inleg"] = df_view["totaal_inleg"].apply(lambda x: format_currency(x, "GMD"))
    df_view["totaal_rente"] = df_view["totaal_rente"].apply(lambda x: format_currency(x, "GMD"))
    df_view["netto_winst"] = df_view["netto_winst"].apply(lambda x: format_currency(x, "GMD"))
    df_view["netto_winst_eur"] = df_view["netto_winst_eur"].apply(lambda x: format_currency(x, "EUR"))

    # ✅ Tabs
    st.subheader(_("📊 Overzicht resultaten"))

    tab1, tab2, tab3 = st.tabs([
        _("📊 Financieel overzicht"),
        _("🧭 Strategie & Planning"),
        _("🗺️ Strategieoverzicht")
    ])

    # --- Financieel overzicht ---
    with tab1:
        subtabs_fin = st.tabs([_("💰 Verkoop"), _("📐 Kosten & Inleg"), _("📊 Winst")])

        with subtabs_fin[0]:
            st.dataframe(df_view[["locatie", "verkoopprijs", "verkoopprijs_eur", "verkoopwaarde", "verkoopwaarde_eur"]],
                         use_container_width=True)

        with subtabs_fin[1]:
            st.dataframe(df_view[["locatie", "totaal_inleg", "totaal_rente"]],
                         use_container_width=True)

        with subtabs_fin[2]:
            st.dataframe(df_view[["locatie", "netto_winst", "netto_winst_eur"]],
                         use_container_width=True)

    # --- Strategie & Planning ---
    with tab2:
        subtabs = st.tabs([_("📈 Opbrengsten"), _("📉 Kosten"), _("📜 Overige")])

        with subtabs[0]:
            st.dataframe(df_view[["locatie", "totaal_opbrengst_gmd", "totaal_opbrengst_eur"]],
                         use_container_width=True)

        with subtabs[1]:
            st.dataframe(df_view[["locatie", "opbrengst_per_maand_gmd", "opbrengst_per_maand_eur"]],
                         use_container_width=True)

        with subtabs[2]:
            overige = [c for c in df_view.columns if c not in [
                "locatie", "verkoopprijs", "verkoopprijs_eur", "verkoopwaarde", "verkoopwaarde_eur",
                "totaal_inleg", "totaal_rente", "netto_winst", "netto_winst_eur",
                "totaal_opbrengst_gmd", "totaal_opbrengst_eur",
                "opbrengst_per_maand_gmd", "opbrengst_per_maand_eur"
            ]]
            if overige:
                st.dataframe(df_view[["locatie"] + overige], use_container_width=True)
            else:
                st.info(_("Geen overige kolommen."))

    # --- Strategieoverzicht ---
    with tab3:
        df_strategie = df[df["strategie"].notna()].copy()
        if not df_strategie.empty:
            df_strategie["totale_winst_eur"] = df_strategie["verwachte_opbrengst_eur"] - df_strategie["verwachte_kosten_eur"]

            strategie_stats = df_strategie.groupby("strategie").agg({
                "locatie": "count",
                "verwachte_opbrengst_eur": "sum",
                "verwachte_kosten_eur": "sum",
                "totale_winst_eur": "sum"
            }).reset_index()

            strategie_stats = strategie_stats.rename(columns={
                "strategie": _("Strategie"),
                "locatie": _("Aantal percelen"),
                "verwachte_opbrengst_eur": _("Totale opbrengst (EUR)"),
                "verwachte_kosten_eur": _("Totale kosten (EUR)"),
                "totale_winst_eur": _("Totale winst (EUR)")
            })

            subtabs_strat = st.tabs([_("📈 Opbrengst"), _("📉 Kosten"), _("📊 Winst")])

            with subtabs_strat[0]:
                st.dataframe(strategie_stats[[_("Strategie"), _("Aantal percelen"), _("Totale opbrengst (EUR)")]],
                             use_container_width=True)

            with subtabs_strat[1]:
                st.dataframe(strategie_stats[[_("Strategie"), _("Aantal percelen"), _("Totale kosten (EUR)")]],
                             use_container_width=True)

            with subtabs_strat[2]:
                st.dataframe(strategie_stats[[_("Strategie"), _("Aantal percelen"), _("Totale winst (EUR)")]],
                             use_container_width=True)
        else:
            st.info(_("Nog geen strategieën ingevoerd."))

    # 👥 Per investeerder
    if not verkochte_df.empty:
        st.markdown("### " + _("👥 Investeerders per verkocht perceel"))

        for idx, perceel in verkochte_df.iterrows():
            locatie = perceel.get("locatie", _("Onbekend"))
            with st.expander(f"📍 {locatie} – " + _("details"), expanded=False):
                analyse = analyse_verkocht_perceel(perceel, exchange_rate)

                if analyse:
                    for inv in analyse["investeerders"]:
                        naam = inv.get("naam", _("Investeerder"))
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
                            f"- {naam}: {_('kapitaalkosten')} {format_currency(kapitaalkosten, 'GMD')}, "
                            f"{_('winstdeling')} {format_currency(winstdeling, 'GMD')} "
                            f"({winstdeling_pct:.0f}%), {_('totaal')}: {format_currency(totaal, 'GMD')} "
                            f"({format_currency(totaal_eur, 'EUR')}), {_('netto winst')}: {format_currency(winst_eur, 'EUR')} "
                            f"({rendement_pct:.1f}%)"
                        )

else:
    st.info(_("Geen resultaten beschikbaar."))

# 💬 Chat-tab
tab_chat, = st.tabs([_("💬 Chat (Groq)")])

with tab_chat:
    st.caption(_("Copilot: feiten uit je percelen + acties (lokale tools, NL-intent)."))

    # ---------- Helpers ----------
    import json, re
    from datetime import date, datetime
    from difflib import get_close_matches

    def _percelen_raw():
        return st.session_state.get("percelen", []) or []

    def _normalize_perceel(p: dict) -> dict:
        p = dict(p or {})
        p.setdefault("uploads", {}); p.setdefault("uploads_urls", {})
        p.setdefault("investeerders", [])
        p.setdefault("dealstage", "Aankoop")
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

    # ---------- Lokale tools (single) ----------
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

    # Documentenvereisten (fallback indien globale dict ontbreekt)
    def _get_doc_requirements():
        try:
            return documentvereisten_per_fase
        except NameError:
            return {
                "Aankoop": [
                    _("Sales agreement"), _("Transfer of ownership"), _("Sketch plan"),
                    _("Rates ontvangstbewijs"), _("Land Use Report"), _("Goedkeuring Alkalo"),
                ],
                "Omzetting / bewerking": [_("Resale agreement")],
                "Verkoop": [_("Financieringsoverzicht"), _("ID’s investeerders"), _("Uitbetaling investeerders")],
            }

    def check_missing_docs(only_missing: bool = True):
        req = _get_doc_requirements()
        ps = _percelen_raw()
        items = []
        for p in ps:
            fase = p.get("dealstage", "Aankoop")
            must = list(req.get(fase, []))
            have = p.get("uploads") or {}
            urls = p.get("uploads_urls") or {}
            missing = [d for d in must if not have.get(d)]
            present = [{"doc": d, "url": urls.get(d, "")} for d in must if have.get(d)]
            row = {"locatie": p.get("locatie", _("Onbekend")), "fase": fase, "ontbrekend": missing, "aanwezig": present}
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
            return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}
        opb = float(p.get("verwachte_opbrengst_eur") or 0)
        kos = float(p.get("verwachte_kosten_eur") or 0)
        ank = float(p.get("aankoopprijs_eur") or 0)
        w = opb - kos - ank
        req = _get_doc_requirements()
        must = req.get(p.get("dealstage","Aankoop"), [])
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
            from utils import beoordeel_perceel_modulair, read_marktprijzen, hoofdsteden_df
            markt = read_marktprijzen()
        except Exception:
            beoordeel_perceel_modulair = None
            markt = None
            hoofdsteden_df = None

        target, sug = _resolve_loc(locatie)
        if not target:
            return {"error": _("Perceel '{loc}' niet gevonden").format(loc=locatie),
                    **({"suggestie": _("Bedoelde je '{sug}'?").format(sug=sug)} if sug else {})}

        if callable(beoordeel_perceel_modulair) and markt is not None and hoofdsteden_df is not None:
            try:
                score, toel, adv = beoordeel_perceel_modulair(target, markt, hoofdsteden_df)
                return {"locatie": target.get("locatie"), "score": score, "toelichting": toel, "advies": adv}
            except Exception:
                pass

        ank = float(target.get("aankoopprijs_eur") or 0)
        opb = float(target.get("verwachte_opbrengst_eur") or 0)
        kos = float(target.get("verwachte_kosten_eur") or 0)
        winst = opb - kos - ank
        score = (1 if winst > 0 else -1) + (1 if (opb > 0 and ank > 0 and opb/ank >= 1.2) else 0)
        advies = _("Kopen") if score >= 2 else (_("Twijfel") if score == 1 else _("Mijden"))
        toel = _("Verwachte winst €{w}").format(
            w=f"{winst:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        return {"locatie": target.get("locatie"), "score": score, "toelichting": toel, "advies": advies}

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
            fase = p.get("dealstage","Aankoop")
            must = req.get(fase, [])
            have = p.get("uploads") or {}
            docs_ok = sum(1 for d in must if have.get(d))
            docs_ratio = docs_ok / max(1, len(must))
            winst = float(p.get("verwachte_winst_eur") or 0)
            score = round(60*docs_ratio + 40*(1 if winst > 0 else 0), 1)
            rows.append({
                "locatie": p.get("locatie"), 
                "fase": fase, 
                "docs_ok": f"{docs_ok}/{len(must)}", 
                "winst": winst, 
                "score": score
            })
        return {"scores": sorted(rows, key=lambda x: x["score"], reverse=True)}

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
                "winst_oud": opb-kos-ank, 
                "winst_nieuw": round(opb_new-kos-ank, 2)
            })
        return {"delta_pct": delta_pct, "items": out}

    # ---------- Batch/wijzere tools ----------
    def summary_all():
        return {"items": [summary_perceel(p.get("locatie","")) for p in _percelen_norm()]}

    def docs_all():
        return check_missing_docs(only_missing=False)

    def readiness_top(n: int = 5):
        res = score_readiness().get("scores", [])
        return {"top": res[:max(1,int(n))]}

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
        return {"veld": field, "top": rows[:max(1,int(n))]}

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
        "get_docs_perceel": get_docs_perceel,
        "simulate_fx": simulate_fx,
        # batch/advanced
        "summary_all": summary_all,
        "docs_all": docs_all,
        "readiness_top": readiness_top,
        "advies_all": advies_all,
        "rank_by": rank_by,
    }

    # ---------- Chatgeschiedenis ----------
    if "chat_history_tools_dashboard" not in st.session_state:
        st.session_state.chat_history_tools_dashboard = []
    for m in st.session_state.chat_history_tools_dashboard:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    use_tools = st.toggle(_("🔧 Tools gebruiken (lokaal)"), value=True, key="dashboard_tools_toggle")

    # ---------- Intent-router ----------
    def route_intent(txt: str):
        t = (txt or "").lower().strip()

        def has_any(s, words): return any(w in s for w in words)

        perceel_hit = has_any(t, ["perceel","percelen","grondstuk","kavel","plot","plots"])

        # Aantal / lijst
        if perceel_hit and has_any(t, ["hoeveel","aantal"]): 
            return "get_aantal_percelen", {}
        if (has_any(t, ["lijst","toon","geef","laat zien"]) and "locat" in t) or "locaties" in t:
            m = (re.search(r"limit\s*=\s*(\d+)", t) or re.search(r"\btop\s+(\d+)\b", t))
            limit = int(m.group(1)) if m else 20
            return "list_locaties", {"limit": limit}

        # Laatste
        if has_any(t, ["laatste","recent","recentste","meest recent"]): 
            return "laatste_toegevoegd", {}

        # Ontbrekende documenten
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

        # Readiness
        if any(w in t for w in ["verkoopklaar","verkoopklaarheid","readiness","klaar voor verkoop","risico","score"]):
            return "score_readiness", {}

        # Top N
        m = re.search(r"(?:meest|hoogste)\s+(verkoopklaar(?:heid)?|readiness|winst)[^\d]*(\d+)?", t)
        if m:
            n = int(m.group(2) or 5)
            if "winst" in m.group(1):
                return "rank_by", {"field": "verwachte_winst_eur", "n": n, "desc": True}
            else:
                return "readiness_top", {"n": n}

        # FX simulatie
        m = re.search(r"(fx|wisselkoers|eur).*(\+|-)?\s*(\d+)\s*%", t)
        if m:
            sign = -1 if m.group(2) == "-" else 1
            return "simulate_fx", {"delta_pct": sign*int(m.group(3))}

        return None, None

    # ---------- Chat-afhandeling ----------
    if prompt := st.chat_input(_("Typ je dashboard-vraag…"), key="dashboard_chat_input"):
        st.session_state.chat_history_tools_dashboard.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        base_messages = [
            {"role": "system", "content": _(
                "Je bent een beknopte NL-copilot voor het Vastgoeddashboard. "
                "Regels: (1) Probeer eerst een lokale tool; (2) TOOL_RESULT is waarheid; "
                "Als een intent zowel single- als batch-variant heeft, kies batch bij 'alle' of meerdere percelen; "
                "Gebruik fuzzy-matching voor locatienamen en geef 'suggestie' terug indien van toepassing. "
                "Antwoord in 1–3 zinnen, bedragen in EUR."
            )},
            *st.session_state.chat_history_tools_dashboard,
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
                "Voorbeelden: ‘welke percelen missen documenten?’, "
                "‘samenvatting voor Sanyang 2’, ‘advies voor alle percelen’, "
                "‘meest winst 3’, ‘verkoopklaar top 5’, ‘fx -10%’."
            )

        with st.chat_message("assistant"):
            st.markdown(answer or "_(" + _("Geen antwoord") + ")_")

        st.session_state.chat_history_tools_dashboard.append({"role": "assistant", "content": answer})
# ==== einde Groq-chatblok – Dashboard =========================================







