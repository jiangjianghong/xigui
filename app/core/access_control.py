"""Atomic, versioned credential administration; plaintext API keys are never stored."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from argon2 import PasswordHasher

from app.core.config import config_snapshot, update_config
from app.schemas.security import validate_document


def public_config(document: dict, version: str) -> dict:
    access = validate_document(document).access_control
    return {
        "version": version,
        "api_keys": [key.model_dump(mode="json", exclude={"key_hash"}) for key in access.api_keys],
        "ip_allowlist": [entry.model_dump(mode="json") for entry in access.ip_allowlist],
    }


def read_access_control() -> dict:
    return public_config(*config_snapshot())


def mutate_access_control(kind: str, operation: str, version: str,
                          entry_id: str | None, values: dict) -> dict:
    if kind not in {"api_keys", "ip_allowlist"}:
        raise ValueError("未知访问控制类型")
    allowed = {"name", "expires_at", "enabled"} if kind == "api_keys" else {"name", "network", "enabled"}
    if set(values) - allowed:
        raise ValueError("包含不允许修改的字段")
    if operation in {"rotate", "delete"} and values:
        raise ValueError("重新生成或删除时不接受字段更新")
    if operation == "rotate" and kind != "api_keys":
        raise ValueError("只有 API Key 可以重新生成")
    if operation not in {"create", "update", "delete", "rotate"}:
        raise ValueError("未知操作")
    values = dict(values)
    if "name" in values and isinstance(values["name"], str):
        values["name"] = values["name"].strip()
    if values.get("expires_at") is not None:
        try:
            expires = datetime.fromisoformat(values["expires_at"].replace("Z", "+00:00"))
            if expires.utcoffset() is None or expires <= datetime.now(timezone.utc):
                raise ValueError()
        except (TypeError, AttributeError, ValueError) as exc:
            raise ValueError("有效期必须是带时区的未来时间") from exc
        values["expires_at"] = expires.isoformat()
    raw_key = None

    def mutate(document):
        nonlocal raw_key
        entries = document.setdefault("access_control", {}).setdefault(kind, [])
        if operation == "create":
            if entry_id is not None:
                raise ValueError("创建时不能指定 ID")
            entry = {"id": secrets.token_hex(16), "enabled": True, **values}
            entries.append(entry)
        else:
            entry = next((item for item in entries if item["id"] == entry_id), None)
            if entry is None:
                raise ValueError("条目不存在，请刷新后重试")
            if operation == "delete":
                entries.remove(entry)
            elif operation == "update":
                entry.update(values)
        if kind == "api_keys" and operation in {"create", "rotate"}:
            raw_key = "xj_" + secrets.token_urlsafe(32)
            entry.update(key_hash=hashlib.sha256(raw_key.encode()).hexdigest(), hint="xj_…" + raw_key[-4:])
            entry.setdefault("created_at", datetime.now(timezone.utc).isoformat())

    result = public_config(*update_config(mutate, version=version))
    if raw_key is not None:
        result["key"] = raw_key
    return result


def initialize_auth() -> None:
    document, version = config_snapshot()
    settings = validate_document(document).settings
    if settings.password is not None:
        def migrate(data):
            section = data["settings"]
            if not settings.password_hash and settings.password:
                section["password_hash"] = PasswordHasher().hash(settings.password)
            section.pop("password", None)
            section.setdefault("username", "admin")
        document, _ = update_config(migrate, version=version)
    if not validate_document(document).settings.password_hash:
        raise RuntimeError("管理员密码未配置。请先执行 uv run python scripts/set_admin_password.py")
