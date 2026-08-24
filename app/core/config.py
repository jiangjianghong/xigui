"""集中式应用配置加载。

从单个 YAML 文件（默认 ``config/config.yaml``）加载所有配置。路径可通过
``CONFIG_PATH`` 环境变量覆盖，方便同一镜像在不同主机间复用而无需重新构建。

与 orc_gonngguan 版本的差异
-----------------------------
1. 不再使用 ``@lru_cache``。改为基于文件 mtime 的轻量缓存：每次调用都做一次
   ``stat()`` 比对 mtime，只有变化时才重新读 YAML。这样直接编辑挂载的
   ``config/config.yaml`` 即可在下一次请求时生效，不必重启容器。
2. ``build_extra_body()`` 保留原样，用于在 dashscope / vllm 两种后端之间
   屏蔽 ``enable_thinking`` 传参形式的差异。
3. 新增 ``get_db_config()`` 和 ``build_db_url()``，读取 ``mysql:`` 节并拼装
   SQLAlchemy 连接串，供 ORM 引擎与建库脚本共用。

规则不再由本模块提供：规则真源已是 MySQL，读路径见 ``app/db/repo_rules.py``。
``rules_config_path`` 仅用于首次导入与默认导出路径，解析见
``app/core/startup.resolve_rules_path()``。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

_cfg_cache: dict[str, Any] = {"data": None, "mtime": 0.0, "path": None}
_lock = threading.Lock()


def _resolve_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.getenv("CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """强制从磁盘读取一次配置，不走缓存。"""
    return _read_yaml(_resolve_config_path(path))


def get_config() -> dict[str, Any]:
    """读取主配置；基于 mtime 自动重载，无需重启进程。"""
    path = _resolve_config_path()
    with _lock:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError as e:
            raise FileNotFoundError(f"配置文件不存在: {path}") from e
        if (
            _cfg_cache["data"] is None
            or _cfg_cache["path"] != path
            or mtime != _cfg_cache["mtime"]
        ):
            _cfg_cache["data"] = _read_yaml(path)
            _cfg_cache["mtime"] = mtime
            _cfg_cache["path"] = path
        return _cfg_cache["data"]


def reload_config() -> dict[str, Any]:
    """同上，针对主配置。返回重新加载后的配置字典。"""
    with _lock:
        _cfg_cache["data"] = None
        _cfg_cache["mtime"] = 0.0
    return get_config()


def build_extra_body(model_cfg: dict[str, Any]) -> dict[str, Any]:
    """构造 OpenAI ``extra_body``，控制思考模式开关。

    不同后端对 ``enable_thinking`` 的传参位置不一样，本函数做一次屏蔽：

    * ``dashscope``（默认）—— 阿里云百炼 OpenAI 兼容模式，顶层字段:
      ``{"enable_thinking": false}``
    * ``vllm`` —— 本地 vLLM 部署的 Qwen3 系列，需通过 chat template 传:
      ``{"chat_template_kwargs": {"enable_thinking": false}}``

    如果后续接入新后端（Ollama / TGI 等），在此处新增分支即可，业务代码无需改动。
    """
    backend = (model_cfg.get("backend") or "dashscope").lower()
    enable_thinking = bool(model_cfg.get("enable_thinking", False))

    if backend == "vllm":
        return {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
    if backend == "dashscope":
        return {"enable_thinking": enable_thinking}
    return {
        "enable_thinking": enable_thinking,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }


# ---------------------------------------------------------------------------
# 数据库配置
# ---------------------------------------------------------------------------

_DB_DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 3306,
    "database": "ai_cross_check",
    "username": "root",
    "password": "",
    "charset": "utf8mb4",
    "pool_size": 5,
    "max_overflow": 10,
    "echo": False,
}


def get_db_config() -> dict[str, Any]:
    """读取 ``mysql:`` 节，缺项回落到 ``_DB_DEFAULTS``。

    其中 ``host`` ``port`` ``database`` ``username`` ``password`` 这五项额外支持
    同名环境变量覆盖（``MYSQL_HOST`` 等），优先级高于配置文件——这几项是跨环境
    部署时真正会变的。其余四项只读配置文件。

    分离字段而非单行连接串：改起来不易出错，也便于逐项做环境变量覆盖。
    """
    raw = get_config().get("mysql") or {}

    def take(key: str) -> Any:
        """yaml → 默认值。"""
        val = raw.get(key)
        return val if val not in (None, "") else _DB_DEFAULTS[key]

    def pick(key: str) -> Any:
        """环境变量 → yaml → 默认值。"""
        env = os.getenv(f"MYSQL_{key.upper()}")
        return env if env not in (None, "") else take(key)

    return {
        "host": str(pick("host")),
        "port": int(pick("port")),
        "database": str(pick("database")),
        "username": str(pick("username")),
        "password": str(pick("password")),
        "charset": str(take("charset")),
        "pool_size": int(take("pool_size")),
        "max_overflow": int(take("max_overflow")),
        "echo": bool(take("echo")),
    }


def build_db_url(*, with_database: bool = True) -> str:
    """拼装 SQLAlchemy 连接串。

    用户名密码必须过 ``quote``——含 ``@`` ``:`` ``/`` ``#`` 任一字符时，未编码的
    连接串会被解析错位，而报错通常指向「host 不存在」，极难定位。

    这里用 ``quote(x, safe="")`` 而非 ``quote_plus``：后者把空格编成 ``+``，而
    SQLAlchemy 解析 URL 时用的是 ``unquote`` 不是 ``unquote_plus``，``+`` 会原样
    留在密码里，认证报 Access denied，配置文件却看着完全正常。

    返回值含明文密码，**不要直接打进日志**；需要打印时走
    ``make_url(url).render_as_string(hide_password=True)``。

    ``with_database=False`` 用于建库前连到 server 本身。
    """
    c = get_db_config()
    user = quote(c["username"], safe="")
    pwd = quote(c["password"], safe="")
    db = c["database"] if with_database else ""
    return (
        f"mysql+pymysql://{user}:{pwd}@{c['host']}:{c['port']}/{db}"
        f"?charset={c['charset']}"
    )
