import pytest
import sqlite3
import tempfile
import os
import numpy as np


def llm_available() -> bool:
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/v1/models", timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def embedding_model_available() -> bool:
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer("MongoDB/mdbr-leaf-ir-asym")
        return True
    except Exception:
        return False


skip_no_local_llm = pytest.mark.skipif(not llm_available(), reason="Local LLM endpoint not available")
skip_no_embeddings = pytest.mark.skipif(not embedding_model_available(), reason="Embedding model not available")


@pytest.fixture
def memory_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "memories.db")
        from codemira.store.db import open_db
        conn = open_db(db_path)
        yield conn
        conn.close()


@pytest.fixture
def tmpdir_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _make_embedding(dim=768, seed=42):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dim).astype(np.float32).tolist()


def _make_embeddings(n, dim=768, seed=42):
    rng = np.random.default_rng(seed)
    return [rng.standard_normal(dim).astype(np.float32).tolist() for _ in range(n)]
