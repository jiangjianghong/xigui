"""手工把 yaml 导入数据库。

用法:
    uv run python scripts/import_rules.py                    # 导入 config/rules.yaml
    uv run python scripts/import_rules.py path/to/other.yaml

语义与 Web UI 的「导入」一致:按 id 合并 upsert,全文件单事务。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging  # noqa: E402
from app.core.rules_sync import import_bundle  # noqa: E402
from app.core.startup import resolve_rules_path  # noqa: E402
from app.db.bootstrap import bootstrap_database  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402


def main() -> int:
    setup_logging()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else resolve_rules_path()
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    bootstrap_database()
    with get_sessionmaker()() as db:
        try:
            result = import_bundle(db, path.read_text(encoding="utf-8"))
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"导入失败,已回滚: {e}", file=sys.stderr)
            return 1

    print(
        f"导入完成: 项目 新增 {result.projects_created} / 更新 {result.projects_updated}，"
        f"规则 新增 {result.rules_created} / 更新 {result.rules_updated}"
    )
    for w in result.warnings:
        print(f"  告警: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
