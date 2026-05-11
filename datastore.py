class DataStore:
    def load_percelen(self):
        raise NotImplementedError

    def save_percelen(self, percelen):
        raise NotImplementedError


class MemoryStore(DataStore):
    def __init__(self):
        self._percelen = []

    def load_percelen(self):
        return self._percelen

    def save_percelen(self, percelen):
        self._percelen = percelen


store = MemoryStore()
