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

# utils
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

# --- Power BI Embed ---
st.markdown(
    """
    <style>
        .pbi-wrapper {
            width: 100vw;  
            /* Centreer en schuif 40px naar rechts om de sidebar te compenseren */
            margin-left: calc(-50vw + 50% + 40px);  
        }
        .pbi-wrapper iframe {
            width: 100%;
            height: 90vh;
            border: none;
            display: block;
        }
    </style>

    <div class="pbi-wrapper">
        <iframe title="PBI extensie Gambia, dashboard"
                src="https://app.powerbi.com/view?r=eyJrIjoiYTZjNGYzYWQtZTUwOS00ZjRmLWEzNDUtMDc5Njc3YjQ5ODE4IiwidCI6IjE5ZjY4NTk4LWZiMzUtNDVhMS1hNzEwLTA1NmI1NTFlODkyZCIsImMiOjl9&pageName=657908c8083714fca2c4"
                allowFullScreen="true"></iframe>
    </div>
    """,
    unsafe_allow_html=True
)


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
