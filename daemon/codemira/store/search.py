import re
import sqlite3
from dataclasses import dataclass

from codemira.store.index import MemoryIndex


@dataclass
class SearchResult:
    memory_id: str
    score: float
    text: str
    category: str


def sanitize_fts_query(query: str) -> str:
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


class HybridSearcher:
    RRF_K = 60

    def bm25_search(self, query: str, limit: int, conn: sqlite3.Connection) -> list[SearchResult]:
        fts_query = sanitize_fts_query(query)
        if not fts_query:
            return []
        cursor = conn.execute(
            "SELECT m.id, bm25(memories_fts) as score, m.text, m.category "
            "FROM memories_fts fts JOIN memories m ON m.rowid = fts.rowid "
            "WHERE memories_fts MATCH ? AND m.is_archived = 0 "
            "ORDER BY score LIMIT ?",
            (fts_query, limit),
        )
        results = []
        for row in cursor.fetchall():
            results.append(SearchResult(
                memory_id=row["id"],
                score=row["score"],
                text=row["text"],
                category=row["category"],
            ))
        return results

    def ann_search(self, query_embedding: list[float], limit: int,
                   index: MemoryIndex, conn: sqlite3.Connection) -> list[SearchResult]:
        raw = index.search(query_embedding, k=limit)
        results = []
        for memory_id, similarity in raw:
            row = conn.execute(
                "SELECT text, category FROM memories WHERE id = ? AND is_archived = 0",
                (memory_id,),
            ).fetchone()
            if row is None:
                continue
            results.append(SearchResult(
                memory_id=memory_id,
                score=similarity,
                text=row["text"],
                category=row["category"],
            ))
        return results

    def hybrid_search(self, query: str, query_embedding: list[float], limit: int,
                       conn: sqlite3.Connection, index: MemoryIndex,
                       bm25_weight: float = 0.4, ann_weight: float = 0.6) -> list[SearchResult]:
        bm25_results = self.bm25_search(query, limit * 2, conn)
        ann_results = self.ann_search(query_embedding, limit * 2, index, conn)
        rrf_scores: dict[str, float] = {}
        memory_data: dict[str, SearchResult] = {}
        for rank, result in enumerate(bm25_results):
            rrf_scores[result.memory_id] = rrf_scores.get(result.memory_id, 0) + bm25_weight / (self.RRF_K + rank + 1)
            memory_data[result.memory_id] = result
        for rank, result in enumerate(ann_results):
            rrf_scores[result.memory_id] = rrf_scores.get(result.memory_id, 0) + ann_weight / (self.RRF_K + rank + 1)
            if result.memory_id not in memory_data:
                memory_data[result.memory_id] = result
        sorted_ids = sorted(rrf_scores.keys(), key=lambda mid: rrf_scores[mid], reverse=True)
        results = []
        for mid in sorted_ids[:limit]:
            r = memory_data[mid]
            results.append(SearchResult(
                memory_id=mid,
                score=rrf_scores[mid],
                text=r.text,
                category=r.category,
            ))
        return results
