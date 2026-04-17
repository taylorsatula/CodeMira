import os
import threading

import pytest

from tests.conftest import _make_embeddings


@pytest.fixture
def store(tmpdir_path):
    from codemira.config import DaemonConfig
    from codemira.store.manager import StoreManager
    project_dir = os.path.join(tmpdir_path, "proj")
    os.makedirs(project_dir, exist_ok=True)
    manager = StoreManager(DaemonConfig())
    yield manager.get(project_dir)
    manager.close_all()


def _insert_with_lock(store, text, category, emb, session_id):
    from codemira.store.db import insert_memory
    with store.lock:
        mid = insert_memory(store.conn, text, category, emb, session_id)
        store.index.add_vector(mid, emb)
        return mid


class TestConcurrentInserts:
    def test_two_threads_insert_simultaneously(self, store):
        from codemira.store.db import get_all_memories
        embs = _make_embeddings(20)
        results: list[str] = []
        results_lock = threading.Lock()
        errors: list[Exception] = []

        def worker(start, end):
            try:
                for i in range(start, end):
                    mid = _insert_with_lock(store, f"memory {i}", "priority", embs[i], f"ses_{i}")
                    with results_lock:
                        results.append(mid)
            except Exception as e:
                with results_lock:
                    errors.append(e)

        t1 = threading.Thread(target=worker, args=(0, 10))
        t2 = threading.Thread(target=worker, args=(10, 20))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent inserts raised: {errors}"
        assert len(results) == 20
        with store.lock:
            mems = get_all_memories(store.conn)
        assert len(mems) == 20

    def test_concurrent_read_while_writing(self, store):
        from codemira.store.db import insert_memory, get_all_memories
        emb = _make_embeddings(1)[0]
        with store.lock:
            insert_memory(store.conn, "seed memory", "priority", emb, "ses_seed")
            store.index.rebuild_after_write(store.conn)

        embs = _make_embeddings(10, seed=99)
        write_done = threading.Event()
        read_results: list[int] = []
        errors: list[Exception] = []

        def writer():
            try:
                for i, e in enumerate(embs):
                    _insert_with_lock(store, f"writer-{i}", "priority", e, f"ses_w{i}")
            except Exception as e:
                errors.append(e)
            finally:
                write_done.set()

        def reader():
            try:
                while not write_done.is_set():
                    with store.lock:
                        mems = get_all_memories(store.conn)
                    read_results.append(len(mems))
            except Exception as e:
                errors.append(e)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert errors == [], f"Concurrent ops raised: {errors}"
        with store.lock:
            mems = get_all_memories(store.conn)
        assert len(mems) == 11

    def test_concurrent_index_search_and_rebuild(self, store):
        from codemira.store.db import insert_memory
        embs = _make_embeddings(5)
        with store.lock:
            for i, e in enumerate(embs):
                mid = insert_memory(store.conn, f"seed {i}", "priority", e, f"ses_seed{i}")
                store.index.add_vector(mid, e)
            store.index.rebuild_after_write(store.conn)

        more_embs = _make_embeddings(10, seed=7)
        done = threading.Event()
        errors: list[Exception] = []

        def writer():
            try:
                for i, e in enumerate(more_embs):
                    _insert_with_lock(store, f"writer-{i}", "priority", e, f"ses_w{i}")
                with store.lock:
                    store.index.rebuild_after_write(store.conn)
            except Exception as e:
                errors.append(e)
            finally:
                done.set()

        def searcher():
            try:
                query = embs[0]
                while not done.is_set():
                    with store.lock:
                        store.index.search(query, k=3)
            except Exception as e:
                errors.append(e)

        t_w = threading.Thread(target=writer)
        t_s = threading.Thread(target=searcher)
        t_w.start()
        t_s.start()
        t_w.join()
        t_s.join()

        assert errors == [], f"Concurrent search/rebuild raised: {errors}"


class TestStoreLockSeparateProjects:
    def test_locks_are_independent_per_project(self, tmpdir_path):
        from codemira.config import DaemonConfig
        from codemira.store.manager import StoreManager
        proj_a = os.path.join(tmpdir_path, "a")
        proj_b = os.path.join(tmpdir_path, "b")
        os.makedirs(proj_a)
        os.makedirs(proj_b)
        manager = StoreManager(DaemonConfig())
        store_a = manager.get(proj_a)
        store_b = manager.get(proj_b)
        assert store_a.lock is not store_b.lock
        with store_a.lock:
            acquired_b = store_b.lock.acquire(blocking=False)
            assert acquired_b, "store_b.lock should be acquirable while store_a.lock is held"
            store_b.lock.release()
        manager.close_all()
