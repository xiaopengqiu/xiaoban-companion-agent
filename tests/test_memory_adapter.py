import pytest


class VectorBoundary:
    """SDK boundary fixture; no embedding/model network calls in these tests."""
    def __init__(self):
        self.rows = []
        self.calls = []

    def get_all(self, *, filters, top_k=20):
        self.calls.append(("get", filters))
        return {"results": [r for r in self.rows if r["user_id"] == filters["user_id"]][:top_k]}

    def add(self, messages, *, user_id, metadata, infer=True):
        self.calls.append(("add", user_id, infer))
        row = {"id": str(len(self.calls)), "user_id": user_id, "memory": messages, "metadata": metadata}
        self.rows.append(row)
        return {"results": [row]}

    def delete(self, memory_id):
        self.rows = [r for r in self.rows if r["id"] != memory_id]

    def search(self, query, *, filters, top_k=20):
        return self.get_all(filters=filters, top_k=top_k)

    def delete_all(self, *, user_id):
        self.rows = [r for r in self.rows if r["user_id"] != user_id]


def adapter(client):
    from companion.memory_adapter import MemoryAdapter
    return MemoryAdapter(client=client)


def test_disabled_never_initializes_a_remote_client():
    from companion.memory_adapter import MemoryAdapter
    subject = MemoryAdapter(enabled=False)
    subject.sync("u1", [{"id": "m1", "text": "不吃海鲜"}])
    assert subject.search("u1", "饮食") == []
    subject.delete_user("u1")


def test_sync_corrects_and_deletes_without_cross_user_changes():
    client = VectorBoundary()
    subject = adapter(client)
    subject.sync("a", [{"id": "m1", "text": "喜欢夜跑"}, {"id": "m2", "text": "不吃海鲜"}])
    subject.sync("b", [{"id": "m3", "text": "目标每周散步"}])
    subject.sync("a", [{"id": "m1", "text": "改成早晨散步"}])
    assert subject.search("a", "运动") == [{"id": "m1", "text": "改成早晨散步"}]
    assert subject.search("b", "运动") == [{"id": "m3", "text": "目标每周散步"}]
    assert all(call[2] is False for call in client.calls if call[0] == "add")


def test_repeated_sync_is_idempotent_and_delete_is_verified():
    client = VectorBoundary()
    subject = adapter(client)
    subject.sync("a", [{"id": "m1", "text": "工作日安静"}])
    subject.sync("a", [{"id": "m1", "text": "工作日安静"}])
    assert len(client.rows) == 1
    subject.delete_user("a")
    assert client.rows == []


def test_search_excludes_foreign_metadata_and_unmanaged_rows():
    client = VectorBoundary()
    subject = adapter(client)
    subject.sync("a", [{"id": "m1", "text": "喜欢茶"}])
    client.rows.extend([
        {"id": "rogue", "user_id": "companion:a", "memory": "其他信息", "metadata": {}},
        {"id": "foreign", "user_id": "companion:a", "memory": "他人信息", "metadata": {"owner_id": "b", "companion_id": "m2"}},
    ])
    assert subject.search("a", "偏好") == [{"id": "m1", "text": "喜欢茶"}]


def test_upstream_failure_is_sanitized_and_not_reported_as_cleanup_success():
    from companion.memory_adapter import MemoryAdapterError
    class Broken(VectorBoundary):
        def delete_all(self, **kwargs):
            raise RuntimeError("secret api-key-should-never-leak")
    subject = adapter(Broken())
    with pytest.raises(MemoryAdapterError, match="清理") as exc:
        subject.delete_user("a")
    assert "api-key" not in str(exc.value)
