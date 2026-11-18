"""FastAPI 应用入口。

启动方式：
    # 本地直跑（自带 uvicorn）
    python -m app.main

    # Docker / 生产
    uvicorn app.main:app --host 0.0.0.0 --port 6549 --workers 1 --no-proxy-headers
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api import (
    admin,
    admin_import_export,
    admin_projects,
    admin_rules,
    auth,
    compare,
    health,
    history,
)
from app.core.config import PROJECT_ROOT, get_config
from app.core.logging import setup_logging
from app.core.auth import AuthMiddleware, admin_auth
from app.core.access_control import initialize_auth
from app.schemas.security import PASSWORD_LENGTH_MESSAGE

# Configure handlers during lifespan, after test/deployment configuration is selected.
# Importing the application must not read deployment credentials.
log = logging.getLogger("aicheck.boot")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    from app.core.startup import seed_rules_if_empty
    from app.db import repo_rules
    from app.db.bootstrap import bootstrap_database
    from app.db.session import get_db, get_sessionmaker

    initialize_auth()
    admin_auth.reset()
    cfg = get_config()
    llm_cfg = cfg.get("llm", {})

    # 测试通过 dependency_overrides 注入自己的会话,此时不应连真 MySQL
    if get_db in _app.dependency_overrides:
        log.info("检测到会话依赖被覆盖(测试环境),跳过建库建表与首次导入")
        yield
        log.info("服务关闭")
        return

    bootstrap_database()

    with get_sessionmaker()() as db:
        seed_rules_if_empty(db)
        rules = repo_rules.list_rules(db)
        log.info(
            "服务启动 host=%s port=%s llm.model=%s backend=%s rules=%d",
            cfg.get("app", {}).get("host"),
            cfg.get("app", {}).get("port"),
            llm_cfg.get("model"),
            llm_cfg.get("backend"),
            len(rules),
        )
        for r in rules:
            log.info("  规则 %s %s", r.rule_id, r.name or "")
    yield
    log.info("服务关闭")


app = FastAPI(
    title="AI交叉校验 - 规则比对引擎",
    version="0.1.0",
    description=(
        "投资项目文件合规审核第二阶段:按规则 ID 调用 LLM 执行判断或自定义输出,"
        "返回结果、中文理由、耗时与 token 成本。规则配置存于 MySQL,每次请求实时读取。"
    ),
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.rstrip("/") == "/auth/login" and any(
        error["loc"] == ("body", "password")
        and error["type"] in {"string_too_short", "string_too_long"}
        for error in exc.errors()
    ):
        return JSONResponse({"detail": PASSWORD_LENGTH_MESSAGE}, status_code=422)
    if request.url.path.startswith(("/auth/", "/admin/access-control", "/admin/model-config")):
        return JSONResponse({"detail": "请求格式不正确，请检查字段和长度"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(OperationalError)
def _db_unavailable(_request: Request, exc: OperationalError) -> JSONResponse:
    """数据库不可用是依赖故障,返 503 而非 500。

    连接池已配 pool_pre_ping,能自愈的断连不会走到这里;走到这里说明
    库确实连不上,让调用方知道该重试而不是当成业务错误。
    """
    log.error("数据库不可用: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "数据库不可用,请稍后重试"},
    )


app.include_router(health.router)
app.add_middleware(AuthMiddleware)
app.include_router(auth.router)
app.include_router(compare.router)
app.include_router(admin.router, prefix="/admin")
app.include_router(admin_rules.router, prefix="/admin")
app.include_router(admin_projects.router, prefix="/admin")
app.include_router(admin_import_export.router, prefix="/admin")
app.include_router(history.router, prefix="/admin")


# ---------------------------------------------------------------------------
# UI 静态资源:挂到 /ui,根路径 / 自动 302 到 /ui/
# ---------------------------------------------------------------------------

_UI_DIR = PROJECT_ROOT / "ui"
if _UI_DIR.is_dir():
    # Register before /ui's static mount so the public URL has no file suffix.
    @app.api_route("/ui/login", methods=["GET", "HEAD"], include_in_schema=False)
    def _login_page() -> FileResponse:
        return FileResponse(_UI_DIR / "login.html", media_type="text/html")

    @app.api_route("/ui/login/", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/ui/login.html", methods=["GET", "HEAD"], include_in_schema=False)
    def _legacy_login_redirect() -> RedirectResponse:
        return RedirectResponse("/ui/login", status_code=308)

    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def _root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui/")
else:
    log.warning("UI 目录不存在: %s,跳过静态资源挂载", _UI_DIR)


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
        proxy_headers=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
