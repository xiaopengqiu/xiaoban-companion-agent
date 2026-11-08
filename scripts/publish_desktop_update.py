"""Operator-only signed Windows release publication. Credentials never enter CLI args."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
MAX_SIZE = 64 * 1024 * 1024
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


class PublishError(Exception):
    pass


def publish(executable: Path, config: dict, token: str, version: str, notes: str,
            *, transport=None) -> dict:
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PublishError("Invalid version; use three integers such as 0.4.0")
    if not token or any(c.isspace() for c in token):
        raise PublishError("Missing or invalid publisher token")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise PublishError("Release notes must be at most 2000 characters")
    parts = urlsplit(config["feed_url"])
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path != "/api/desktop-updates/latest"):
        raise PublishError("Invalid HTTPS update feed")
    executable = Path(executable)
    if executable.is_symlink() or not executable.is_file():
        raise PublishError("Executable must be a regular file")
    digest = hashlib.sha256()
    with executable.open("rb") as source:
        before = os.fstat(source.fileno())
        if not 1 <= before.st_size <= MAX_SIZE:
            raise PublishError("Executable must be between 1 byte and 64 MiB")
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PublishError("Executable changed while hashing")
        checksum = digest.hexdigest()
        source.seek(0)
        meta = {"sha256": checksum, "size": before.st_size, "notes": notes,
                "platform": "windows-x64"}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(before.st_size),
            "X-Update-Metadata": base64.b64encode(json.dumps(
                meta, ensure_ascii=False, separators=(",", ":")).encode()).decode(),
        }
        url = f"https://{parts.netloc}/api/internal/desktop-updates/{version}"
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False,
                          transport=transport) as client:
            try:
                reply = client.put(url, headers=headers,
                                   content=iter(lambda: source.read(256 * 1024), b""))
            except httpx.HTTPError as exc:
                raise PublishError(f"Upload failed ({type(exc).__name__}); safe to retry") from exc
    if reply.status_code not in (200, 201):
        raise PublishError(f"Upload rejected: HTTP {reply.status_code}")
    if len(reply.content) > 16384:
        raise PublishError("Oversized publish response")
    try:
        envelope = reply.json()
        payload = envelope["payload"]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode()
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(
            config["public_key"], validate=True))
        public_key.verify(base64.b64decode(envelope["signature"], validate=True), canonical)
    except Exception as exc:
        raise PublishError("Invalid response signature; inspect server before distribution") from exc
    expected_url = f"https://{parts.netloc}/api/desktop-updates/assets/{version}/{checksum}.exe"
    expected = {"schema": 1, "version": version, "platform": "windows-x64",
                "sha256": checksum, "size": before.st_size, "url": expected_url, "notes": notes}
    if any(payload.get(k) != value for k, value in expected.items()):
        raise PublishError("Signed response does not match uploaded executable")
    return envelope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "desktop/update-config.json")
    parser.add_argument("--token-file", type=Path,
                        default=ROOT / ".credentials/desktop-update-publish-token")
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("DESKTOP_UPDATE_PUBLISH_TOKEN")
        if token is None:
            token = args.token_file.read_text(encoding="ascii").strip()
        result = publish(args.exe, json.loads(args.config.read_text(encoding="utf-8")),
                         token, args.version, args.notes_file.read_text(encoding="utf-8"))
    except (PublishError, OSError, ValueError, KeyError) as exc:
        print(f"Publication failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
