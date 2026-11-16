"""三张表的 ORM 定义。

字段增改约定
------------
受控的向前兼容字段迁移在 ``app.db.migrations`` 中登记，并于启动时检查和补齐；
其他结构变更仍需同步更新迁移逻辑与 ``docs/schema.sql``。

方言兼容
--------
主键用 ``_BIGINT_PK``：MySQL 下是 BIGINT，SQLite 下退化成 INTEGER。
SQLite 只把 ``INTEGER PRIMARY KEY`` 当 rowid 别名，渲染成 BIGINT 就不自增了，
而测试全跑在 SQLite 上。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_BIGINT_PK = BigInteger().with_variant(sqlite.INTEGER(), "sqlite")


class Base(DeclarativeBase):
    pass


class Project(Base):
    """项目。``key`` 供 yaml 跨环境引用，``id`` 供 DB 内部关联。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    rules: Mapped[list[Rule]] = relationship(back_populates="project")


class Rule(Base):
    """规则。``rule_id`` 是下游 ``/compare`` 传的业务 ID，全局唯一。

    主键用自增 ``id`` 而非 ``rule_id``：PUT 接口支持 rename，
    ``rule_id`` 若为主键则 rename 变成改主键。
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    project_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    rule_type: Mapped[str] = mapped_column(String(16), nullable=False, default="judge")
    field_defs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    require_field_name: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project | None] = relationship(back_populates="rules")


class CompareHistory(Base):
    """比对历史。

    刻意不设外键：存的是调用当时的快照，规则后续改名/删除/换项目
    都不该影响既有历史。
    """

    __tablename__ = "compare_history"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    rule_type: Mapped[str] = mapped_column(String(16), nullable=False, default="judge")
    project_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fields: Mapped[list] = mapped_column(JSON, nullable=False)
    result: Mapped[Any] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )
