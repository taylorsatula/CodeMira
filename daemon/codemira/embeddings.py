import threading
from sentence_transformers import SentenceTransformer


class EmbeddingsProvider:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.model = SentenceTransformer("MongoDB/mdbr-leaf-ir-asym")

    @classmethod
    def get(cls) -> "EmbeddingsProvider":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def encode_realtime(self, text: str) -> list[float]:
        return self.model.encode_query(text).tolist()

    def encode_deep(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode_document(texts).tolist()
