from utils import (
    load_percelen_from_json,
    save_percelen_as_json,
)

class DataStore:
    def load_percelen(self):
        raise NotImplementedError

    def save_percelen(self, percelen):
        raise NotImplementedError


class GoogleSheetsStore(DataStore):
    def load_percelen(self):
        return load_percelen_from_json()

    def save_percelen(self, percelen):
        return save_percelen_as_json(percelen)


store = GoogleSheetsStore()
