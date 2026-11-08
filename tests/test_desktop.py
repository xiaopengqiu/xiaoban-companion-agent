import socket
import sys
from pathlib import Path

import httpx
import pytest

from desktop.runtime import LocalServer, Settings, SingleInstance, data_directory, resource_root, validate_url


def test_resources_resolve_source_and_frozen(monkeypatch, tmp_path):
    assert (resource_root() / 'web/index.html').is_file()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    assert resource_root() == tmp_path


def test_data_is_outside_bundled_resources(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert data_directory() == tmp_path / 'Xiaoban'
    assert data_directory().is_dir()


@pytest.mark.parametrize('url', ['http://public.example', 'file:///C:/secret', 'https://user:pass@example.com', 'https://example.com/#secret', 'http://127.0.0.1.evil:1234'])
def test_urls_reject_insecure_or_ambiguous_hosts(url):
    with pytest.raises(ValueError):
        validate_url(url, allow_loopback=True)


def test_https_and_explicit_loopback_rules():
    assert validate_url('https://companion.example/') == 'https://companion.example/'
    assert validate_url('http://127.0.0.1:43210', allow_loopback=True).startswith('http://127.0.0.1:')
    with pytest.raises(ValueError):
        validate_url('http://127.0.0.1:43210')


def test_settings_only_store_cloud_origin_not_credentials(tmp_path):
    settings = Settings(tmp_path)
    settings.set_public_url('https://companion.example')
    assert Settings(tmp_path).public_url == 'https://companion.example'
    with pytest.raises(ValueError):
        settings.set_public_url('http://public.example')
    assert 'token' not in (tmp_path / 'desktop-settings.json').read_text()


def test_local_service_uses_reserved_free_port_and_stops_without_orphans(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPANION_MODEL_API_KEY', 'must-not-be-inherited')
    monkeypatch.setenv('MEM0_ENABLED', 'true')
    service = LocalServer(tmp_path)
    url = service.start()
    assert url.startswith('http://127.0.0.1:')
    with httpx.Client(base_url=url, trust_env=False) as client:
        assert client.get('/api/health').json()['mode'] == 'local'
        assert client.get('/').status_code == 200
        auth = client.post('/api/demo', json={}).json()
        token = auth['token']
        headers = {'Authorization': 'Bearer ' + token}
        status = client.get('/api/integrations/status', headers=headers).json()
        assert status['model']['configured'] is False
        assert status['memory']['configured'] is False
        assert client.post('/api/integrations/open-wearables/connect', headers=headers, json={}).status_code == 409
        assert client.get('/desktop/settings?key=wrong').status_code == 403
        assert client.get('/desktop/settings?key=' + service.settings_key).status_code == 200
        target = '/desktop/settings?key=' + service.settings_key
        assert client.post(target, data={'url': 'https://evil.example'}, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert '在线账户只能使用 HTTPS' in client.post(target, data={'url': 'http://public.example'}).text
        page = client.post(target, data={'url': 'https://companion.example'})
        assert 'nonce-' in page.headers['content-security-policy']
        assert Settings(tmp_path).public_url == 'https://companion.example'
        assert client.delete('/api/me', headers=headers).status_code == 200
    port = service.port
    service.stop()
    assert not service.running
    with socket.socket() as connection:
        assert connection.connect_ex(('127.0.0.1', port)) != 0


def test_two_instances_choose_distinct_ports(tmp_path):
    first, second = LocalServer(tmp_path / 'one'), LocalServer(tmp_path / 'two')
    try:
        first.start()
        second.start()
        assert first.port != second.port
    finally:
        first.stop()
        second.stop()


def test_port_is_reused_to_preserve_local_login_origin_and_conflicts_fall_back(tmp_path):
    first = LocalServer(tmp_path)
    first.start()
    port = first.port
    first.stop()
    second = LocalServer(tmp_path)
    second.start()
    assert second.port == port
    second.stop()
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', port))
        occupied.listen()
        third = LocalServer(tmp_path)
        try:
            third.start()
            assert third.port != port
        finally:
            third.stop()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named mutex')
def test_single_instance_lock_is_scoped_to_data_directory_and_released(tmp_path):
    first, second = SingleInstance(tmp_path), SingleInstance(tmp_path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.close()
        assert second.acquire() is True
    finally:
        first.close()
        second.close()
