"""Persistent changes must be atomic and versioned across simultaneous admins."""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import threading
import time

import pytest

from app.core import config
from app.core.access_control import mutate_access_control, read_access_control


def test_concurrent_writes_have_one_winner():
    version = read_access_control()['version']
    def write(index):
        try:
            mutate_access_control('ip_allowlist', 'create', version, None, {'name': str(index), 'network': f'192.0.2.{index}'})
            return 'success'
        except config.ConfigConflictError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1, 2]))
    assert sorted(results) == ['conflict', 'success']
    assert len(read_access_control()['ip_allowlist']) == 1


def test_failed_atomic_replace_preserves_disk_and_memory(monkeypatch, isolated_config):
    before = isolated_config.read_bytes()
    cached = config.get_config()
    def fail(*args):
        raise OSError('simulated full disk')
    monkeypatch.setattr(config.os, 'replace', fail)
    with pytest.raises(OSError, match='full disk'):
        config.update_config(lambda data: data['settings'].update(username='lost'))
    assert isolated_config.read_bytes() == before
    assert config.get_config() == cached
    assert not list(isolated_config.parent.glob('.config-*.tmp'))


def test_password_reset_process_cannot_be_overwritten_by_pending_api_write(isolated_config):
    started, release = threading.Event(), threading.Event()
    marker = isolated_config.parent / 'reset-started'
    script = (
        'from pathlib import Path; from app.core.config import update_config; '
        'import os; Path(os.environ["RESET_MARKER"]).touch(); '
        'update_config(lambda data: data["settings"].update(username="new-admin"))'
    )
    def pending_api_update():
        def mutate(data):
            started.set()
            assert release.wait(10)
            data.setdefault('access_control', {})['trusted_proxies'] = ['127.0.0.1']
        config.update_config(mutate)
    process = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(pending_api_update)
        try:
            assert started.wait(3)
            process = subprocess.Popen([sys.executable, '-c', script], env={**os.environ, 'RESET_MARKER': str(marker)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert marker.exists(), 'reset subprocess never started'
            # The child reached update_config but cannot commit until our writer releases.
            time.sleep(.2)
            assert process.poll() is None
        finally:
            release.set()
        pending.result(timeout=5)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr.decode(errors='replace')
    updated = config.get_config()
    assert updated['settings']['username'] == 'new-admin'
    assert updated['access_control']['trusted_proxies'] == ['127.0.0.1']
