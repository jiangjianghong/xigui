"""AI交叉校验 - 启动入口。

用法:
    uv run app.py                # 本地开发(推荐)
    python app.py                # 不用 uv 时
    uvicorn app.main:app --workers 1 --no-proxy-headers  # 生产/Docker
"""

from app.core.config import get_config


def main() -> None:
    import uvicorn

    app_cfg = get_config().get("app", {})
    uvicorn.run(
        "app.main:app",
        host=app_cfg.get("host", "0.0.0.0"),
        port=int(app_cfg.get("port", 6549)),
        log_level=str(app_cfg.get("log_level", "info")).lower(),
        log_config=None,
        proxy_headers=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
