"""统一日志配置。

要求（来自 plan）：
- 控制台 + 文件双输出，docker stdout 与 logs/*.log 都要详细。
- 文件用 ``RotatingFileHandler`` 避免无限增长。
- 配置参数从 ``config.yaml`` 的 ``app`` 节读取，跟着活配置走。
- uvicorn 自身的访问日志/错误日志也接入同一套 handler。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_config

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(threadName)s]: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_setup_done = False


def _resolve_log_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def setup_logging() -> None:
    """初始化全局日志。幂等，多次调用安全。"""
    global _setup_done
    if _setup_done:
        return

    cfg = get_config().get("app", {})
    level_name = str(cfg.get("log_level", "info")).upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = _resolve_log_path(cfg.get("log_file_path", "logs/ai_cross_check.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # 控制台 → stdout（docker logs 抓取）
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    # 文件 → 滚动切割
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=int(cfg.get("log_max_bytes", 10 * 1024 * 1024)),
        backupCount=int(cfg.get("log_backup_count", 7)),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    root = logging.getLogger()
    # 清掉 uvicorn / pytest 等可能预设的 handler，避免重复输出
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # 把 uvicorn 三个 logger 也接到 root，并去掉它们自带的 handler
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level)

    # openai SDK 噪音多，固定一档
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _setup_done = True
    logging.getLogger("aicheck.boot").info(
        "日志初始化完成 level=%s file=%s pid=%d", level_name, log_path, os.getpid()
    )
