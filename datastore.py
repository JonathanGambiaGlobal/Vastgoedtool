from supabase import create_client
import streamlit as st


class DataStore:
    def __init__(self):
        self.client = create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_KEY"]
        )

    def load_percelen(self):
        response = (
            self.client
            .table("percelen")
            .select("perceel")
            .execute()
        )

        return [row["perceel"] for row in response.data]

    def save_percelen(self, percelen):
        self.client.table("percelen").delete().neq("id", "").execute()

        rows = [{"perceel": p} for p in percelen]

        if rows:
            self.client.table("percelen").insert(rows).execute()


store = DataStore()
