from supabase import create_client
import streamlit as st

st.write(st.secrets)
st.stop()


class DataStore:
    def __init__(self):
        self.client = create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_ANON_KEY"]
        )

    def load_percelen(self):
        response = (
            self.client
            .table("percelen")
            .select("perceel")
            .execute()
        )

        if not response.data:
            return []

        return [row["perceel"] for row in response.data]

    def save_percelen(self, percelen):
        # verwijder oude records
        self.client.table("percelen").delete().neq("id", "").execute()

        # voeg nieuwe records toe
        rows = [{"perceel": p} for p in percelen]

        if rows:
            self.client.table("percelen").insert(rows).execute()


store = DataStore()
