"""Strict schemas for persisted credentials and access-control metadata."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Annotated

from argon2 import extract_parameters
from argon2.low_level import Type
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=100)]
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 12
PASSWORD_LENGTH_MESSAGE = "密码长度必须为 6–12 个字符"
AdminPassword = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


def network(value: str) -> str:
    if "%" in value:
        raise ValueError("不接受带 zone ID 的地址")
    return str(ipaddress.ip_network(value.strip(), strict=False))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminSettings(StrictModel):
    username: Name = "admin"
    password_hash: str = ""
    password: AdminPassword | None = Field(default=None, exclude=True)
    session_minutes: Annotated[int, Field(strict=True, ge=1, le=1440)] = 30
    secure_cookie: StrictBool = False

    @field_validator("password_hash")
    @classmethod
    def valid_hash(cls, value):
        if value:
            try:
                params = extract_parameters(value)
                if params.type != Type.ID:
                    raise ValueError("需要 Argon2id")
            except Exception as exc:
                raise ValueError("需要有效的 Argon2id 密码哈希") from exc
        return value


class APIKey(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: Name
    key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    hint: str = Field(min_length=4, max_length=32)
    created_at: datetime
    expires_at: datetime | None = None
    enabled: StrictBool = True

    @field_validator("created_at", "expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return value


class AllowedIP(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: Name
    network: str
    enabled: StrictBool = True

    _network = field_validator("network")(network)


class AccessControl(StrictModel):
    api_keys: list[APIKey] = Field(default_factory=list, max_length=1000)
    ip_allowlist: list[AllowedIP] = Field(default_factory=list, max_length=1000)
    trusted_proxies: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("trusted_proxies")
    @classmethod
    def valid_proxies(cls, values):
        return [network(value) for value in values]

    @model_validator(mode="after")
    def unique_ids(self):
        for entries in (self.api_keys, self.ip_allowlist):
            if len({entry.id for entry in entries}) != len(entries):
                raise ValueError("条目 ID 重复")
        return self


class SecurityConfig(BaseModel):
    # Existing app/LLM/MySQL settings remain owned by their respective consumers.
    model_config = ConfigDict(extra="ignore")
    settings: AdminSettings = Field(default_factory=AdminSettings)
    access_control: AccessControl = Field(default_factory=AccessControl)


def validate_document(document: dict) -> SecurityConfig:
    """Validate the entire YAML mapping, including every persisted auth entry."""
    if not isinstance(document, dict):
        raise ValueError("配置必须是 YAML 映射")
    for key in ("app", "llm", "mysql"):
        if key in document and not isinstance(document[key], dict):
            raise ValueError(f"{key} 必须是映射")
    if "rules_config_path" in document and not isinstance(document["rules_config_path"], str):
        raise ValueError("rules_config_path 必须是路径字符串")
    return SecurityConfig.model_validate(document)
