"""Optional Mem0 OSS semantic index. Local SQL memories remain authoritative.

No SDK is imported and no data leaves the service unless explicitly enabled.
Mem0's infer=False preserves the user's exact correction instead of rephrasing it.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
from threading import RLock


class MemoryAdapterError(RuntimeError):
    pass


class MemoryAdapter:
    def __init__(self, enabled: bool = True, *, client=None, config: dict | None = None):
        self.enabled = enabled
        self._client = client
        self._config = config
        self._lock = RLock()

    @classmethod
    def from_env(cls):
        enabled = os.getenv("MEM0_ENABLED", "false").lower() == "true"
        if not enabled:
            return cls(enabled=False)
        path = os.getenv("MEM0_CONFIG_PATH")
        if not path:
            raise MemoryAdapterError("启用语义记忆前，请配置 MEM0_CONFIG_PATH。")
        try:
            config = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("expected object")
        except (OSError, ValueError) as exc:
            raise MemoryAdapterError("语义记忆配置文件无法读取。") from exc
        return cls(config=config)

    @property
    def client(self):
        if self._client is None:
            try:
                # Set before importing the optional SDK. Callers may choose
                # providers in their private config; we never imply local inference.
                os.environ.setdefault("MEM0_TELEMETRY", "false")
                from mem0 import Memory
                self._client = Memory.from_config(self._config or {})
            except Exception as exc:
                raise MemoryAdapterError("语义记忆服务初始化失败，请检查可选依赖和配置。") from exc
        return self._client

    @staticmethod
    def namespace(user_id):
        if not isinstance(user_id, str) or not user_id.strip():
            raise MemoryAdapterError("记忆操作缺少用户范围。")
        return f"companion:{user_id}"

    def _all(self, user_id):
        client = self.client
        scope = self.namespace(user_id)
        params = inspect.signature(client.get_all).parameters
        # Current OSS API uses filters/top_k. Older releases use user_id/limit.
        kwargs = {"filters": {"user_id": scope}, "top_k": 10000} if "top_k" in params else {"user_id": scope, "limit": 10000}
        response = client.get_all(**kwargs)
        rows = response.get("results", []) if isinstance(response, dict) else response
        if not isinstance(rows, list) or len(rows) >= 10000:
            raise MemoryAdapterError("语义记忆索引超过同步范围，需要维护后重试。")
        return rows

    def sync(self, user_id: str, memories: list[dict]) -> None:
        if not self.enabled:
            return
        with self._lock:
            try:
                desired = {str(m["id"]): str(m["text"]) for m in memories}
                if len(desired) != len(memories):
                    raise ValueError("duplicate local ids")
                found = set()
                for remote in self._all(user_id):
                    metadata = remote.get("metadata") or {}
                    local_id = metadata.get("companion_id")
                    if metadata.get("owner_id") != user_id:
                        continue
                    text = desired.get(local_id)
                    digest = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
                    if local_id in found or text is None or metadata.get("companion_digest") != digest:
                        self.client.delete(remote["id"])
                    else:
                        found.add(local_id)
                for memory_id, text in desired.items():
                    if memory_id not in found:
                        self.client.add(text, user_id=self.namespace(user_id), infer=False, metadata={
                            "owner_id": user_id, "companion_id": memory_id,
                            "companion_digest": hashlib.sha256(text.encode()).hexdigest(),
                        })
            except Exception as exc:
                if isinstance(exc, MemoryAdapterError):
                    raise
                raise MemoryAdapterError("语义记忆同步失败，本地记录仍保留，请重试。") from exc

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        if not self.enabled:
            return []
        with self._lock:
            try:
                params = inspect.signature(self.client.search).parameters
                scope = self.namespace(user_id)
                kwargs = {"filters": {"user_id": scope}, "top_k": limit} if "top_k" in params else {"user_id": scope, "limit": limit}
                response = self.client.search(query, **kwargs)
                rows = response.get("results", []) if isinstance(response, dict) else response
                return [{"id": r["metadata"]["companion_id"], "text": r.get("memory", "")} for r in rows
                        if (r.get("metadata") or {}).get("owner_id") == user_id and (r.get("metadata") or {}).get("companion_id")]
            except Exception as exc:
                raise MemoryAdapterError("语义记忆检索暂时不可用，请重试。") from exc

    def delete_user(self, user_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            try:
                self.client.delete_all(user_id=self.namespace(user_id))
                if self._all(user_id):
                    raise ValueError("index still contains user data")
            except Exception as exc:
                raise MemoryAdapterError("语义记忆清理未完成，请恢复索引连接后重试删除。") from exc
