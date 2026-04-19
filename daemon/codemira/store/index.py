import hnswlib
import sqlite3
import numpy as np

from codemira.store.db import parse_embedding_blob


class MemoryIndex:
    def __init__(self, db_path: str, index_path: str, dim: int = 768,
                 ef_construction: int = 200, m: int = 16, ef_search: int = 50):
        self.db_path = db_path
        self.index_path = index_path
        self.dim = dim
        self.ef_construction = ef_construction
        self.m = m
        self.ef_search = ef_search
        self.index = None
        self.id_map: dict[int, str] = {}
        self.label_map: dict[str, int] = {}
        self._next_label = 0

    def build_from_db(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT id, embedding FROM memories WHERE is_archived = 0 AND embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            self.index = None
            self.id_map = {}
            self.label_map = {}
            self._next_label = 0
            return
        self.index = hnswlib.Index(space='cosine', dim=self.dim)
        max_elements = max(len(rows) * 2, 100)
        self.index.init_index(max_elements=max_elements,
                              ef_construction=self.ef_construction, M=self.m)
        self.id_map = {}
        self.label_map = {}
        for label, row in enumerate(rows):
            memory_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            blob = row["embedding"] if isinstance(row, sqlite3.Row) else row[1]
            vec = np.frombuffer(blob, dtype=np.float32)
            self.index.add_items(vec, label)
            self.id_map[label] = memory_id
            self.label_map[memory_id] = label
        self._next_label = len(rows)
        self.index.set_ef(self.ef_search)
        self.index.save_index(self.index_path)

    def search(self, query_embedding: list[float], k: int) -> list[tuple[str, float]]:
        if self.index is None or len(self.id_map) == 0:
            return []
        actual_k = min(k, len(self.id_map))
        vec = np.array(query_embedding, dtype=np.float32)
        labels, distances = self.index.knn_query(vec, k=actual_k)
        results = []
        for label, dist in zip(labels[0], distances[0]):
            label = int(label)
            if label in self.id_map:
                results.append((self.id_map[label], max(0.0, 1.0 - dist)))
        return results

    def add_vector(self, memory_id: str, embedding: list[float]):
        if self.index is None:
            return
        current_max = self.index.get_max_elements()
        if self._next_label >= current_max:
            self.index.resize_index(max(current_max * 2, self._next_label + 1))
        vec = np.array(embedding, dtype=np.float32)
        label = self._next_label
        self.index.add_items(vec, label)
        self.id_map[label] = memory_id
        self.label_map[memory_id] = label
        self._next_label += 1

    def rebuild_after_write(self, conn: sqlite3.Connection):
        self.build_from_db(conn)
