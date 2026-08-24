"""``/admin/history`` 路由实现。

历史已落库(``compare_history`` 表),重启不再丢数据。响应结构与此前的
内存实现保持一致,前端历史页无需改动。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import repo_history
from app.db.session import get_db

router = APIRouter(tags=["history"])


@router.get("/history", summary="查询请求历史记录列表")
def list_history(
    rule_id: str | None = Query(None, description="按规则 ID 过滤"),
    result: bool | None = Query(
        None, description="按比对结果过滤 (true=一致, false=不一致)"
    ),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: Session = Depends(get_db),
) -> dict:
    return repo_history.list_records(
        db, rule_id=rule_id, result=result, limit=limit, offset=offset
    )


@router.get("/history/{record_id}", summary="获取单条历史记录详情")
def get_history_record(record_id: str, db: Session = Depends(get_db)) -> dict:
    row = repo_history.get(db, record_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"记录不存在: {record_id}",
        )
    return repo_history.to_api_dict(row)


@router.delete("/history", summary="清空所有历史记录")
def clear_history(db: Session = Depends(get_db)) -> dict:
    count = repo_history.clear(db)
    db.commit()
    return {"message": f"已清空 {count} 条记录"}
