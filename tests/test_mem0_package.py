"""Actual Mem0/Qdrant package contract; mock embedding prevents network access.

This validates SDK compatibility and isolation, not embedding semantic quality.
"""
import pytest


def test_actual_mem0_oss_roundtrip_without_external_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    mem0 = pytest.importorskip("mem0")
    from companion.memory_adapter import MemoryAdapter
    from mem0.embeddings.mock import MockEmbeddings
    from mem0.utils.factory import EmbedderFactory
    # Mock the external embedding boundary. The actual Memory, Qdrant, metadata,
    # filtering, updates and deletion implementations all run unchanged.
    monkeypatch.setattr(EmbedderFactory, "create", staticmethod(lambda *a, **kw: MockEmbeddings()))
    memory = mem0.Memory.from_config({
        "embedder": {"provider": "openai", "config": {"api_key": "test-only-unused", "embedding_dims": 10}},
        "llm": {"provider": "openai", "config": {"api_key": "test-only-unused-with-infer-false"}},
        "vector_store": {"provider": "qdrant", "config": {
            "path": str(tmp_path / "vectors"), "collection_name": "companion_test", "embedding_model_dims": 10,
        }},
        "history_db_path": str(tmp_path / "history.sqlite"),
    })
    subject = MemoryAdapter(client=memory)
    subject.sync("alice", [{"id": "m1", "text": "我不吃海鲜"}])
    subject.sync("bob", [{"id": "m2", "text": "我喜欢散步"}])
    assert subject.search("alice", "饮食") == [{"id": "m1", "text": "我不吃海鲜"}]
    subject.sync("alice", [{"id": "m1", "text": "我喜欢早晨散步"}])
    assert subject.search("alice", "运动") == [{"id": "m1", "text": "我喜欢早晨散步"}]
    subject.delete_user("alice")
    assert subject.search("alice", "偏好") == []
    assert subject.search("bob", "运动") == [{"id": "m2", "text": "我喜欢散步"}]
