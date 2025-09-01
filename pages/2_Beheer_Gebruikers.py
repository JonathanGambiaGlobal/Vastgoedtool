import streamlit as st
import json
import os
from auth import login_check

# 🌐 vertalingen ophalen uit session_state
_ = st.session_state.get("_", lambda x: x)

# 🔐 Login check & alleen admins toelaten
login_check()
if st.session_state.rol != "admin":
    st.warning(_("⛔ Alleen admins hebben toegang tot dit scherm."))
    st.stop()

# 📁 Pad naar gebruikersbestand
USERS_FILE = "users.json"

# 📥 Inladen gebruikers
with open(USERS_FILE, "r") as f:
    gebruikers = json.load(f)

# 👥 GEBRUIKERSBEHEER
st.image("QG.png", width=150)
st.subheader(_("👥 Gebruikersbeheer"))

for naam, info in gebruikers.items():
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        st.text_input(_("Gebruiker"), value=naam, key=f"u_{naam}", disabled=True)
    with col2:
        nieuwe_rol = st.selectbox(
            _("Rol"),
            ["admin", "viewer"],
            index=["admin", "viewer"].index(info["rol"]),
            key=f"r_{naam}"
        )
        gebruikers[naam]["rol"] = nieuwe_rol
    with col3:
        if naam != "admin":
            if st.button(_("🗑 Verwijder"), key=f"del_{naam}"):
                gebruikers.pop(naam)
                with open(USERS_FILE, "w") as f:
                    json.dump(gebruikers, f)
                st.success(_("Gebruiker '{naam}' verwijderd.").format(naam=naam))
                st.rerun()

st.divider()
st.subheader(_("➕ Gebruiker toevoegen"))

nieuwe_naam = st.text_input(_("Nieuwe gebruikersnaam"))
nieuw_wachtwoord = st.text_input(_("Wachtwoord"), type="password")
nieuwe_rol = st.selectbox(_("Rol van nieuwe gebruiker"), ["admin", "viewer"])

if st.button(_("Gebruiker toevoegen")):
    if nieuwe_naam in gebruikers:
        st.warning(_("Gebruiker bestaat al."))
    elif not nieuwe_naam or not nieuw_wachtwoord:
        st.warning(_("Gebruikersnaam en wachtwoord zijn verplicht."))
    else:
        gebruikers[nieuwe_naam] = {"wachtwoord": nieuw_wachtwoord, "rol": nieuwe_rol}
        with open(USERS_FILE, "w") as f:
            json.dump(gebruikers, f)
        st.success(_("Gebruiker '{naam}' toegevoegd.").format(naam=nieuwe_naam))
        st.rerun()

