from supabase import create_client
import streamlit as st
import traceback

from settings import require_secret


class DataStore:
    def __init__(self):
        print("=== Initialiseren Supabase ===")

        url = require_secret("SUPABASE_URL")
        key = require_secret("SUPABASE_ANON_KEY", "SUPABASE_KEY")

        self.client = create_client(
            url,
            key,
        )

        print("Supabase client aangemaakt.\n")

    def load_percelen(self):
        try:
            print("=== Laden percelen ===")

            response = (
                self.client
                .table("percelen")
                .select("perceel")
                .execute()
            )

            print("Load succesvol.")
            print(response.data)

            if not response.data:
                return []

            return [row["perceel"] for row in response.data]

        except Exception as e:
            print("\n=== FOUT BIJ LOAD ===")
            traceback.print_exc()
            print(repr(e))
            raise

    def save_percelen(self, percelen):
        try:
            print("\n=== SAVE START ===")

            print("Aantal percelen:")
            print(len(percelen))

            print("DELETE uitvoeren...")

            delete_result = (
                self.client
                .table("percelen")
                .delete()
                .not_.is_("id", None)
                .execute()
            )

            print("DELETE gelukt.")
            print(delete_result)

            rows = [{"perceel": p} for p in percelen]

            print(f"{len(rows)} records worden ingevoegd.")

            if rows:
                insert_result = (
                    self.client
                    .table("percelen")
                    .insert(rows)
                    .execute()
                )

                print("INSERT gelukt.")
                print(insert_result)

            print("=== SAVE VOLTOOID ===")

        except Exception as e:
            print("\n==============================")
            print("SUPABASE FOUT")
            print("==============================")
            traceback.print_exc()
            print("\nException:")
            print(repr(e))
            print("==============================\n")
            raise


@st.cache_resource
def get_store() -> DataStore:
    """Maak de Supabase-client pas aan wanneer de app hem echt nodig heeft."""
    return DataStore()
