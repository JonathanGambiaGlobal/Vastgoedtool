from supabase import create_client
import streamlit as st


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
        try:
            print("DELETE START")
    
            response = (
                self.client
                .table("percelen")
                .delete()
                .neq("id", "")
                .execute()
            )
    
            print(response)
    
            rows = [{"perceel": p} for p in percelen]
    
            if rows:
                response = (
                    self.client
                    .table("percelen")
                    .insert(rows)
                    .execute()
                )
    
                print(response)
    
        except Exception as e:
            st.exception(e)
            raise


store = DataStore()
