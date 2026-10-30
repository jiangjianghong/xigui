"""Administrator password implementation; entry: scripts/set_admin_password.py."""

import argparse
import getpass
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher

from app.core.config import update_config
from app.schemas.security import PASSWORD_LENGTH_MESSAGE, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


def main():
    parser = argparse.ArgumentParser(description="初始化或重置管理员密码（不会修改业务配置）")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--generate-to", type=Path, help="生成随机密码并保存到新文件；文件须妥善保管，用后删除")
    args = parser.parse_args()
    if args.generate_to:
        password = secrets.token_urlsafe(PASSWORD_MAX_LENGTH)[:PASSWORD_MAX_LENGTH]
    else:
        password = getpass.getpass("管理员新密码（6–12 个字符）: ")
        if password != getpass.getpass("再次输入: "):
            parser.error("两次密码不一致")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        parser.error(PASSWORD_LENGTH_MESSAGE)
    secret_file = None
    if args.generate_to:
        # Never overwrite another credential file; do not put passwords in logs/argv.
        fd = os.open(args.generate_to, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        secret_file = args.generate_to
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"用户名: {args.username}\n密码: {password}\n请安全保存密码后删除此文件。\n")
    try:
        hashed = PasswordHasher().hash(password)
        def mutate(document):
            settings = document.setdefault("settings", {})
            settings.update(username=args.username, password_hash=hashed)
            settings.pop("password", None)
            settings.setdefault("session_minutes", 30)
            settings.setdefault("secure_cookie", False)
            document.setdefault("access_control", {"api_keys": [], "ip_allowlist": [], "trusted_proxies": []})
        update_config(mutate)
    except Exception:
        if secret_file:
            secret_file.unlink(missing_ok=True)
        raise
    print("管理员密码已更新，原会话将在后续请求时失效。")
    if secret_file:
        print(f"初始密码已保存到: {secret_file.resolve()}")


if __name__ == "__main__":
    main()
