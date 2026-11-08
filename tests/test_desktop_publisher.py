import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.publish_desktop_update import PublishError, publish


def fixture(tmp_path):
    key = Ed25519PrivateKey.generate()
    config = {
        "schema": 1, "version": "0.4.0",
        "feed_url": "https://example.test/api/desktop-updates/latest",
        "public_key": base64.b64encode(key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode(),
    }
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"MZ" + b"test executable\x00" * 90000)
    return key, config, exe


def response(key, config, body, *, tamper=False):
    digest = hashlib.sha256(body).hexdigest()
    payload = {
        "schema": 1, "version": "0.4.0", "platform": "windows-x64",
        "url": f"https://example.test/api/desktop-updates/assets/0.4.0/{digest}.exe",
        "sha256": digest, "size": len(body),
        "published_at": "2026-11-08T00:00:00.000Z", "notes": "自动更新测试",
    }
    signature = base64.b64encode(key.sign(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())).decode()
    if tamper:
        payload["version"] = "9.9.9"
    return {"payload": payload, "signature": signature}


def test_publishes_streamed_bytes_with_operator_token_and_verifies_reply(tmp_path):
    key, config, exe = fixture(tmp_path)
    seen = []

    def handle(request):
        assert request.url == "https://example.test/api/internal/desktop-updates/0.4.0"
        assert request.headers["authorization"] == "Bearer private-operator-token"
        assert request.headers["content-length"] == str(exe.stat().st_size)
        meta = json.loads(base64.b64decode(request.headers["x-update-metadata"]))
        body = request.read()
        assert meta == {"sha256": hashlib.sha256(body).hexdigest(),
                        "size": len(body), "notes": "自动更新测试", "platform": "windows-x64"}
        seen.append(body)
        return httpx.Response(201, json=response(key, config, body))

    result = publish(exe, config, "private-operator-token", "0.4.0", "自动更新测试",
                     transport=httpx.MockTransport(handle))
    assert seen == [exe.read_bytes()]
    assert result["payload"]["version"] == "0.4.0"
    assert "private-operator-token" not in json.dumps(result)


def test_rejects_unsigned_or_tampered_success_reply(tmp_path):
    key, config, exe = fixture(tmp_path)
    transport = httpx.MockTransport(lambda r: httpx.Response(
        201, json=response(key, config, r.read(), tamper=True)))
    with pytest.raises(PublishError, match="signature"):
        publish(exe, config, "token", "0.4.0", "自动更新测试", transport=transport)


@pytest.mark.parametrize("version", ["v0.4.0", "0.04.0", "0.4", "1.0.0/other"])
def test_invalid_version_never_uploads(tmp_path, version):
    _, config, exe = fixture(tmp_path)
    def handle(request):
        pytest.fail("Invalid input reached network")
    with pytest.raises(PublishError, match="version"):
        publish(exe, config, "token", version, "notes", transport=httpx.MockTransport(handle))


def test_redirect_does_not_forward_publisher_credential(tmp_path):
    _, config, exe = fixture(tmp_path)
    seen = []
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://other.test/steal"})
    with pytest.raises(PublishError, match="307"):
        publish(exe, config, "token", "0.4.0", "notes", transport=httpx.MockTransport(handle))
    assert len(seen) == 1
