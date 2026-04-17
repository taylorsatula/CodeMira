import sqlite3
from dataclasses import dataclass

from codemira.store.manager import Store


@dataclass(frozen=True)
class ExtractionContext:
    store: Store
    opencode_conn: sqlite3.Connection
    prompts_dir: str
    api_key: str
