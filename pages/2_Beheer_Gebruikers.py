import streamlit as st
import json
import os
from auth import login_check

# 🔐 Login check & alleen admins toelaten
login_check()
if st.session_state.rol != "admin":
    st.warning("⛔ Alleen admins hebben toegang tot dit scherm.")
    st.stop()

# 📁 Pad naar gebruikersbestand
USERS_FILE = "users.json"

# 📥 Inladen gebruikers
with open(USERS_FILE, "r") as f:
    gebruikers = json.load(f)

# 👥 GEBRUIKERSBEHEER
st.image("QG.png", width=150)
st.subheader("👥 Gebruikersbeheer")

for naam, info in gebruikers.items():
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        st.text_input("Gebruiker", value=naam, key=f"u_{naam}", disabled=True)
    with col2:
        nieuwe_rol = st.selectbox("Rol", ["admin", "viewer"], index=["admin", "viewer"].index(info["rol"]), key=f"r_{naam}")
        gebruikers[naam]["rol"] = nieuwe_rol
    with col3:
        if naam != "admin":
            if st.button("🗑 Verwijder", key=f"del_{naam}"):
                gebruikers.pop(naam)
                with open(USERS_FILE, "w") as f:
                    json.dump(gebruikers, f)
                st.success(f"Gebruiker '{naam}' verwijderd.")
                st.rerun()

st.divider()
st.subheader("➕ Gebruiker toevoegen")
nieuwe_naam = st.text_input("Nieuwe gebruikersnaam")
nieuw_wachtwoord = st.text_input("Wachtwoord", type="password")
nieuwe_rol = st.selectbox("Rol van nieuwe gebruiker", ["admin", "viewer"])

if st.button("Gebruiker toevoegen"):
    if nieuwe_naam in gebruikers:
        st.warning("Gebruiker bestaat al.")
    elif not nieuwe_naam or not nieuw_wachtwoord:
        st.warning("Gebruikersnaam en wachtwoord zijn verplicht.")
    else:
        gebruikers[nieuwe_naam] = {"wachtwoord": nieuw_wachtwoord, "rol": nieuwe_rol}
        with open(USERS_FILE, "w") as f:
            json.dump(gebruikers, f)
        st.success(f"Gebruiker '{nieuwe_naam}' toegevoegd.")
        st.rerun()
