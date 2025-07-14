# 🔐 AUTHENTICATIE – login_check functie
import streamlit as st
import json
import os

USERS_FILE = "users.json"

def login_check():
    # 📁 Als het bestand nog niet bestaat, maak standaardgebruikers aan
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump({
                "admin": {"wachtwoord": "admin", "rol": "admin"},
                "gast": {"wachtwoord": "gast", "rol": "viewer"}
            }, f)

    # 📥 Gebruikers inladen
    with open(USERS_FILE, "r") as f:
        gebruikers = json.load(f)

    # 🔑 Sessie initialiseren
    if "ingelogd" not in st.session_state:
        st.session_state.ingelogd = False
        st.session_state.gebruiker = None
        st.session_state.rol = None

    # ⛔ Toon loginpagina als niet ingelogd
    if not st.session_state.ingelogd:
        st.title("🔐 Login vereist")
        gebruikersnaam = st.text_input("Gebruikersnaam")
        wachtwoord = st.text_input("Wachtwoord", type="password")

        if st.button("Inloggen"):
            gebruiker = gebruikers.get(gebruikersnaam)
            if gebruiker and gebruiker["wachtwoord"] == wachtwoord:
                st.session_state.ingelogd = True
                st.session_state.gebruiker = gebruikersnaam
                st.session_state.rol = gebruiker["rol"]
                st.success(f"Ingelogd als {gebruikersnaam} ({gebruiker['rol']})")
                st.rerun()
            else:
                st.error("Ongeldige inloggegevens")
        st.stop()

    # ✅ Toon loginstatus in sidebar
    st.sidebar.success(f"✅ Ingelogd als: {st.session_state.gebruiker} ({st.session_state.rol})")


