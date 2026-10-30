"""Admin login/logout and access-control administration."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, ValidationError
from filelock import Timeout

from app.core.access_control import mutate_access_control, read_access_control
from app.core.auth import COOKIE_NAME, LoginError, admin_auth
from app.core.config import ConfigConflictError
from app.schemas.security import AdminPassword, StrictModel

router = APIRouter(tags=["auth"])


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: AdminPassword


class AccessMutation(StrictModel):
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["create", "update", "rotate", "delete"]
    id: str | None = None
    values: dict = Field(default_factory=dict)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    try:
        token, session, secure = admin_auth.login(
            body.username, body.password, request.state.client_ip, request.cookies.get(COOKIE_NAME),
        )
    except LoginError as exc:
        raise HTTPException(exc.status, exc.detail,
                            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None) from exc
    response.set_cookie(COOKIE_NAME, token, max_age=session["max_age"], httponly=True,
                        secure=secure, samesite="strict", path="/")
    return {"authenticated": True, "username": session["username"], "expires_at": session["expires_at"]}


@router.get("/auth/session")
def session(request: Request):
    info = admin_auth.validate(request.cookies.get(COOKIE_NAME))
    return ({"authenticated": True, "username": info["username"], "expires_at": info["expires_at"]}
            if info else {"authenticated": False})


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    admin_auth.logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return {"authenticated": False}


@router.get("/admin/access-control")
def access_control():
    return read_access_control()


@router.post("/admin/access-control/{kind}")
def update_access_control(kind: Literal["api_keys", "ip_allowlist"], body: AccessMutation):
    try:
        return mutate_access_control(kind, body.operation, body.version, body.id, body.values)
    except ConfigConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, Timeout) as exc:
        raise HTTPException(503, "暂时无法保存配置，请稍后重试并检查配置目录写入权限") from exc
    except ValidationError as exc:
        # Pydantic's default error includes input values, which can contain secrets.
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise HTTPException(422, f"访问控制配置校验失败：{fields}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
