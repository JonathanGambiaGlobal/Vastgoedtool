from supabase import create_client
import streamlit as st
import traceback


class DataStore:
    def __init__(self):
        self.client = create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_ANON_KEY"]
        )

    def load_percelen(self):
        try:
            response = (
                self.client
                .table("percelen")
                .select("perceel")
                .execute()
            )

            if not response.data:
                return []

            return [row["perceel"] for row in response.data]

        except Exception:
            print("=== FOUT BIJ LOAD_PERCELEN ===")
            traceback.print_exc()
            raise

    def save_percelen(self, percelen):
        try:
            print("=== START SAVE ===")

            print("Verwijderen oude records...")
            delete_result = (
                self.client
                .table("percelen")
                .delete()
                .neq("id", "")
                .execute()
            )

            print("DELETE GELUKT")
            print(delete_result)

            rows = [{"perceel": p} for p in percelen]

            print(f"Aantal nieuwe records: {len(rows)}")

            if rows:
                insert_result = (
                    self.client
                    .table("percelen")
                    .insert(rows)
                    .execute()
                )

                print("INSERT GELUKT")
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


store = DataStore()
