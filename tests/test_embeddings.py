import pytest
from tests.conftest import skip_no_embeddings


@skip_no_embeddings
class TestEmbeddingsProvider:
    def test_encode_realtime_dimension(self):
        from codemira.embeddings import EmbeddingsProvider
        provider = EmbeddingsProvider()
        result = provider.encode_realtime("test query")
        assert len(result) == 768

    def test_encode_deep_dimension(self):
        from codemira.embeddings import EmbeddingsProvider
        provider = EmbeddingsProvider()
        result = provider.encode_deep(["test document 1", "test document 2"])
        assert len(result) == 2
        assert len(result[0]) == 768

    def test_encode_realtime_floats(self):
        from codemira.embeddings import EmbeddingsProvider
        provider = EmbeddingsProvider()
        result = provider.encode_realtime("test query")
        assert all(isinstance(x, float) for x in result)

    def test_singleton(self):
        from codemira.embeddings import EmbeddingsProvider
        p1 = EmbeddingsProvider.get()
        p2 = EmbeddingsProvider.get()
        assert p1 is p2

    def test_asymmetric_different_outputs(self):
        from codemira.embeddings import EmbeddingsProvider
        provider = EmbeddingsProvider()
        query_emb = provider.encode_realtime("asyncio threading")
        doc_emb = provider.encode_deep(["asyncio threading"])
        assert query_emb != doc_emb[0]
