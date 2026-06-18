"""请求历史记录存储。

使用内存列表存储，服务重启后清空。可后续改为 SQLite 持久化。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any


class HistoryStore:
    """请求历史记录存储。"""

    def __init__(self, max_size: int = 1000):
        self._records: list[dict[str, Any]] = []
        self._max_size = max_size

    def add(self, record: dict[str, Any]) -> str:
        """添加一条记录，返回记录 ID。"""
        record_id = str(uuid.uuid4())[:8]
        record["id"] = record_id
        record["created_at"] = datetime.now().isoformat()
        self._records.insert(0, record)  # 最新的在前面
        if len(self._records) > self._max_size:
            self._records.pop()
        return record_id

    def list(
        self,
        *,
        rule_id: str | None = None,
        result: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """查询历史记录列表。"""
        filtered = self._records
        if rule_id is not None:
            filtered = [r for r in filtered if r.get("rule_id") == rule_id]
        if result is not None:
            filtered = [r for r in filtered if r.get("result") == result]
        total = len(filtered)
        items = filtered[offset : offset + limit]
        return {"total": total, "items": items}

    def get(self, record_id: str) -> dict[str, Any] | None:
        """获取单条记录详情。"""
        for r in self._records:
            if r.get("id") == record_id:
                return r
        return None

    def clear(self) -> int:
        """清空所有记录，返回清空数量。"""
        count = len(self._records)
        self._records.clear()
        return count


# 全局单例
_history_store = HistoryStore()


def get_history_store() -> HistoryStore:
    return _history_store
