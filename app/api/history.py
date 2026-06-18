"""``/admin/history`` 路由实现。

提供请求历史记录的查询和管理接口。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.history import get_history_store

router = APIRouter(tags=["history"])


@router.get(
    "/history",
    summary="查询请求历史记录列表",
)
def list_history(
    rule_id: str | None = Query(None, description="按规则 ID 过滤"),
    result: bool | None = Query(None, description="按比对结果过滤 (true=一致, false=不一致)"),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
) -> dict:
    store = get_history_store()
    return store.list(rule_id=rule_id, result=result, limit=limit, offset=offset)


@router.get(
    "/history/{record_id}",
    summary="获取单条历史记录详情",
)
def get_history_record(record_id: str) -> dict:
    store = get_history_store()
    record = store.get(record_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"记录不存在: {record_id}",
        )
    return record


@router.delete(
    "/history",
    summary="清空所有历史记录",
)
def clear_history() -> dict:
    store = get_history_store()
    count = store.clear()
    return {"message": f"已清空 {count} 条记录"}
