import os
import sqlite3
import threading

from codemira.config import DaemonConfig
from codemira.store.db import open_db
from codemira.store.index import MemoryIndex


CODEMEMORY_DIR = ".codememory"
MEMORIES_DB = "memories.db"
MEMORIES_INDEX = "memories.index"


def project_store_paths(project_dir: str) -> tuple[str, str, str]:
    project_dir = os.path.abspath(project_dir)
    codememory = os.path.join(project_dir, CODEMEMORY_DIR)
    return codememory, os.path.join(codememory, MEMORIES_DB), os.path.join(codememory, MEMORIES_INDEX)


class StoreManager:
    def __init__(self, config: DaemonConfig):
        self.config = config
        self._stores: dict[str, tuple[sqlite3.Connection, MemoryIndex]] = {}
        self._lock = threading.Lock()

    def get(self, project_dir: str) -> tuple[sqlite3.Connection, MemoryIndex]:
        key = os.path.abspath(project_dir)
        with self._lock:
            if key in self._stores:
                return self._stores[key]
            codememory_dir, db_path, index_path = project_store_paths(key)
            os.makedirs(codememory_dir, exist_ok=True)
            conn = open_db(db_path)
            index = MemoryIndex(
                db_path, index_path, self.config.embedding_dimension,
                self.config.hnsw_ef_construction, self.config.hnsw_m, self.config.hnsw_ef_search,
            )
            index.build_from_db(conn)
            self._stores[key] = (conn, index)
            return conn, index

    def register(self, project_dir: str, conn: sqlite3.Connection, index: MemoryIndex):
        key = os.path.abspath(project_dir)
        with self._lock:
            self._stores[key] = (conn, index)

    def items(self) -> list[tuple[str, sqlite3.Connection, MemoryIndex]]:
        with self._lock:
            return [(k, conn, idx) for k, (conn, idx) in self._stores.items()]

    def close_all(self):
        with self._lock:
            for conn, _ in self._stores.values():
                conn.close()
            self._stores.clear()
