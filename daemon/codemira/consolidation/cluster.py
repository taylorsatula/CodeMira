from collections import deque
import sqlite3

from codemira.store.index import MemoryIndex
from codemira.store.db import get_memory


def find_clusters(conn: sqlite3.Connection, index: MemoryIndex,
                  threshold: float = 0.85) -> list[list[str]]:
    rows = conn.execute(
        "SELECT id FROM memories WHERE is_archived = 0 AND embedding IS NOT NULL"
    ).fetchall()
    all_ids = [r["id"] for r in rows]
    graph = {mid: set() for mid in all_ids}
    for memory_id in all_ids:
        mem = get_memory(conn, memory_id)
        if mem is None or mem["embedding"] is None:
            continue
        neighbors = index.search(mem["embedding"], k=21)
        for neighbor_id, similarity in neighbors:
            if neighbor_id != memory_id and similarity >= threshold:
                graph[memory_id].add(neighbor_id)
                graph[neighbor_id].add(memory_id)
    visited = set()
    clusters = []
    for start in all_ids:
        if start in visited:
            continue
        if len(graph[start]) == 0:
            continue
        cluster = []
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            for neighbor in graph[node]:
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(cluster) >= 2:
            clusters.append(cluster)
    return clusters
