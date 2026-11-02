"""Explicit trust boundary for the IIS-hosted SOP deployment.

IIS must overwrite both proxy headers and authorize every POST by Windows group.
The shared key authenticates that proxy, not a browser or a Windows user by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
import binascii
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


class GatewayError(ValueError):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def canonical_origin(value):
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("Origin must be an explicit HTTP(S) origin without whitespace.")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or "\\" in value
            or "?" in value or "#" in value):
        raise ValueError("Origin must contain only an HTTP(S) scheme and host, with optional port.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Origin port is invalid.") from error
    host = parsed.hostname.lower()
    if not re.fullmatch(r"[a-z0-9.-]+|[a-f0-9:]+", host, re.I):
        raise ValueError("Origin hostname must be an ASCII DNS name or IP address.")
    if ":" in host:
        ipaddress.IPv6Address(host)
        host = f"[{host}]"
    if port == (443 if parsed.scheme == "https" else 80):
        port = None
    return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")


def single_header(headers, name, *, required=False):
    values = headers.get_all(name, [])
    if len(values) > 1 or (required and len(values) != 1):
        raise GatewayError("代理请求头缺失或重复。")
    value = values[0] if values else None
    if value is not None and (not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise GatewayError("代理请求头格式无效。")
    return value


@dataclass(frozen=True)
class GatewayConfig:
    base_path: str
    public_origin: str
    proxy_secret: str = field(repr=False)
    allow_writes: bool = False
    paths: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        allowed = {"base_path", "public_origin", "proxy_secret_env", "allow_writes",
                   "data_dir", "source_dir", "shared_config", "plan_file"}
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("Gateway configuration contains unsupported fields.")
        base = raw.get("base_path")
        if not isinstance(base, str) or not re.fullmatch(r"/[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*", base):
            raise ValueError("base_path must be an explicit subpath such as /sop, without trailing slash.")
        origin = canonical_origin(raw.get("public_origin"))
        env_name = raw.get("proxy_secret_env")
        if not isinstance(env_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
            raise ValueError("proxy_secret_env must name an environment variable.")
        secret = os.environ.get(env_name, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
            raise ValueError("Proxy secret environment variable must contain a 32-256 character URL-safe random key.")
        writes = raw.get("allow_writes", False)
        if not isinstance(writes, bool):
            raise ValueError("allow_writes must be a boolean.")
        paths = {}
        for name in ("data_dir", "source_dir", "shared_config", "plan_file"):
            if name in raw:
                value = raw[name]
                if not isinstance(value, str) or not Path(value).is_absolute():
                    raise ValueError(f"{name} must be an absolute local filesystem path.")
                paths[name] = Path(value)
        return cls(base, origin, secret, writes, paths)

    def configure_environment(self):
        for field_name, env_name in (("data_dir", "SOP_DATA_DIR"), ("source_dir", "SOP_SOURCE_DIR"),
                                     ("shared_config", "SOP_SHARED_CONFIG")):
            if field_name in self.paths:
                os.environ[env_name] = str(self.paths[field_name])

    def authorize(self, headers, peer, port, method):
        try:
            if not ipaddress.ip_address(peer).is_loopback:
                raise GatewayError("仅允许受信代理访问。")
        except ValueError:
            raise GatewayError("仅允许受信代理访问。")
        key = single_header(headers, "X-SOP-Proxy-Key", required=True)
        if not key.isascii() or not hmac.compare_digest(key, self.proxy_secret):
            raise GatewayError("代理身份验证失败。")
        user = single_header(headers, "X-SOP-User", required=True)
        if user.startswith("b64:"):
            try:
                user = base64.b64decode(user[4:], validate=True).decode("utf-8")
            except (binascii.Error, UnicodeError, ValueError):
                raise GatewayError("Windows 用户身份编码无效。")
        elif not user.isascii():
            raise GatewayError("Windows 用户身份编码无效。")
        if (not user or user != user.strip() or len(user) > 100 or "," in user
                or any(ord(c) < 32 or ord(c) == 127 for c in user)
                or not re.fullmatch(r"[^\\@]+\\[^\\@]+|[^\\@]+@[^\\@]+", user)):
            raise GatewayError("Windows 用户身份无效。")
        host = single_header(headers, "Host", required=True).lower()
        public_host = urlsplit(self.public_origin).netloc
        allowed_hosts = {public_host, f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost", "[::1]"})
        if host not in allowed_hosts:
            raise GatewayError("代理 Host 不匹配。")
        origin = single_header(headers, "Origin", required=method == "POST")
        if origin is not None:
            try:
                permitted = canonical_origin(origin) == self.public_origin
            except ValueError:
                permitted = False
            if not permitted:
                raise GatewayError("请求来源不允许。")
        if headers.get_all("Transfer-Encoding", []) or len(headers.get_all("Content-Length", [])) > 1:
            raise GatewayError("请求长度或传输格式无效。", 400)
        return user

    def route(self, target):
        if not isinstance(target, str) or not target.startswith("/") or target.startswith("//"):
            raise GatewayError("应用路径无效。", 400)
        raw = target.split("?", 1)[0]
        if re.search(r"%(?:2f|5c)", raw, re.I):
            raise GatewayError("应用路径包含编码分隔符。", 400)
        try:
            path = unquote(raw, errors="strict")
        except (UnicodeError, ValueError):
            raise GatewayError("应用路径编码无效。", 400)
        if (any(ord(char) < 32 or ord(char) == 127 for char in path) or any(c in path for c in "\\%:#")
                or "//" in path or any(part in {".", ".."} for part in path.split("/"))):
            raise GatewayError("应用路径无效。", 400)
        if path == self.base_path:
            return None
        if not path.startswith(self.base_path + "/"):
            raise GatewayError("应用路径不存在。", 404)
        return path[len(self.base_path):]


def attributed_body(body, user):
    """Replace every client-supplied owner, while preserving the business values."""
    if isinstance(body, list):
        return [attributed_body(item, user) for item in body]
    if not isinstance(body, dict):
        return body
    return {key: user if key == "owner" else attributed_body(value, user) for key, value in body.items()}
