"""历史仓储。

对外 ``id`` 保持字符串：内存版用的是 ``uuid4()[:8]``，前端按字符串处理。
改成数字会波及历史页的详情展开逻辑，没必要为了内部主键类型去动 UI。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CompareHistory


def add(db: Session, record: dict[str, Any]) -> str:
    row = CompareHistory(
        rule_id=record.get("rule_id", ""),
        rule_name=record.get("rule_name") or "",
        project_key=record.get("project_key"),
        project_name=record.get("project_name"),
        fields=record.get("fields") or [],
        result=bool(record.get("result")),
        reason=record.get("reason"),
        time_ms=int(record.get("time_ms") or 0),
        cost=record.get("cost") or {},
        raw_request=record.get("raw_request"),
    )
    db.add(row)
    db.flush()
    return str(row.id)


def get(db: Session, record_id: str) -> CompareHistory | None:
    """按 id 取。非数字 id 返 None 而非抛异常——前端传脏值不该 500。"""
    try:
        pk = int(record_id)
    except (TypeError, ValueError):
        return None
    return db.get(CompareHistory, pk)


def list_records(
    db: Session,
    *,
    rule_id: str | None = None,
    result: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(CompareHistory)
    count_stmt = select(func.count(CompareHistory.id))
    if rule_id is not None:
        stmt = stmt.where(CompareHistory.rule_id == rule_id)
        count_stmt = count_stmt.where(CompareHistory.rule_id == rule_id)
    if result is not None:
        stmt = stmt.where(CompareHistory.result == result)
        count_stmt = count_stmt.where(CompareHistory.result == result)

    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(
        stmt.order_by(CompareHistory.id.desc()).limit(limit).offset(offset)
    ).all()
    return {"total": total, "items": [to_api_dict(r) for r in rows]}


def clear(db: Session) -> int:
    n = db.query(CompareHistory).delete(synchronize_session=False)
    db.flush()
    return int(n)


def to_api_dict(row: CompareHistory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "rule_id": row.rule_id,
        "rule_name": row.rule_name or "",
        "project_key": row.project_key,
        "project_name": row.project_name,
        "fields": row.fields or [],
        "result": bool(row.result),
        "reason": row.reason or "",
        "time_ms": row.time_ms,
        "cost": row.cost or {},
        "raw_request": row.raw_request,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
