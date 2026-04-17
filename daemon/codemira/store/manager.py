import os
import sqlite3
import threading
from dataclasses import dataclass

from codemira.config import DaemonConfig
from codemira.store.db import open_db
from codemira.store.index import MemoryIndex


CODEMEMORY_DIR = ".codememory"
MEMORIES_DB = "memories.db"
MEMORIES_INDEX = "memories.index"


@dataclass
class Store:
    conn: sqlite3.Connection
    index: MemoryIndex
    lock: threading.Lock


def project_store_paths(project_root: str) -> tuple[str, str, str]:
    project_root = os.path.abspath(project_root)
    codememory = os.path.join(project_root, CODEMEMORY_DIR)
    return codememory, os.path.join(codememory, MEMORIES_DB), os.path.join(codememory, MEMORIES_INDEX)


class StoreManager:
    def __init__(self, config: DaemonConfig):
        self.config = config
        self._stores: dict[str, Store] = {}
        self._lock = threading.Lock()

    def get(self, project_root: str) -> Store:
        key = os.path.abspath(project_root)
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
            store = Store(conn=conn, index=index, lock=threading.Lock())
            self._stores[key] = store
            return store

    def register(self, project_root: str, conn: sqlite3.Connection, index: MemoryIndex):
        key = os.path.abspath(project_root)
        with self._lock:
            self._stores[key] = Store(conn=conn, index=index, lock=threading.Lock())

    def items(self) -> list[tuple[str, Store]]:
        with self._lock:
            return list(self._stores.items())

    def close_all(self):
        with self._lock:
            for store in self._stores.values():
                store.conn.close()
            self._stores.clear()
