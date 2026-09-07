"""Configuratie uit Render environment variables of lokale Streamlit secrets."""

import os

import streamlit as st


def get_secret(name: str, *aliases: str, default=None):
    """Lees eerst de omgeving (Render), daarna lokale Streamlit secrets."""
    names = (name, *aliases)

    for key in names:
        value = os.getenv(key)
        if value:
            return value

    try:
        for key in names:
            value = st.secrets.get(key)
            if value:
                return value
    except Exception:
        # Op Render is een secrets.toml niet nodig; environment variables volstaan.
        pass

    return default


def require_secret(name: str, *aliases: str) -> str:
    value = get_secret(name, *aliases)
    if not value:
        accepted = ", ".join((name, *aliases))
        raise RuntimeError(
            f"Ontbrekende configuratie: {accepted}. "
            "Stel deze in als Environment Variable op Render."
        )
    return value
