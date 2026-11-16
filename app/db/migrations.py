"""Small, idempotent schema migrations applied during application startup."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, JSON, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

log = logging.getLogger("aicheck.db")

_ADDITIVE_COLUMNS = {
    "rules": {
        "rule_type": "VARCHAR(16) NOT NULL DEFAULT 'judge'",
        "output_schema": "JSON NULL",
    },
    "compare_history": {
        "rule_type": "VARCHAR(16) NOT NULL DEFAULT 'judge'",
    },
}


def _quoted(engine: Engine, identifier: str) -> str:
    return engine.dialect.identifier_preparer.quote(identifier)


def _column_names(engine: Engine, table: str) -> set[str]:
    inspector = inspect(engine)
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add_column_if_missing(
    engine: Engine, table: str, column: str, definition: str
) -> bool:
    if not inspect(engine).has_table(table):
        return False
    if column in _column_names(engine, table):
        return False

    table_sql = _quoted(engine, table)
    column_sql = _quoted(engine, column)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {table_sql} ADD COLUMN {column_sql} {definition}")
            )
    except (OperationalError, ProgrammingError):
        # Multiple service instances can observe the same missing column.
        if column in _column_names(engine, table):
            return False
        raise

    log.info("数据库迁移：已为 %s 添加字段 %s", table, column)
    return True


def _is_json_type(column_type: object) -> bool:
    visit_name = getattr(column_type, "__visit_name__", "").lower()
    return isinstance(column_type, JSON) or visit_name == "json"


def _convert_history_result(engine: Engine) -> bool:
    """Convert legacy boolean history results to JSON without losing rows.

    ``result_json`` acts as a recoverable staging column if startup is
    interrupted between adding the column and promoting it to ``result``.
    """
    names = _column_names(engine, "compare_history")
    if not names:
        return False

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("compare_history")
    }
    result_exists = "result" in columns
    staging_exists = "result_json" in columns
    if result_exists and _is_json_type(columns["result"]["type"]):
        return False
    if not result_exists and not staging_exists:
        raise RuntimeError("compare_history 缺少 result 字段，无法自动迁移历史结果")

    table_sql = _quoted(engine, "compare_history")
    result_sql = _quoted(engine, "result")
    staging_sql = _quoted(engine, "result_json")
    changed = False

    if result_exists and not staging_exists:
        _add_column_if_missing(engine, "compare_history", "result_json", "JSON NULL")
        staging_exists = True
        changed = True

    if result_exists and staging_exists:
        # The source column is the legacy 0/1 value. JSON scalar booleans are
        # used so existing judge history keeps its public true/false shape.
        if engine.dialect.name == "sqlite":
            expression = f"CASE WHEN {result_sql} <> 0 THEN 'true' ELSE 'false' END"
        elif engine.dialect.name == "mysql":
            expression = f"JSON_EXTRACT(IF({result_sql} <> 0, 'true', 'false'), '$')"
        else:
            raise RuntimeError(
                f"暂不支持 {engine.dialect.name} 数据库的历史结果类型迁移"
            )
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"UPDATE {table_sql} SET {staging_sql} = {expression} "
                    f"WHERE {staging_sql} IS NULL"
                ))
        except (OperationalError, ProgrammingError):
            # A parallel startup may have promoted the column between inspect
            # and UPDATE, removing the staging column we were about to update.
            refreshed = {
                column["name"]: column
                for column in inspect(engine).get_columns("compare_history")
            }
            if "result" in refreshed and _is_json_type(refreshed["result"]["type"]):
                return True
            raise

    latest_names = _column_names(engine, "compare_history")
    if "result_json" not in latest_names:
        return changed
    if "result" in latest_names and _is_json_type(
        next(
            column["type"]
            for column in inspect(engine).get_columns("compare_history")
            if column["name"] == "result"
        )
    ):
        return changed

    try:
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                if "result" in latest_names:
                    conn.execute(text(f"ALTER TABLE {table_sql} DROP COLUMN {result_sql}"))
                conn.execute(text(
                    f"ALTER TABLE {table_sql} RENAME COLUMN {staging_sql} TO {result_sql}"
                ))
            elif engine.dialect.name == "mysql":
                if "result" in latest_names:
                    conn.execute(text(
                        f"ALTER TABLE {table_sql} DROP COLUMN {result_sql}, "
                        f"CHANGE COLUMN {staging_sql} {result_sql} JSON NOT NULL"
                    ))
                else:
                    conn.execute(text(
                        f"ALTER TABLE {table_sql} CHANGE COLUMN "
                        f"{staging_sql} {result_sql} JSON NOT NULL"
                    ))
            else:
                raise RuntimeError(
                    f"暂不支持 {engine.dialect.name} 数据库的历史结果类型迁移"
                )
    except (OperationalError, ProgrammingError):
        # Another instance may have completed the promotion after our inspect.
        refreshed = {
            column["name"]: column
            for column in inspect(engine).get_columns("compare_history")
        }
        if "result" in refreshed and _is_json_type(refreshed["result"]["type"]):
            return changed
        raise

    log.info("数据库迁移：compare_history.result 已升级为 JSON")
    return True


def migrate_schema(engine: Engine) -> None:
    """Add the custom-output fields missing from an existing database."""
    for table, columns in _ADDITIVE_COLUMNS.items():
        for column, definition in columns.items():
            _add_column_if_missing(engine, table, column, definition)
    _convert_history_result(engine)

