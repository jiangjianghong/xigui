"""FastAPI 应用入口。

启动方式：
    # 本地直跑（自带 uvicorn）
    python -m app.main

    # Docker / 生产
    uvicorn app.main:app --host 0.0.0.0 --port 6549
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import admin, compare, health
from app.core.config import get_config, get_rules
from app.core.logging import setup_logging

# 必须最先调用，否则后续模块的 logger 输出可能用默认配置
setup_logging()
log = logging.getLogger("wanzi.boot")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    cfg = get_config()
    llm_cfg = cfg.get("llm", {})
    rules = get_rules()
    log.info(
        "服务启动 host=%s port=%s llm.model=%s backend=%s rules=%d",
        cfg.get("app", {}).get("host"),
        cfg.get("app", {}).get("port"),
        llm_cfg.get("model"),
        llm_cfg.get("backend"),
        len(rules),
    )
    for r in rules:
        log.info("  规则 %s [%s] %s", r.get("id"), r.get("type"), r.get("name", ""))
    yield
    log.info("服务关闭")


app = FastAPI(
    title="皖资规约 - 规则比对引擎",
    version="0.1.0",
    description=(
        "投资项目文件合规审核第二阶段：根据规则 ID 对两个字段做语义/数值/时间"
        "/严格比对，返回布尔结论 + 中文理由 + 耗时 + token 成本。"
    ),
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(compare.router)
app.include_router(admin.router, prefix="/admin")


def main() -> None:
    import uvicorn

    app_cfg = get_config().get("app", {})
    uvicorn.run(
        "app.main:app",
        host=app_cfg.get("host", "0.0.0.0"),
        port=int(app_cfg.get("port", 6549)),
        log_level=str(app_cfg.get("log_level", "info")).lower(),
        # 我们已经接管了 logging，禁用 uvicorn 默认日志配置避免重复
        log_config=None,
    )


if __name__ == "__main__":
    main()
