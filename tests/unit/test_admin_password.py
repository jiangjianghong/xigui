"""Password-reset command validates boundaries and preserves unrelated settings."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from app.core import admin_password
from app.core.config import config_snapshot, get_config
from app.schemas.security import AdminSettings

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("password", ["Abc123", "Abc123456789"])
def test_reset_accepts_boundaries_and_invalidates_old_session(client, monkeypatch, password):
    before = get_config()["llm"].copy()
    monkeypatch.setattr(sys, "argv", ["scripts/set_admin_password.py"])
    monkeypatch.setattr(admin_password.getpass, "getpass", lambda _: password)
    admin_password.main()
    stored = get_config()
    assert PasswordHasher().verify(stored["settings"]["password_hash"], password)
    assert "password" not in stored["settings"]
    assert stored["llm"] == before
    assert client.get("/admin/rules").status_code == 401
    assert client.post("/auth/login", json={"username": "admin", "password": password}).status_code == 200


@pytest.mark.parametrize("password", ["12345", "1234567890123"])
def test_reset_rejects_invalid_length_without_writing(isolated_config, monkeypatch, password):
    before = isolated_config.read_bytes()
    monkeypatch.setattr(sys, "argv", ["scripts/set_admin_password.py"])
    monkeypatch.setattr(admin_password.getpass, "getpass", lambda _: password)
    with pytest.raises(SystemExit) as error:
        admin_password.main()
    assert error.value.code == 2
    assert isolated_config.read_bytes() == before


def test_reset_rejects_confirmation_mismatch(isolated_config, monkeypatch):
    before = isolated_config.read_bytes()
    responses = iter(["First123", "Other123"])
    monkeypatch.setattr(sys, "argv", ["scripts/set_admin_password.py"])
    monkeypatch.setattr(admin_password.getpass, "getpass", lambda _: next(responses))
    with pytest.raises(SystemExit):
        admin_password.main()
    assert isolated_config.read_bytes() == before


def test_script_entry_generates_twelve_character_password(isolated_config, tmp_path):
    output = tmp_path / "initial-password.txt"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "set_admin_password.py"), "--generate-to", str(output)],
        cwd=tmp_path, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True, encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    password = output.read_text(encoding="utf-8").splitlines()[1].split(": ", 1)[1]
    assert len(password) == 12
    assert password not in result.stdout + result.stderr
    stored, _ = config_snapshot()
    assert PasswordHasher().verify(stored["settings"]["password_hash"], password)
    before = isolated_config.read_bytes()
    retry = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "set_admin_password.py"), "--generate-to", str(output)],
        cwd=tmp_path, capture_output=True, timeout=15,
    )
    assert retry.returncode != 0
    assert isolated_config.read_bytes() == before


@pytest.mark.parametrize("password,valid", [("12345", False), ("123456", True), ("123456789012", True), ("1234567890123", False)])
def test_plaintext_config_uses_same_password_policy(password, valid):
    if valid:
        assert AdminSettings(password=password).password == password
    else:
        with pytest.raises(ValueError):
            AdminSettings(password=password)
