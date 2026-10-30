"""Do not let Argon2 or config writers block the single-worker event loop."""
import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import AdminAuth, AuthMiddleware, LoginError, admin_auth
from app.core.config import update_config


@pytest.mark.asyncio
async def test_session_checks_and_event_loop_stay_live_during_login(monkeypatch):
    started, release = threading.Event(), threading.Event()

    class SlowHasher:
        def verify(self, hashed, password):
            started.set()
            assert release.wait(5), "test never released verification"
            return True

    monkeypatch.setattr(admin_auth, "_hasher", SlowHasher())
    application = FastAPI()
    application.add_middleware(AuthMiddleware)

    @application.get('/health')
    async def health():
        return {'status': 'ok'}

    login = asyncio.create_task(asyncio.to_thread(admin_auth.login, 'admin', 'TestAdmin123', '127.0.0.1'))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        # Session validation must not wait for password verification to finish.
        validated = threading.Event()
        thread = threading.Thread(target=lambda: (admin_auth.validate(None), validated.set()))
        thread.start()
        assert await asyncio.to_thread(validated.wait, 1)
        thread.join()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://testserver') as client:
            response = await asyncio.wait_for(client.get('/health'), timeout=1)
            assert response.status_code == 200
        with pytest.raises(LoginError) as error:
            admin_auth.login('admin', 'TestAdmin123', '192.0.2.1')
        assert error.value.status == 429
    finally:
        release.set()
        await login


def test_credentials_changed_during_hash_cannot_create_session(monkeypatch):
    manager = AdminAuth()

    class ChangingHasher:
        def verify(self, hashed, password):
            update_config(lambda data: data['settings'].update(username='replacement'))
            return True

    monkeypatch.setattr(manager, '_hasher', ChangingHasher())
    with pytest.raises(LoginError) as error:
        manager.login('admin', 'TestAdmin123', '127.0.0.1')
    assert error.value.status == 401
