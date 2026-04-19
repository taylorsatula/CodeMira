import pytest
import os
import numpy as np
from tests.conftest import _make_embedding, _make_embeddings


@pytest.fixture
def cluster_setup(tmpdir_path, memory_db):
    from codemira.store.db import insert_memory
    from codemira.store.index import MemoryIndex
    rng = np.random.default_rng(42)
    base_vec = rng.standard_normal(768).astype(np.float32)
    similar_vecs = [base_vec + rng.standard_normal(768).astype(np.float32) * 0.3 for _ in range(3)]
    different_vec = rng.standard_normal(768).astype(np.float32)
    ids = []
    texts = [
        "Prefers threading over asyncio for concurrent I/O",
        "Rejected asyncio for the task runner called it a mess",
        "Prefers threading Event and Timer for concurrency",
        "Uses Docker for deployment and containerization",
    ]
    vecs = similar_vecs + [different_vec]
    for i, (text, vec) in enumerate(zip(texts, vecs)):
        mid = insert_memory(memory_db, text, "priority", vec.tolist())
        ids.append(mid)
    index_path = os.path.join(tmpdir_path, "memories.index")
    mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
    mi.build_from_db(memory_db)
    return memory_db, mi, ids


class TestFindClusters:
    def test_finds_cluster_of_similar(self, cluster_setup):
        from codemira.consolidation.cluster import build_clusters
        conn, mi, ids = cluster_setup
        clusters = build_clusters(conn, mi, threshold=0.5)
        assert len(clusters) >= 1
        all_clustered = set()
        for c in clusters:
            all_clustered.update(c)
        similar_ids = {ids[0], ids[1], ids[2]}
        assert similar_ids & all_clustered, f"Expected at least one similar memory in clusters, got {all_clustered}"
        different_id = ids[3]
        for c in clusters:
            assert different_id not in c or len(c) > 1, "Different memory shouldn't form a singleton cluster"

    def test_no_clusters_below_threshold(self, cluster_setup):
        from codemira.consolidation.cluster import build_clusters
        conn, mi, ids = cluster_setup
        clusters = build_clusters(conn, mi, threshold=0.99)
        assert len(clusters) == 0

    def test_empty_db(self, memory_db, tmpdir_path):
        from codemira.consolidation.cluster import build_clusters
        from codemira.store.index import MemoryIndex
        index_path = os.path.join(tmpdir_path, "memories.index")
        mi = MemoryIndex(os.path.join(tmpdir_path, "memories.db"), index_path)
        mi.build_from_db(memory_db)
        clusters = build_clusters(memory_db, mi)
        assert len(clusters) == 0


class TestClusterSizes:
    def test_cluster_has_at_least_two(self, cluster_setup):
        from codemira.consolidation.cluster import build_clusters
        conn, mi, ids = cluster_setup
        clusters = build_clusters(conn, mi, threshold=0.5)
        for c in clusters:
            assert len(c) >= 2
